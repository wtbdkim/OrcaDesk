"""
ORCAdesk - entry point.

Run in development:   python main.py
Frozen build:         ORCAdesk.exe  (see build.spec)
"""

import sys

from PyQt6.QtWidgets import QApplication

from orcamgr.gui.window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ORCAdesk")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
