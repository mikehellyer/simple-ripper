from conftest import IN_RAINBOWS_DISC_ID, IN_RAINBOWS_TOC

from simple_ripper.core.toc import SESSION_GAP_SECTORS, Toc, TocTrack


def test_disc_id_matches_musicbrainz(in_rainbows):
    assert in_rainbows.musicbrainz_disc_id == IN_RAINBOWS_DISC_ID


def test_toc_string_round_trips(in_rainbows):
    assert in_rainbows.musicbrainz_toc == IN_RAINBOWS_TOC


def test_track_ranges_run_up_to_the_next_track(in_rainbows):
    assert in_rainbows.track_range(1) == (0, 18009 - 150)
    assert in_rainbows.track_range(10) == (171136 - 150, 192271 - 150)
    assert round(in_rainbows.track_seconds(1)) == 238  # 15 Step is 3:58


def test_enhanced_cd_leaves_out_the_data_track():
    # Two audio tracks, then a data track in a second session.
    toc = Toc(
        tracks=(TocTrack(1, 0), TocTrack(2, 20000), TocTrack(3, 60000, is_audio=False)),
        leadout=90000,
    )
    assert [t.number for t in toc.audio_tracks] == [1, 2]
    assert toc.track_range(2) == (20000, 60000 - SESSION_GAP_SECTORS)
    first, last, leadout, *_ = toc.musicbrainz_toc.split()
    assert (first, last, leadout) == ("1", "2", str(60000 - SESSION_GAP_SECTORS + 150))
