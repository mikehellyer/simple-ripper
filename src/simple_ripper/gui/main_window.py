from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMessageBox, QStatusBar, QTabWidget, QVBoxLayout, QWidget

from simple_ripper import __version__
from simple_ripper.core.diagnostics import log_dir
from simple_ripper.core.self_update import download_asset, find_asset_for_this_platform, launch_installer
from simple_ripper.core.settings import SettingsStore
from simple_ripper.core.updater import API_TIMEOUT_SECONDS, check_for_update
from simple_ripper.gui.rip_page import RipPage
from simple_ripper.gui.settings_dialog import SettingsDialog
from simple_ripper.gui.tagger_page import TaggerPage
from simple_ripper.gui.update_banner import UpdateBanner
from simple_ripper.gui.workers import BackgroundRunner

UPDATE_OWNER = "mikehellyer"
UPDATE_REPO = "simple-ripper"
BACKGROUND_JOIN_TIMEOUT_MS = (API_TIMEOUT_SECONDS + 1) * 1000
ICON_PATH = Path(__file__).parent / "resources" / "icon.png"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Simple-Ripper v{__version__}")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1180, 760)

        self._settings = SettingsStore()
        self._runner = BackgroundRunner(self)
        self._pending_update = None

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._update_banner = UpdateBanner()
        self._update_banner.update_clicked.connect(self._start_update)
        layout.addWidget(self._update_banner)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._rip_page = RipPage(self._settings, self._runner)
        self._tagger_page = TaggerPage(self._settings, self._runner)
        self._tabs.addTab(self._rip_page, "Rip CD")
        self._tabs.addTab(self._tagger_page, "Tagger")
        layout.addWidget(self._tabs, 1)
        self.setCentralWidget(central)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        for page in (self._rip_page, self._tagger_page):
            page.status_message.connect(lambda text: self._status.showMessage(text, 8000))

        self._build_menus()
        self._check_for_updates()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        for text, shortcut, slot in (
            ("Read Disc", "Ctrl+R", self._rip_page.read_disc),
            ("Open Music Folder", None, self._open_music_folder),
            (None, None, None),
            ("Settings…", QKeySequence.Preferences, self._open_settings),
            (None, None, None),
            ("Quit", QKeySequence.Quit, self.close),
        ):
            if text is None:
                file_menu.addSeparator()
                continue
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False, slot=slot: slot())
            file_menu.addAction(action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Open Log Folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        help_menu.addAction("About Simple-Ripper", self._about)

    def _open_settings(self) -> None:
        if SettingsDialog(self._settings, self).exec():
            self._rip_page.settings_changed()

    def _open_music_folder(self) -> None:
        folder = Path(self._settings.settings.output_folder).expanduser()
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About Simple-Ripper",
            f"<b>Simple-Ripper</b> v{__version__}<br><br>"
            "Rips audio CDs and tags them with data from "
            '<a href="https://musicbrainz.org">MusicBrainz</a>, the way Picard does. '
            'Cover art from the <a href="https://coverartarchive.org">Cover Art Archive</a>.<br><br>'
            "By Mike Hellyer, built with Claude Code.",
        )

    # --- updates --------------------------------------------------------

    def _check_for_updates(self) -> None:
        self._runner.run(
            lambda: check_for_update(__version__, UPDATE_OWNER, UPDATE_REPO),
            self._on_update_checked,
            lambda error: None,
        )

    def _on_update_checked(self, info) -> None:
        if info:
            self._pending_update = info
            self._update_banner.announce(info.version)

    def _start_update(self) -> None:
        if self._pending_update is None:
            return
        if self._rip_page.is_ripping:
            self._update_banner.set_status("Finish or cancel the rip first, then click Update")
            return
        asset_url = find_asset_for_this_platform(self._pending_update.assets)
        if asset_url is None:
            self._update_banner.set_status(f"No installer for this platform — see {self._pending_update.url}")
            return
        self._update_banner.set_busy(True, "Downloading…")
        self._runner.run(lambda: download_asset(asset_url), self._on_update_downloaded, lambda error: self._on_update_downloaded(None))

    def _on_update_downloaded(self, path) -> None:
        if path is None:
            self._update_banner.set_status("Couldn't download the update — try again")
            self._update_banner.set_busy(False)
            return
        try:
            process = launch_installer(path)
        except OSError:
            self._update_banner.set_status(f"Downloaded to {path} — open it manually")
            self._update_banner.set_busy(False)
            return

        self._update_banner.set_status("Installer launched — closing to finish…")
        if sys.platform not in ("win32", "darwin") and process is not None:
            # Linux: wait for pkexec/apt to finish before quitting — quitting
            # immediately can kill its password prompt before it's answered.
            self._runner.run(lambda: (path, self._wait_for_installer(process)), self._on_installer_finished)
        else:
            QTimer.singleShot(1500, self.close)

    @staticmethod
    def _wait_for_installer(process) -> tuple[int, str]:
        returncode = process.wait()
        stderr_text = process.stderr.read().strip() if process.stderr is not None else ""
        return returncode, stderr_text

    def _on_installer_finished(self, result) -> None:
        path, (returncode, stderr_text) = result
        if returncode == 0:
            self.close()
            return
        detail = f": {stderr_text}" if stderr_text else f" (exit code {returncode})"
        self._update_banner.set_status(f"Update didn't install{detail} — installer is at {path}")
        self._update_banner.set_busy(False)

    def closeEvent(self, event) -> None:
        if self._rip_page.is_ripping:
            answer = QMessageBox.question(self, "Rip in Progress", "A rip is still running. Stop it and quit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self._rip_page.shutdown()
        self._runner.shutdown(BACKGROUND_JOIN_TIMEOUT_MS)
        super().closeEvent(event)
