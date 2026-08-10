from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QLabel, QRadioButton, QVBoxLayout, QWidget

from plugins.repo_internal.MayaPublisher.interface import publish_mode_store


class PublishModeSettingsPage(QWidget):
    """Repository Setting > MayaPublisher — picks which of Rig / Model /
    Animation this repo publishes as (replaces enabling one of the old
    separate RigPublisher/ModelPublisher/AnimationPublisher plugins). Same
    self-resolving-active-repo `refresh()` pattern every CATEGORY_REPO tab
    in this app uses (e.g. UkoreShot's own RepoVideoSettingsPage) — a
    single choice, self-persisting on click."""

    def __init__(self, parent=None, *, api):
        super().__init__(parent)
        self._api = api
        self._project_id: str | None = None
        self._repo_id: str | None = None

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)

        self._button_group = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint_label)
        for mode in publish_mode_store.MODES:
            button = QRadioButton(publish_mode_store.MODE_LABELS[mode])
            self._button_group.addButton(button)
            self._buttons[mode] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self._button_group.buttonClicked.connect(self._on_mode_clicked)

        self.refresh()

    def refresh(self) -> None:
        project_id = self._api.local_config.active_project_id
        repo_id = self._api.local_config.active_repo_id
        self._project_id = project_id
        self._repo_id = repo_id

        if not project_id or not repo_id:
            self.hint_label.setText("Select a repo to see this information.")
            for button in self._buttons.values():
                button.setEnabled(False)
            return

        for button in self._buttons.values():
            button.setEnabled(True)

        current_mode = publish_mode_store.get_publish_mode(self._api, project_id, repo_id)
        self.hint_label.setText(
            "Which kind of scene does this repo publish with MayaPublisher? "
            "Every ticket in this repo's Maya-side Manage Tickets... uses this mode."
            if current_mode
            else "This repo has no Publish Mode set yet — pick one below before publishing "
            "anything with MayaPublisher."
        )

        self._button_group.blockSignals(True)
        for mode, button in self._buttons.items():
            button.setChecked(mode == current_mode)
        self._button_group.blockSignals(False)

    def _on_mode_clicked(self, button) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        mode = next(m for m, b in self._buttons.items() if b is button)
        publish_mode_store.set_publish_mode(self._api, self._project_id, self._repo_id, mode)
        self.refresh()
