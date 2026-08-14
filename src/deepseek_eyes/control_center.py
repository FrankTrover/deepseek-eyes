"""Control Center — PySide6 desktop app with five tabs.

Tabs: Status, Integration, Permissions, Token Plan/Usage, Security/Diagnostics.

The window is deliberately thin: every action delegates to the same modules the
CLI and MCP server use (``config``, ``credentials``, ``integration``,
``diagnostics``), so the GUI can never drift from the tested core.
"""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import EyesConfig, load_config, restore_backup, save_config
from .credentials import delete_credential, save_credential
from .diagnostics import assert_no_secrets, redacted_report, to_json
from .integration import apply_integration, integration_state, remove_integration


def run() -> int:
    app = QApplication([])
    win = ControlCenter()
    win.show()
    return app.exec()


class ControlCenter(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DeepSeek Eyes — Control Center")
        self.resize(760, 520)
        tabs = QTabWidget()
        tabs.addTab(self._status_tab(), "Status")
        tabs.addTab(self._integration_tab(), "Integration")
        tabs.addTab(self._permissions_tab(), "Permissions")
        tabs.addTab(self._usage_tab(), "Token Plan/Usage")
        tabs.addTab(self._security_tab(), "Security/Diagnostics")
        self.setCentralWidget(tabs)
        self._refresh_all()

    # -- helpers -------------------------------------------------------------

    def _refresh_all(self) -> None:
        self._refresh_status()
        self._refresh_integration()
        self._refresh_permissions()
        self._refresh_usage()
        self._refresh_security()

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    # -- Status --------------------------------------------------------------

    def _status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._status_text = QPlainTextEdit()
        self._status_text.setReadOnly(True)
        layout.addWidget(self._status_text)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_status)
        layout.addWidget(refresh)
        return w

    def _refresh_status(self) -> None:
        try:
            cfg = load_config()
        except Exception as exc:
            self._status_text.setPlainText(f"Config error: {exc}")
            return
        lines = [
            "DeepSeek Eyes Control Center",
            "",
            "Capture scopes:",
            f"  region      {'enabled' if cfg.region_capture_allowed else 'disabled'}",
            f"  window      {'enabled' if cfg.window_capture_allowed else 'disabled'}",
            f"  fullscreen  {'enabled' if cfg.fullscreen_capture_allowed else 'disabled'}",
            "",
            "Backend capabilities: see Security/Diagnostics.",
        ]
        self._status_text.setPlainText("\n".join(lines))

    # -- Integration ---------------------------------------------------------

    def _integration_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(self._label("OpenCode host config (opencode.json)"))
        self._integration_text = QPlainTextEdit()
        self._integration_text.setReadOnly(True)
        layout.addWidget(self._integration_text)
        row = QWidget()
        row_layout = QVBoxLayout(row)
        apply_btn = QPushButton("Apply integration")
        apply_btn.clicked.connect(self._on_apply)
        remove_btn = QPushButton("Remove integration")
        remove_btn.clicked.connect(self._on_remove)
        row_layout.addWidget(apply_btn)
        row_layout.addWidget(remove_btn)
        layout.addWidget(row)
        return w

    def _refresh_integration(self) -> None:
        state = integration_state()
        status = "configured" if state.configured else "not configured"
        self._integration_text.setPlainText(
            f"{state.path}\nstatus: {status}\n\nentry:\n{state.entry or '—'}"
        )

    def _on_apply(self) -> None:
        try:
            state = apply_integration()
        except Exception as exc:
            QMessageBox.critical(self, "Integration", str(exc))
            return
        QMessageBox.information(
            self, "Integration", f"Written to {state.path}\nBackup saved as *.eyes-backup"
        )
        self._refresh_integration()

    def _on_remove(self) -> None:
        state = remove_integration()
        QMessageBox.information(self, "Integration", f"Removed from {state.path}")
        self._refresh_integration()

    # -- Permissions ---------------------------------------------------------

    def _permissions_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._cb_region = QCheckBox("Allow region capture")
        self._cb_window = QCheckBox("Allow window capture")
        self._cb_fullscreen = QCheckBox("Allow full-screen capture (per-call confirmation)")
        for cb in (self._cb_region, self._cb_window, self._cb_fullscreen):
            cb.toggled.connect(self._on_permission_change)
            layout.addWidget(cb)
        self._perm_saved = QLabel("")
        layout.addWidget(self._perm_saved)
        layout.addStretch()
        return w

    def _refresh_permissions(self) -> None:
        cfg = load_config()
        self._cb_region.setChecked(cfg.region_capture_allowed)
        self._cb_window.setChecked(cfg.window_capture_allowed)
        self._cb_fullscreen.setChecked(cfg.fullscreen_capture_allowed)

    def _on_permission_change(self) -> None:
        save_config(
            EyesConfig(
                region_capture_allowed=self._cb_region.isChecked(),
                window_capture_allowed=self._cb_window.isChecked(),
                fullscreen_capture_allowed=self._cb_fullscreen.isChecked(),
            )
        )
        self._perm_saved.setText("Saved")

    # -- Token Plan/Usage ----------------------------------------------------

    def _usage_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(self._label("Token Plan credential (stored in the OS keyring)"))
        form = QFormLayout()
        self._url_edit = QLineEdit()
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Base URL", self._url_edit)
        form.addRow("Token (tp-…)", self._token_edit)
        layout.addLayout(form)
        save_btn = QPushButton("Save credential")
        save_btn.clicked.connect(self._on_save_credential)
        delete_btn = QPushButton("Delete credential")
        delete_btn.clicked.connect(self._on_delete_credential)
        layout.addWidget(save_btn)
        layout.addWidget(delete_btn)
        self._usage_text = QLabel("")
        layout.addWidget(self._usage_text)
        layout.addStretch()
        return w

    def _refresh_usage(self) -> None:
        from .credentials import has_credential, load_credential

        if has_credential():
            cred = load_credential()
            if cred is None:
                return
            self._url_edit.setText(cred.base_url)
            self._token_edit.setText("")
            self._usage_text.setText("Credential present (token hidden)")
        else:
            self._usage_text.setText("No credential configured")

    def _on_save_credential(self) -> None:
        url = self._url_edit.text().strip()
        token = self._token_edit.text().strip()
        if not url or not token:
            QMessageBox.warning(self, "Token Plan", "Base URL and token are required")
            return
        try:
            save_credential(url, token)
        except Exception as exc:
            QMessageBox.critical(self, "Token Plan", str(exc))
            return
        self._refresh_usage()

    def _on_delete_credential(self) -> None:
        delete_credential()
        self._refresh_usage()

    # -- Security/Diagnostics ------------------------------------------------

    def _security_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._diag_text = QPlainTextEdit()
        self._diag_text.setReadOnly(True)
        layout.addWidget(self._diag_text)
        row = QWidget()
        row_layout = QVBoxLayout(row)
        export_btn = QPushButton("Export redacted diagnostics")
        export_btn.clicked.connect(self._on_export)
        restore_btn = QPushButton("Restore config from backup")
        restore_btn.clicked.connect(self._on_restore)
        row_layout.addWidget(export_btn)
        row_layout.addWidget(restore_btn)
        layout.addWidget(row)
        return w

    def _refresh_security(self) -> None:
        report = redacted_report()
        assert_no_secrets(report)
        self._diag_text.setPlainText(to_json(report))

    def _on_export(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        report = redacted_report()
        assert_no_secrets(report)
        path = QFileDialog.getSaveFileName(
            self, "Save diagnostics", "eyes-diagnostics.json", "*.json"
        )
        if not path[0]:
            return
        with open(path[0], "w", encoding="utf-8") as fh:
            fh.write(to_json(report))
        QMessageBox.information(self, "Diagnostics", f"Saved to {path[0]}")

    def _on_restore(self) -> None:
        try:
            restore_backup()
        except Exception as exc:
            QMessageBox.critical(self, "Config", str(exc))
            return
        QMessageBox.information(self, "Config", "Backup restored")
        self._refresh_all()


class _CredentialDialog(QDialog):
    """Small form dialog used for entry-level flows (kept for host re-use)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Token Plan credential")
        form = QFormLayout(self)
        self.url = QLineEdit()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Base URL", self.url)
        form.addRow("Token", self.token)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


def _asyncio_drain() -> None:  # pragma: no cover — GUI convenience
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    raise SystemExit(run())
