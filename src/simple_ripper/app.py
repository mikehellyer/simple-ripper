import sys

from PySide6.QtWidgets import QApplication

from simple_ripper import __version__
from simple_ripper.core.diagnostics import enable_app_log, enable_crash_log, log_event
from simple_ripper.gui.main_window import MainWindow


def main() -> int:
    enable_app_log(__version__)
    enable_crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName("Simple-Ripper")
    window = MainWindow()
    window.show()
    code = app.exec()
    log_event(f"event loop finished (code {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
