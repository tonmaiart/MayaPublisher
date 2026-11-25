"""Edit Ticket... dialog — UkoreHub-side port of the Maya window's
TicketSettingsDialog (maya-scripts/MayaPublisher/interface.py), plus the
new Batch Mode field. Programs and repos are picked by name, which is how
ticket_store stores them."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .tickets import ticket_store

_NONE_LABEL = "(none)"
_BATCH_MODE_LABELS = {
    ticket_store.BATCH_MODE_STANDALONE: "Standalone — headless mayapy (fast, parallel)",
    ticket_store.BATCH_MODE_GUI: "GUI — opens a Maya window (for tickets that need a viewport)",
}


def _fill_combo(combo: QComboBox, choices: list[str], current, allow_none: bool = True) -> None:
    if allow_none:
        combo.addItem(_NONE_LABEL, None)
    for choice in choices:
        combo.addItem(choice, choice)
    if current and current not in choices:
        combo.addItem(f"{current} (not found)", current)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)


class TicketSettingsDialog(QDialog):
    def __init__(self, ticket_label, meta, programs, from_repos, to_repos, open_script, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Ticket — {ticket_label}")
        self.setMinimumWidth(420)

        self._combos = {}
        form = QFormLayout()
        for key, label, choices in (
            ("from_program", "From Program", programs),
            ("from_repo", "From Repo", from_repos),
            ("to_program", "To Program", programs),
            ("to_repo", "To Repo", to_repos),
        ):
            combo = QComboBox()
            _fill_combo(combo, choices, meta.get(key))
            form.addRow(label, combo)
            self._combos[key] = combo

        mode_combo = QComboBox()
        for mode in ticket_store.BATCH_MODES:
            mode_combo.addItem(_BATCH_MODE_LABELS[mode], mode)
        current_mode = meta.get(ticket_store.TICKET_BATCH_MODE_KEY) or ticket_store.BATCH_MODE_DEFAULT
        mode_combo.setCurrentIndex(max(0, mode_combo.findData(current_mode)))
        form.addRow("Batch Mode", mode_combo)
        self._combos[ticket_store.TICKET_BATCH_MODE_KEY] = mode_combo

        hint = QLabel("From Repo: the ticket only publishes from that repo. To Repo: only that repo's Custom Paths are searched.")
        hint.setWordWrap(True)

        open_button = QPushButton("Open Script...")
        open_button.clicked.connect(open_script)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(open_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def values(self) -> dict:
        """{meta key: value or None} — feed straight to ticket_store.set_ticket_meta()."""
        return {key: combo.currentData() for key, combo in self._combos.items()}
