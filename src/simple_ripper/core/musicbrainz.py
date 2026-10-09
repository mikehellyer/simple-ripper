"""Talking to MusicBrainz and the Cover Art Archive.

MusicBrainz asks every client for a descriptive User-Agent and no more
than one request a second, so every call here goes through _get(), which
enforces both. Unlike the update check, these lookups are things the user
asked for, so failures raise MusicBrainzError with a message worth showing
rather than being swallowed.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from simple_ripper import __version__
from simple_ripper.core.http import SSL_CONTEXT
from simple_ripper.core.toc import Toc

API_ROOT = "https://musicbrainz.org/ws/2"
COVER_ART_ROOT = "https://coverartarchive.org"
USER_AGENT = f"Simple-Ripper/{__version__} ( https://github.com/mikehellyer/simple-ripper )"
TIMEOUT_SECONDS = 15
MIN_INTERVAL_SECONDS = 1.05

RELEASE_INCLUDES = "artist-credits+recordings+release-groups+labels+isrcs+genres"

_rate_lock = threading.Lock()
_last_request = 0.0


class MusicBrainzError(Exception):
    pass


def _get(url: str, *, raw: bool = False, allow_404: bool = False):
    global _last_request
    with _rate_lock:
        wait = _last_request + MIN_INTERVAL_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()

    headers = {"User-Agent": USER_AGENT}
    if not raw:
        headers["Accept"] = "application/json"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=SSL_CONTEXT) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404 and allow_404:
            return None
        if error.code == 503:
            raise MusicBrainzError("MusicBrainz is busy right now — try again in a moment") from error
        raise MusicBrainzError(f"MusicBrainz returned an error ({error.code})") from error
    except (OSError, ValueError) as error:
        raise MusicBrainzError(f"Couldn't reach MusicBrainz: {error}") from error
    if raw:
        return body
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MusicBrainzError("MusicBrainz sent back something unreadable") from error


def lookup_disc(toc: Toc) -> list[dict]:
    """Releases matching this disc: exact disc-ID matches if there are any,
    otherwise MusicBrainz's fuzzy match on the track layout. Each is a
    release as the API returns it, enough to choose between; fetch the
    chosen one with get_release() for its full details."""
    query = urllib.parse.urlencode(
        {"toc": toc.musicbrainz_toc, "inc": RELEASE_INCLUDES, "cdstubs": "no", "fmt": "json"},
        safe="+",
    )
    data = _get(f"{API_ROOT}/discid/{toc.musicbrainz_disc_id}?{query}", allow_404=True)
    if not data:
        return []
    return [r for r in data.get("releases", []) if isinstance(r, dict) and r.get("id")]


def _lucene_escape(text: str) -> str:
    special = '+-&|!(){}[]^"~*?:\\/'
    return "".join("\\" + ch if ch in special else ch for ch in text)


def search_releases(artist: str = "", album: str = "", track_count: Optional[int] = None, limit: int = 25) -> list[dict]:
    """Free-text release search, for files with no disc to look up."""
    terms = []
    if album.strip():
        terms.append(f'release:"{_lucene_escape(album.strip())}"')
    if artist.strip():
        terms.append(f'artist:"{_lucene_escape(artist.strip())}"')
    if not terms:
        return []
    params = urllib.parse.urlencode({"query": " AND ".join(terms), "limit": limit, "fmt": "json"})
    data = _get(f"{API_ROOT}/release?{params}")
    releases = [r for r in data.get("releases", []) if isinstance(r, dict) and r.get("id")]
    if track_count:
        # Releases with exactly as many tracks as the files first; the
        # rest stay in case the files are an incomplete album. sorted() is
        # stable, so MusicBrainz's relevance order holds within each group.
        releases.sort(key=lambda r: r.get("track-count") != track_count)
    return releases


def get_release(release_id: str) -> dict:
    """Everything about one release, including which disc IDs belong to
    which medium."""
    data = _get(f"{API_ROOT}/release/{release_id}?inc={RELEASE_INCLUDES}+discids&fmt=json", allow_404=True)
    if not data:
        raise MusicBrainzError("That release is no longer on MusicBrainz")
    return data


def fetch_cover_art(release_id: str, release_group_id: str = "") -> Optional[bytes]:
    """The front cover (500px) from the Cover Art Archive, falling back to
    the release group's cover. None when there isn't one or it can't be
    fetched — artwork is a nicety, never worth failing a rip over."""
    candidates = [f"{COVER_ART_ROOT}/release/{release_id}/front-500"]
    if release_group_id:
        candidates.append(f"{COVER_ART_ROOT}/release-group/{release_group_id}/front-500")
    for url in candidates:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=SSL_CONTEXT) as response:
                data = response.read()
        except (OSError, ValueError):
            continue
        if data:
            return data
    return None


def submission_url(toc: Toc) -> str:
    """The page for adding this disc to MusicBrainz (or attaching it to a
    release that's already there) — what Picard opens for an unknown CD."""
    params = urllib.parse.urlencode(
        {"id": toc.musicbrainz_disc_id, "tracks": len(toc.audio_tracks), "toc": toc.musicbrainz_toc}
    )
    return f"https://musicbrainz.org/cdtoc/attach?{params}"
