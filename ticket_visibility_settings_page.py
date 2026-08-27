"""UkoreHub-side "Ticket Visibility" Repository Setting tab for MayaPublisher.

Lets a studio admin uncheck individual Ticket Scripts (see function.py's
module docstring for what a Ticket Script is) per repo. An unchecked
script is filtered out of the Maya-side "Choose Ticket Scripts" list by
function.get_hidden_ticket_scripts()/list_ticket_scripts() — this file
itself has no Maya dependency and is never imported from Maya (Maya only
sees maya-scripts/, not this plugin's own root).

Loaded from plugin.py via importlib.util.spec_from_file_location, not a
normal import — see developer/app/docs/plugins-guide.md's "Working on a
single plugin" section: a cache/plugins/ folder isn't guaranteed to sit
under a real plugins.* package, so sibling files load by file path
instead of a dotted import.

Stored on Repo.plugin_data["maya_publisher"]["hidden_tickets"]
(category -> list of hidden script names) via
MetadataStore.get_repo_plugin_data/set_repo_plugin_data — that data lives
inside the repo's own project blob (data/projects/<id>.json), already
cloud-synced, same convention plugins/core/explorer/bookmarks_store.py
uses for per-repo bookmarks.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

TOOL_ID = "maya_publisher"
_DATA_KEY = "hidden_tickets"


def _tickets_root(plugin_root: Path) -> Path:
    return plugin_root / "maya-scripts" / "MayaPublisher" / "tickets"


def _list_categories(plugin_root: Path) -> list[str]:
    root = _tickets_root(plugin_root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def _list_ticket_scripts(plugin_root: Path, category: str) -> list[str]:
    """Every Ticket Script under this category, unfiltered — unlike the
    Maya-side function.list_ticket_scripts(), this always shows every
    script (including already-hidden ones) so the admin can re-check one."""
    folder = _tickets_root(plugin_root) / category
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.iterdir() if p.suffix == ".py" and p.stem != "__init__")


class TicketVisibilitySettingsPage(QWidget):
    def __init__(self, metadata_store, get_repo_context, plugin_root: Path):
        super().__init__()
        self._metadata_store = metadata_store
        self._get_repo_context = get_repo_context
        self._plugin_root = Path(plugin_root)
        self._checkboxes: dict[str, dict[str, QCheckBox]] = {}

        self._body_layout = QVBoxLayout()
        body = QWidget()
        body.setLayout(self._body_layout)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(body)

        self._status_label = QLabel()
        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self._save)

        outer = QVBoxLayout(self)
        outer.addWidget(
            QLabel(
                "Uncheck a Ticket Script to hide it from this repo's MayaPublisher "
                "window in Maya. Categories and scripts come from MayaPublisher's own "
                "bundled tickets/ folder."
            )
        )
        outer.addWidget(scroll_area, 1)
        outer.addWidget(self._status_label)
        outer.addWidget(self._save_button)

    def refresh(self) -> None:
        self._status_label.setText("")
        self._clear_body()
        self._checkboxes = {}

        repo_context = self._get_repo_context()
        if repo_context is None:
            self._body_layout.addWidget(QLabel("No active repo selected in UkoreHub."))
            self._save_button.setEnabled(False)
            return
        self._save_button.setEnabled(True)

        categories = _list_categories(self._plugin_root)
        if not categories:
            self._body_layout.addWidget(QLabel("No ticket categories found under tickets/."))
            return

        entry = self._metadata_store.get_repo_plugin_data(repo_context.project_id, repo_context.repo_id, TOOL_ID)
        hidden_by_category = entry.get(_DATA_KEY, {})

        for category in categories:
            scripts = _list_ticket_scripts(self._plugin_root, category)
            group = QGroupBox(category)
            group_layout = QVBoxLayout(group)
            hidden_for_category = set(hidden_by_category.get(category, []))
            self._checkboxes[category] = {}

            if not scripts:
                group_layout.addWidget(QLabel("(no ticket scripts)"))
            for script_name in scripts:
                checkbox = QCheckBox(script_name)
                checkbox.setChecked(script_name not in hidden_for_category)
                group_layout.addWidget(checkbox)
                self._checkboxes[category][script_name] = checkbox

            self._body_layout.addWidget(group)
        self._body_layout.addStretch(1)

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _save(self) -> None:
        repo_context = self._get_repo_context()
        if repo_context is None:
            return

        hidden_by_category: dict[str, list[str]] = {}
        for category, checkboxes in self._checkboxes.items():
            hidden_scripts = [name for name, checkbox in checkboxes.items() if not checkbox.isChecked()]
            if hidden_scripts:
                hidden_by_category[category] = hidden_scripts

        entry = dict(
            self._metadata_store.get_repo_plugin_data(repo_context.project_id, repo_context.repo_id, TOOL_ID)
        )
        entry[_DATA_KEY] = hidden_by_category
        self._metadata_store.set_repo_plugin_data(repo_context.project_id, repo_context.repo_id, TOOL_ID, entry)

        self._status_label.setText("Saved.")
