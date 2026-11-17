"""Ponto de entrada da interface."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .core.tools import ASSETS_DIR, ROOT_DIR


def main() -> None:
    QApplication.setOrganizationName("EasyCUT")
    QApplication.setApplicationName("EasyCUT")
    QApplication.setApplicationVersion(__version__)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    for icon in (ROOT_DIR / "EasyCUT.ico", ASSETS_DIR / "logo.png"):
        if icon.is_file():
            app.setWindowIcon(QIcon(str(icon)))
            break

    from .ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    if len(sys.argv) > 1 and sys.argv[1].endswith((".easycut", ".json")):
        win.open_project(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
