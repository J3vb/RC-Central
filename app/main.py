"""RC Central - install and launch RC drift setup tools."""

import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from app import __version__, catalog, installer, launcher, updater


class _InstallSignals(QObject):
    """Bridge from the download thread back to the Qt main thread."""

    progress = Signal(int, int)
    done = Signal()
    error = Signal(str)


class MainWindow(QMainWindow):
    COLS = ("Tool", "Vendor", "Version", "Status", "")

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"RC Central v{__version__}")
        self.resize(760, 400)
        self.tools = catalog.load_catalog()

        self.table = QTableWidget(len(self.tools), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setCentralWidget(self.table)

        # ponytail: one shared progress bar; per-row bars if parallel installs matter
        self.progress = QProgressBar()
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        for row, tool in enumerate(self.tools):
            name = QTableWidgetItem(tool["name"])
            name.setToolTip(tool.get("description", ""))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(tool["vendor"]))
            self.table.setItem(row, 2, QTableWidgetItem(tool["version"]))
            button = QToolButton()
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.clicked.connect(lambda _=False, r=row: self._on_action(r))
            menu = QMenu(button)
            menu.addAction(
                "Locate existing install…",
                lambda _=False, r=row: self._locate_existing(r),
            )
            button.setMenu(menu)
            self.table.setCellWidget(row, 4, button)
            self._refresh_row(row)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _refresh_row(self, row: int) -> None:
        tool = self.tools[row]
        state = installer.get_state(tool["id"])
        if state is None:
            status, action = "Not installed", "Install"
        elif state["version"] != tool["version"]:
            status, action = f"Installed v{state['version']}", "Update"
        else:
            status, action = f"Installed v{state['version']}", "Launch"
        self.table.setItem(row, 3, QTableWidgetItem(status))
        self.table.cellWidget(row, 4).setText(action)

    def _on_action(self, row: int) -> None:
        tool = self.tools[row]
        state = installer.get_state(tool["id"])
        if state and state["version"] == tool["version"]:
            try:
                launcher.launch(
                    tool["id"],
                    state["exe_path"],
                    tool.get("install", {}).get("needs_admin", False),
                )
            except OSError as e:  # e.g. UAC prompt declined
                QMessageBox.warning(self, "Launch failed", str(e))
                return
            self.statusBar().showMessage(f"Launched {tool['name']}", 5000)
        else:
            self._install(row, tool)

    def _locate_existing(self, row: int) -> None:
        tool = self.tools[row]
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Locate {tool['name']} executable",
            "",
            "Programs (*.exe);;All files (*)",
        )
        if not path:
            return
        version, ok = QInputDialog.getText(
            self,
            "Installed version",
            f"Which version of {tool['name']} is this?",
            text=tool["version"],
        )
        if not ok:
            return
        try:
            installer.register_existing(tool, path, version.strip() or tool["version"])
        except Exception as e:  # bad path etc. must reach the user, not a traceback
            QMessageBox.warning(self, "Couldn't add existing install", str(e))
            return
        self._refresh_row(row)
        self.statusBar().showMessage(f"Linked existing {tool['name']}", 5000)

    def _install(self, row: int, tool: dict) -> None:
        self.table.cellWidget(row, 4).setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.statusBar().showMessage(f"Downloading {tool['name']}...")

        signals = _InstallSignals(self)  # parented so it outlives this scope
        signals.progress.connect(self._on_progress)
        signals.done.connect(lambda r=row: self._install_finished(r, None))
        signals.error.connect(lambda msg, r=row: self._install_finished(r, msg))

        def work():
            try:
                installer.install(tool, progress=signals.progress.emit)
                signals.done.emit()
            except Exception as e:  # anything here must reach the user, not a traceback
                signals.error.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)  # 0 total -> busy indicator
        self.progress.setValue(done if total else 0)

    def _install_finished(self, row: int, error: str | None) -> None:
        self.progress.hide()
        self.table.cellWidget(row, 4).setEnabled(True)
        self._refresh_row(row)
        if error:
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "Install failed", error)
        else:
            self.statusBar().showMessage(f"Installed {self.tools[row]['name']}", 5000)


def main() -> None:
    app = QApplication(sys.argv)
    updater.cleanup()
    win = MainWindow()
    win.show()
    threading.Thread(target=updater.fetch_update, daemon=True).start()
    code = app.exec()
    updater.apply_pending()  # swap in a downloaded hub update on the way out
    sys.exit(code)


if __name__ == "__main__":
    main()
