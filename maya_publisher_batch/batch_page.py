"""The "Maya Publisher" section — UkoreHub-side replacement for the Maya
window (maya-scripts/MayaPublisher/interface.py). Left: the queue of
scenes, each with its own ticket, destination and status. Right: the ticket
list (same Ticket | From | From Repo | To | To Repo columns, plus Mode) with
all of the old window's right-click ticket management, the selected
ticket's step list, and the note. Bottom: Start/Stop, progress, and the
selected row's Maya log.

Rows are (re)planned against the active repo whenever something changes
(publish_plan.plan_publish) so problems show before Start; Start re-plans
once more, then hands every ready row to a BatchQueue pinned to that repo —
switching repos in UkoreHub mid-batch doesn't affect it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import batch_queue, preset_store, publish_plan
from .maya_env import MayaEnvError, resolve_maya_launch
from .diagnose_tab import DiagnoseTab, interactive_columns
from .ticket_dialogs import TicketSettingsDialog
from .tickets import ticket_store

SCENE_EXTENSIONS = (".ma",)  # Maya ASCII only — .mb is never queued
EXPLORER_SECTION_KEY = "repo_browser"  # plugins/core/explorer's SectionSpec key
DATA_ROLE = Qt.ItemDataRole.UserRole
THUMBNAIL_SIZE = 256
ICON_SIZE = 28

COL_FILE, COL_TICKET, COL_BLOCK, COL_DEST, COL_STATUS, COL_TIME = range(6)
QUEUE_HEADERS = ("File", "Ticket", "Block", "Destination", "Status", "Time")
TCOL_TICKET, TCOL_FROM, TCOL_FROM_REPO, TCOL_TO, TCOL_TO_REPO, TCOL_MODE = range(6)
TICKET_HEADERS = ("Ticket", "From", "From Repo", "To", "To Repo", "Mode")
_TICKET_META_COLUMNS = {
    TCOL_FROM: "from_program",
    TCOL_FROM_REPO: "from_repo",
    TCOL_TO: "to_program",
    TCOL_TO_REPO: "to_repo",
}


@dataclass(eq=False)
class QueueRow:
    scene_path: str
    category: str | None = None
    script_name: str | None = None
    plan: publish_plan.PublishPlan | None = None
    job: batch_queue.BatchJob | None = None
    duplicate: bool = False
    log: str = ""

    @property
    def ticket(self):
        return (self.category, self.script_name) if self.category else None

    @property
    def done(self) -> bool:
        return self.job is not None and self.job.status == batch_queue.OK


def _format_elapsed(seconds) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _open_path(path) -> None:
    if path and os.path.exists(path):
        os.startfile(str(path))


class _QueueTree(QTreeWidget):
    """Accepts .ma/.mb files (and folders) dropped from Explorer."""

    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.paths_dropped.emit(paths)
        else:
            super().dropEvent(event)


class MayaPublisherPage(QWidget):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self._rows: list[QueueRow] = []
        self._items: dict[QueueRow, QTreeWidgetItem] = {}
        self._queue: batch_queue.BatchQueue | None = None
        self._queue_rows: dict[batch_queue.BatchJob, QueueRow] = {}
        self._project_id = None
        self._repo = None
        # UICommandService, set by plugin.py's SectionSpec.wire — None until then.
        self.ui_commands = None

        self._build_ui()
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self.refresh_context()

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------
    def _build_ui(self) -> None:
        style = self.style()

        # header
        self.label_repo = QLabel()
        self.label_maya = QLabel()
        self.spin_parallel = QSpinBox()
        self.spin_parallel.setRange(1, 8)
        self.spin_parallel.setValue(2)
        self.spin_parallel.setToolTip("How many headless (standalone) Maya jobs run at once. GUI jobs always run one at a time.")
        self.check_keep_gui = QCheckBox("Keep GUI Maya open (debug)")
        self.check_keep_gui.setToolTip("GUI-mode jobs leave Maya open after publishing, so a TD can inspect the scene.")
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_content)
        header = QHBoxLayout()
        header.addWidget(QLabel("Repo:"))
        header.addWidget(self.label_repo)
        header.addSpacing(16)
        header.addWidget(self.label_maya)
        header.addStretch(1)
        header.addWidget(QLabel("Parallel:"))
        header.addWidget(self.spin_parallel)
        header.addWidget(self.check_keep_gui)
        header.addWidget(refresh_button)

        # queue
        self.tree_queue = _QueueTree()
        self.tree_queue.setHeaderLabels(QUEUE_HEADERS)
        self.tree_queue.setRootIsDecorated(False)
        self.tree_queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree_queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_queue.customContextMenuRequested.connect(self._show_queue_menu)
        self.tree_queue.itemSelectionChanged.connect(self._show_selected_log)
        self.tree_queue.itemDoubleClicked.connect(self._on_queue_double_clicked)
        self.tree_queue.paths_dropped.connect(self.add_paths)
        interactive_columns(
            self.tree_queue, {COL_FILE: 200, COL_TICKET: 170, COL_BLOCK: 70, COL_DEST: 260, COL_STATUS: 260, COL_TIME: 50}
        )

        self.button_apply_ticket = QPushButton("Use Selected Ticket")
        self.button_apply_ticket.setToolTip("Set the ticket picked on the right for the selected rows (all rows if none selected).")
        self.button_apply_ticket.clicked.connect(self._apply_ticket_to_rows)
        self.button_remove = QPushButton("Remove")
        self.button_remove.clicked.connect(self._remove_selected_rows)
        self.button_clear = QPushButton("Clear")
        self.button_clear.clicked.connect(self._clear_rows)
        queue_buttons = QHBoxLayout()
        queue_buttons.addWidget(self.button_apply_ticket)
        queue_buttons.addStretch(1)
        queue_buttons.addWidget(self.button_remove)
        queue_buttons.addWidget(self.button_clear)

        queue_panel = QWidget()
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        self.combo_preset = QComboBox()
        self.combo_preset.setMinimumWidth(180)
        self.combo_preset.setToolTip("Saved queues for this repo, shared with the team.")
        self.button_load_preset = QPushButton("Load")
        self.button_load_preset.clicked.connect(self._load_preset)
        self.button_save_preset = QPushButton("Save As...")
        self.button_save_preset.clicked.connect(self._save_preset)
        self.button_delete_preset = QPushButton("Delete")
        self.button_delete_preset.clicked.connect(self._delete_preset)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        preset_row.addWidget(self.combo_preset, 1)
        for button in (self.button_load_preset, self.button_save_preset, self.button_delete_preset):
            preset_row.addWidget(button)
        queue_layout.addLayout(preset_row)
        queue_layout.addLayout(queue_buttons)
        queue_layout.addWidget(self.tree_queue)
        queue_hint = QLabel(
            "Add scenes from Explorer: right-click .ma files or a folder > Add to Maya Publish Queue "
            "(or drop them here). New rows get the ticket selected on the right."
        )
        queue_hint.setWordWrap(True)
        queue_layout.addWidget(queue_hint)

        # tickets
        self.check_match_repo = QCheckBox("Show match current repo")
        self.check_match_repo.setChecked(True)
        self.check_match_repo.toggled.connect(self.reload_tickets)
        self.tree_tickets = QTreeWidget()
        self.tree_tickets.setHeaderLabels(TICKET_HEADERS)
        self.tree_tickets.setRootIsDecorated(False)
        self.tree_tickets.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.tree_tickets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_tickets.customContextMenuRequested.connect(self._show_ticket_menu)
        self.tree_tickets.itemSelectionChanged.connect(self._show_ticket_info)
        self.tree_tickets.itemDoubleClicked.connect(lambda item, _col: self._edit_ticket(item.data(TCOL_TICKET, DATA_ROLE)))
        interactive_columns(self.tree_tickets, {TCOL_TICKET: 200})
        self.text_ticket_info = QTextBrowser()
        self.text_ticket_info.setOpenExternalLinks(True)
        self.text_note = QPlainTextEdit()
        self.text_note.setPlaceholderText("Note — written to note.txt in every version this batch publishes")
        self.text_note.setMaximumHeight(70)

        ticket_panel = QWidget()
        ticket_layout = QVBoxLayout(ticket_panel)
        ticket_layout.setContentsMargins(0, 0, 0, 0)
        ticket_layout.addWidget(self.check_match_repo)
        ticket_layout.addWidget(self.tree_tickets, 3)
        ticket_layout.addWidget(self.text_ticket_info, 1)
        ticket_layout.addWidget(self.text_note)

        self.diagnose_tab = DiagnoseTab(
            get_scenes=self._queued_scenes,
            get_roots=self._diagnose_roots,
            show_in_explorer=self._show_in_explorer,
            is_busy=lambda: self.is_running(),
        )
        self.tabs = QTabWidget()
        self.tabs.addTab(queue_panel, "Publish Queue")
        self.tabs.addTab(self.diagnose_tab, "Diagnose References")

        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.addWidget(self.tabs)
        top_splitter.addWidget(ticket_panel)
        top_splitter.setStretchFactor(0, 3)
        top_splitter.setStretchFactor(1, 2)
        top_splitter.setSizes([600, 400])

        # run controls + log
        self.button_start = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Start")
        self.button_start.clicked.connect(self.start_batch)
        self.button_stop = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop), "Stop")
        self.button_stop.clicked.connect(self.stop_batch)
        self.button_stop.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m")
        self.progress.setValue(0)
        self.progress.setMaximum(1)
        self.label_summary = QLabel()
        run_row = QHBoxLayout()
        run_row.addWidget(self.button_start)
        run_row.addWidget(self.button_stop)
        run_row.addWidget(self.progress, 1)
        run_row.addWidget(self.label_summary)

        self.text_log = QPlainTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setMaximumBlockCount(20000)
        self.text_log.setPlaceholderText("Select a row to see its Maya log.")
        self.text_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addLayout(run_row)
        bottom_layout.addWidget(self.text_log)

        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(bottom_panel)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([700, 250])

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(main_splitter, 1)

        self._icons = {
            "ok": style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
            "failed": style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical),
            "warning": style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
            "running": style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "cancelled": style.standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton),
            "ready": QIcon(),
        }

    # ------------------------------------------------------------
    # CONTEXT (active repo)
    # ------------------------------------------------------------
    def _resolve_context(self):
        """(project_id, repo) for the repo active in UkoreHub, or (None, None)."""
        config = self.api.local_config
        project_id, repo_id = config.active_project_id, config.active_repo_id
        if not (project_id and repo_id):
            return None, None
        try:
            return project_id, self.api.metadata.get_repo(project_id, repo_id)
        except Exception:  # noqa: BLE001 - NotFoundError, or a registry mid-sync
            return None, None

    def refresh_context(self, *_args) -> None:
        """Re-reads the active repo (on_repo_changed / Refresh). A running
        batch keeps its own pinned repo; rows are only re-planned once it's
        done."""
        if self.is_running():
            return
        self._project_id, self._repo = self._resolve_context()
        if self._repo is None:
            self.label_repo.setText("(no active repo)")
            self.label_maya.setText("")
        else:
            self.label_repo.setText(self._repo.name)
            try:
                launch = resolve_maya_launch(self.api, self._project_id, self._repo)
                self.label_maya.setText(f"Maya {launch.version}" if launch.version else "Maya")
                self.label_maya.setToolTip(launch.maya_exe)
            except MayaEnvError as exc:
                self.label_maya.setText("Maya: not linked")
                self.label_maya.setToolTip(str(exc))
        self.reload_tickets()
        self.reload_presets()
        self.replan_rows()
        self.diagnose_tab.mark_dirty()

    def refresh_content(self) -> None:
        """UICommandService.refresh_section() hook + the Refresh button."""
        self.refresh_context()

    def shutdown(self) -> None:
        """on_app_close — don't leave Maya processes running headless."""
        self.diagnose_tab.shutdown()
        if self._queue is not None:
            self._queue.cancel()

    def _queued_scenes(self) -> list[str]:
        return [row.scene_path for row in self._rows]

    def _diagnose_roots(self) -> tuple[str | None, str | None]:
        """(project root = the repo folder Maya's setProject uses, workspace root)."""
        workspace_root = self.api.local_config.workspace_root or None
        if self._repo is None or workspace_root is None:
            return None, workspace_root
        return os.path.join(workspace_root, self._repo.local_path), workspace_root

    def is_running(self) -> bool:
        return self._queue is not None and self._queue.is_running()

    # ------------------------------------------------------------
    # TICKETS
    # ------------------------------------------------------------
    def _ticket_label(self, ticket) -> str:
        category, script_name = ticket
        return category if category == script_name else f"{category} / {script_name}"

    def _ticket_matches_repo(self, ticket) -> bool:
        from_repo = ticket_store.get_ticket_meta(*ticket).get("from_repo")
        return not from_repo or (self._repo is not None and from_repo == self._repo.name)

    def _ticket_icon(self, ticket) -> QIcon:
        path = ticket_store.thumbnail_path(*ticket)
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return QIcon(pixmap.scaled(ICON_SIZE, ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        return QIcon()

    def selected_ticket(self):
        items = self.tree_tickets.selectedItems()
        return items[0].data(TCOL_TICKET, DATA_ROLE) if items else None

    def reload_tickets(self, *_args) -> None:
        previous = self.selected_ticket()
        self.tree_tickets.clear()
        only_matching = self.check_match_repo.isChecked()
        for ticket in ticket_store.list_tickets():
            matches = self._ticket_matches_repo(ticket)
            if only_matching and not matches:
                continue
            meta = ticket_store.get_ticket_meta(*ticket)
            item = QTreeWidgetItem(self.tree_tickets)
            item.setData(TCOL_TICKET, DATA_ROLE, ticket)
            item.setText(TCOL_TICKET, self._ticket_label(ticket))
            item.setIcon(TCOL_TICKET, self._ticket_icon(ticket))
            for column, key in _TICKET_META_COLUMNS.items():
                item.setText(column, meta.get(key) or "")
            mode = ticket_store.get_ticket_batch_mode(*ticket)
            item.setText(TCOL_MODE, "GUI" if mode == ticket_store.BATCH_MODE_GUI else "Standalone")
            item.setDisabled(not matches)
            if not matches:
                item.setToolTip(TCOL_TICKET, f"Publishes from repo '{meta.get('from_repo')}', not the active repo.")
            if ticket == previous:
                item.setSelected(True)
        if not self.tree_tickets.selectedItems() and self.tree_tickets.topLevelItemCount():
            for index in range(self.tree_tickets.topLevelItemCount()):
                item = self.tree_tickets.topLevelItem(index)
                if not item.isDisabled():
                    item.setSelected(True)
                    break
        for column in range(1, len(TICKET_HEADERS)):
            self.tree_tickets.resizeColumnToContents(column)
        self._show_ticket_info()

    def _show_ticket_info(self) -> None:
        ticket = self.selected_ticket()
        if ticket is None:
            self.text_ticket_info.setMarkdown("")
            return
        info = ticket_store.get_ticket_info(*ticket)
        self.text_ticket_info.setMarkdown(info or "_No step list yet — right-click the ticket > Edit Info..._")

    def _show_ticket_menu(self, pos) -> None:
        item = self.tree_tickets.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            ticket = item.data(TCOL_TICKET, DATA_ROLE)
            menu.addAction("Edit Ticket...", lambda: self._edit_ticket(ticket))
            menu.addAction("Set Icon from File...", lambda: self._set_ticket_icon(ticket))
            menu.addAction("Clear Icon", lambda: self._clear_ticket_icon(ticket))
            menu.addSeparator()
            menu.addAction("Edit Script...", lambda: _open_path(ticket_store.script_path(*ticket)))
            menu.addAction("Edit Info...", lambda: self._edit_ticket_info(ticket))
            menu.addAction("Open Ticket Dir...", lambda: _open_path(ticket_store.category_dir(ticket[0])))
        else:
            menu.addAction("New Ticket...", self._new_ticket)
            menu.addAction("Open Tickets Dir...", lambda: _open_path(ticket_store.tickets_root()))
        menu.exec(self.tree_tickets.viewport().mapToGlobal(pos))

    def _edit_ticket(self, ticket) -> None:
        if ticket is None or self._repo is None:
            return
        try:
            programs = [program.name for program in self.api.metadata.list_programs(self._project_id)]
        except Exception:  # noqa: BLE001 - an unreadable Program DB just means no choices
            programs = []
        dialog = TicketSettingsDialog(
            self._ticket_label(ticket),
            ticket_store.get_ticket_meta(*ticket),
            programs,
            publish_plan.project_repo_names(self.api, self._project_id),
            publish_plan.target_repo_names(self.api, self._project_id, self._repo.id),
            open_script=lambda: _open_path(ticket_store.script_path(*ticket)),
            parent=self,
        )
        if not dialog.exec():
            return
        try:
            ticket_store.set_ticket_meta(*ticket, dialog.values())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Maya Publisher", f"Couldn't save the ticket: {exc}")
        self.reload_tickets()
        self.replan_rows()

    def _set_ticket_icon(self, ticket) -> None:
        image_path, _ = QFileDialog.getOpenFileName(self, "Ticket Icon", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not image_path:
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            QMessageBox.warning(self, "Maya Publisher", f"Can't read image: {image_path}")
            return
        if pixmap.width() > THUMBNAIL_SIZE or pixmap.height() > THUMBNAIL_SIZE:
            pixmap = pixmap.scaled(THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if not pixmap.save(str(ticket_store.thumbnail_path(*ticket)), "PNG"):
            QMessageBox.warning(self, "Maya Publisher", "Couldn't write the icon file.")
        self.reload_tickets()

    def _clear_ticket_icon(self, ticket) -> None:
        ticket_store.clear_ticket_thumbnail(*ticket)
        self.reload_tickets()

    def _edit_ticket_info(self, ticket) -> None:
        _open_path(ticket_store.ensure_ticket_info(*ticket))

    def _new_ticket(self) -> None:
        name, ok = QInputDialog.getText(self, "New Ticket", "Ticket name (also its category / Custom Path label):")
        name = name.strip()
        if not ok or not name:
            return
        if not name.replace("_", "").isalnum():
            QMessageBox.warning(self, "Maya Publisher", "Use letters, digits and _ only.")
            return
        try:
            _category, path = ticket_store.create_ticket_script(name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Maya Publisher", str(exc))
            return
        self.reload_tickets()
        _open_path(path)

    # ------------------------------------------------------------
    # QUEUE ROWS
    # ------------------------------------------------------------
    @staticmethod
    def collect_scenes(paths) -> list[str]:
        """Every .ma in `paths` — folders searched recursively, in name order."""
        scenes = []
        for path in map(str, paths):
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    dirs.sort(key=str.lower)
                    scenes.extend(os.path.join(root, f) for f in sorted(files, key=str.lower) if f.lower().endswith(SCENE_EXTENSIONS))
            elif path.lower().endswith(SCENE_EXTENSIONS):
                scenes.append(path)
        return scenes

    def add_paths(self, paths) -> int:
        """Queues every .ma in `paths` (folders searched recursively) with
        the selected ticket, skipping an identical scene+ticket row. Used by
        drag-drop and Explorer's "Add to Maya Publish Queue" — returns how
        many rows were added (0 while a batch is running)."""
        if self.is_running():
            return 0
        ticket = self.selected_ticket()
        return self._append_rows([(scene, ticket) for scene in self.collect_scenes(paths)])

    def browse_to_path(self, path) -> None:
        """PathFocusablePage protocol (UICommandService.navigate_and_focus) —
        selects the queue row(s) for `path`, so the rows Explorer just added
        are highlighted when this section opens."""
        target = os.path.normcase(os.path.abspath(str(path)))
        self.tree_queue.clearSelection()
        for row, item in self._items.items():
            if os.path.normcase(os.path.abspath(row.scene_path)) == target:
                item.setSelected(True)
                self.tree_queue.scrollToItem(item)

    def _append_rows(self, entries) -> int:
        """Adds a row per (scene_path, ticket or None), skipping one that
        repeats an existing scene+ticket row, then re-plans. Returns the
        number of rows added."""
        added = 0
        existing = {(os.path.normcase(os.path.abspath(r.scene_path)), r.ticket) for r in self._rows}
        for scene, ticket in entries:
            key = (os.path.normcase(os.path.abspath(scene)), ticket)
            if key in existing:
                continue
            existing.add(key)
            row = QueueRow(scene_path=os.path.normpath(scene))
            if ticket:
                row.category, row.script_name = ticket
            self._rows.append(row)
            item = QTreeWidgetItem(self.tree_queue)
            item.setData(COL_FILE, DATA_ROLE, row)
            self._items[row] = item
            added += 1
        self.replan_rows()
        if added:
            self.diagnose_tab.mark_dirty()
        return added

    # ------------------------------------------------------------
    # PRESETS
    # ------------------------------------------------------------
    def reload_presets(self) -> None:
        current = self.combo_preset.currentText()
        self.combo_preset.clear()
        if self._repo is not None:
            presets = preset_store.list_presets(self.api, self._project_id, self._repo.id)
            for name in sorted(presets, key=str.lower):
                preset = presets[name]
                self.combo_preset.addItem(name)
                self.combo_preset.setItemData(
                    self.combo_preset.count() - 1,
                    f"{len(preset.get('rows', []))} scene(s) — saved {preset.get('updated_at', '?')}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        index = self.combo_preset.findText(current)
        if index >= 0:
            self.combo_preset.setCurrentIndex(index)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        has_preset = self.combo_preset.count() > 0
        running = self.is_running()
        self.combo_preset.setEnabled(has_preset and not running)
        self.button_load_preset.setEnabled(has_preset and not running)
        self.button_delete_preset.setEnabled(has_preset and not running)
        self.button_save_preset.setEnabled(self._repo is not None and bool(self._rows))

    def _load_preset(self) -> None:
        name = self.combo_preset.currentText()
        if not name or self._repo is None or self.is_running():
            return
        try:
            rows, note = preset_store.load_preset(self.api, self._project_id, self._repo.id, name)
        except KeyError:
            QMessageBox.warning(self, "Maya Publisher", f"Preset '{name}' no longer exists.")
            self.reload_presets()
            return

        if self._rows:
            box = QMessageBox(self)
            box.setWindowTitle("Maya Publisher")
            box.setText(f"Load preset '{name}' ({len(rows)} scene(s))?")
            replace = box.addButton("Replace Queue", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Add to Queue", QMessageBox.ButtonRole.ActionRole)
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is cancel:
                return
            if box.clickedButton() is replace:
                self._clear_rows()

        self._append_rows([(row.scene_path, (row.category, row.script_name) if row.category else None) for row in rows])
        if note:
            self.text_note.setPlainText(note)
        missing = [row.scene_path for row in rows if not os.path.isfile(row.scene_path)]
        if missing:
            QMessageBox.warning(
                self,
                "Maya Publisher",
                f"{len(missing)} scene(s) in this preset aren't on this machine (not cloned/pulled yet?) — "
                "they're in the queue marked \"Can't publish\".",
            )

    def _save_preset(self) -> None:
        if self._repo is None or not self._rows:
            return
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name (e.g. a set of shots):", text=self.combo_preset.currentText())
        name = name.strip()
        if not ok or not name:
            return
        existing = preset_store.list_presets(self.api, self._project_id, self._repo.id)
        if name in existing:
            answer = QMessageBox.question(self, "Maya Publisher", f"Overwrite preset '{name}'? Everyone on this repo shares it.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        rows = [preset_store.PresetRow(r.scene_path, r.category, r.script_name) for r in self._rows]
        try:
            preset_store.save_preset(self.api, self._project_id, self._repo.id, name, rows, self.text_note.toPlainText().strip())
        except Exception as exc:  # noqa: BLE001 - a registry write failure is shown, not raised into Qt
            QMessageBox.warning(self, "Maya Publisher", f"Couldn't save preset: {exc}")
            return
        self.reload_presets()
        self.combo_preset.setCurrentIndex(self.combo_preset.findText(name))

    def _delete_preset(self) -> None:
        name = self.combo_preset.currentText()
        if not name or self._repo is None:
            return
        answer = QMessageBox.question(self, "Maya Publisher", f"Delete preset '{name}' for everyone on this repo?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            preset_store.delete_preset(self.api, self._project_id, self._repo.id, name)
        except Exception as exc:  # noqa: BLE001 - same as _save_preset
            QMessageBox.warning(self, "Maya Publisher", f"Couldn't delete preset: {exc}")
        self.reload_presets()

    def _selected_rows(self) -> list[QueueRow]:
        return [item.data(COL_FILE, DATA_ROLE) for item in self.tree_queue.selectedItems()]

    def _apply_ticket_to_rows(self) -> None:
        ticket = self.selected_ticket()
        if ticket is None or self.is_running():
            return
        for row in self._selected_rows() or self._rows:
            if not row.done:
                row.category, row.script_name = ticket
                row.job = None
        self.replan_rows()

    def _remove_selected_rows(self) -> None:
        if self.is_running():
            return
        for row in self._selected_rows():
            self._rows.remove(row)
            item = self._items.pop(row)
            self.tree_queue.takeTopLevelItem(self.tree_queue.indexOfTopLevelItem(item))
        self.replan_rows()
        self.diagnose_tab.mark_dirty()

    def _clear_rows(self) -> None:
        if self.is_running():
            return
        self._rows.clear()
        self._items.clear()
        self.tree_queue.clear()
        self.text_log.clear()
        self.label_summary.setText("")
        self.progress.setValue(0)
        self._update_preset_buttons()
        self.diagnose_tab.mark_dirty()

    def _reset_rows(self, rows) -> None:
        """Run Again — forget the last result so Start publishes it again."""
        for row in rows:
            row.job = None
            row.log = ""
        self.replan_rows()

    def replan_rows(self) -> None:
        """Re-resolves every row that hasn't run yet against the active repo."""
        if self.is_running():
            return
        for row in self._rows:
            if row.job is not None:
                continue
            if row.ticket is None:
                row.plan = publish_plan.PublishPlan(scene_path=row.scene_path, category="", script_name="", error="No ticket picked.")
            elif self._repo is None:
                row.plan = publish_plan.PublishPlan(scene_path=row.scene_path, category=row.category, script_name=row.script_name, error="No active repo in UkoreHub.")
            else:
                row.plan = publish_plan.plan_publish(self.api, self._project_id, self._repo.id, row.scene_path, row.category, row.script_name)
        duplicates = publish_plan.duplicate_destinations([r.plan for r in self._rows if r.plan is not None and r.job is None])
        for row in self._rows:
            row.duplicate = row.job is None and row.plan is not None and row.plan.ok and row.plan.base_dir in duplicates
            self._update_item(row)
        self._update_preset_buttons()

    def _update_item(self, row: QueueRow) -> None:
        item = self._items.get(row)
        if item is None:
            return
        plan, job = row.plan, row.job
        item.setText(COL_FILE, os.path.basename(row.scene_path))
        item.setToolTip(COL_FILE, row.scene_path)
        ticket_text = self._ticket_label(row.ticket) if row.ticket else "—"
        if plan is not None and plan.ok and plan.batch_mode == ticket_store.BATCH_MODE_GUI:
            ticket_text += " (GUI)"
        item.setText(COL_TICKET, ticket_text)
        item.setText(COL_BLOCK, plan.block_name if plan else "")

        if job is not None and job.version_dir:
            item.setText(COL_DEST, f"{plan.target_repo}: {plan.script_name}/{plan.block_name}/v{job.version:03d}")
            item.setToolTip(COL_DEST, job.version_dir)
        elif plan is not None and plan.ok:
            item.setText(COL_DEST, f"{plan.target_repo}: {plan.script_name}/{plan.block_name}/v{plan.next_version:03d}")
            item.setToolTip(COL_DEST, os.path.join(plan.base_dir, f"v{plan.next_version:03d}"))
        else:
            item.setText(COL_DEST, "")
            item.setToolTip(COL_DEST, "")

        if job is not None:
            status_text, icon, tip = {
                batch_queue.PENDING: ("Waiting", "ready", ""),
                batch_queue.RUNNING: ("Running...", "running", ""),
                batch_queue.OK: ("Published", "ok", job.message),
                batch_queue.FAILED: ("Failed", "failed", job.message),
                batch_queue.CANCELLED: ("Cancelled", "cancelled", job.message),
            }[job.status]
            if job.status in (batch_queue.FAILED, batch_queue.CANCELLED) and job.message:
                status_text = f"{status_text}: {job.message}"
        elif plan is None or not plan.ok:
            status_text, icon, tip = (f"Can't publish: {plan.error}" if plan else "", "warning", plan.error if plan else "")
        elif row.duplicate:
            status_text, icon, tip = ("Ready — same folder as another row", "warning", "Each still gets its own version number.")
        else:
            status_text, icon, tip = ("Ready", "ready", "")
        item.setText(COL_STATUS, status_text)
        item.setIcon(COL_STATUS, self._icons[icon])
        item.setToolTip(COL_STATUS, tip or status_text)
        item.setText(COL_TIME, _format_elapsed(job.elapsed_sec) if job else "")

    def _show_queue_menu(self, pos) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        running = self.is_running()
        menu = QMenu(self)
        action = menu.addAction("Use Selected Ticket", self._apply_ticket_to_rows)
        action.setEnabled(not running and self.selected_ticket() is not None)
        action = menu.addAction("Run Again", lambda: self._reset_rows(rows))
        action.setEnabled(not running and any(r.job is not None for r in rows))
        menu.addSeparator()
        row = rows[0]
        action = menu.addAction("Show in Explorer", lambda: self._show_in_explorer(row.scene_path))
        action.setEnabled(self.ui_commands is not None and os.path.isfile(row.scene_path))
        menu.addAction("Open Scene Folder", lambda: _open_path(os.path.dirname(row.scene_path)))
        publish_dir = (row.job.version_dir if row.job and row.job.version_dir else None) or (row.plan.base_dir if row.plan and row.plan.ok else None)
        action = menu.addAction("Open Publish Folder", lambda: _open_path(publish_dir))
        action.setEnabled(bool(publish_dir and os.path.isdir(publish_dir)))
        action = menu.addAction("Open Log File", lambda: _open_path(row.job.log_path if row.job else None))
        action.setEnabled(bool(row.job and os.path.isfile(row.job.log_path)))
        menu.addSeparator()
        action = menu.addAction("Remove", self._remove_selected_rows)
        action.setEnabled(not running)
        menu.exec(self.tree_queue.viewport().mapToGlobal(pos))

    def _show_in_explorer(self, scene_path: str) -> None:
        """Switches to UkoreHub's Explorer with the scene's row selected
        (Explorer's own browse_to_path, via navigate_and_focus)."""
        if self.ui_commands is not None:
            self.ui_commands.navigate_and_focus(EXPLORER_SECTION_KEY, Path(scene_path))

    def _on_queue_double_clicked(self, item, _column) -> None:
        row = item.data(COL_FILE, DATA_ROLE)
        if row.done:
            _open_path(row.job.version_dir)

    def _show_selected_log(self) -> None:
        rows = self._selected_rows()
        self.text_log.setPlainText(rows[0].log if rows else "")
        self.text_log.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------
    def _set_running_ui(self, running: bool) -> None:
        for widget in (
            self.button_start,
            self.button_apply_ticket,
            self.button_remove,
            self.button_clear,
            self.spin_parallel,
            self.check_keep_gui,
            self.text_note,
        ):
            widget.setEnabled(not running)
        self.tree_queue.setAcceptDrops(not running)
        self.button_stop.setEnabled(running)
        self._update_preset_buttons()

    def start_batch(self) -> None:
        if self.is_running():
            return
        self.refresh_context()
        if self._repo is None:
            QMessageBox.warning(self, "Maya Publisher", "Pick a project/repo in UkoreHub first.")
            return
        ready = [r for r in self._rows if r.job is None and r.plan is not None and r.plan.ok]
        blocked = [r for r in self._rows if r.job is None and (r.plan is None or not r.plan.ok)]
        if not ready:
            QMessageBox.information(self, "Maya Publisher", "Nothing ready to publish — add scenes and pick a ticket.")
            return
        try:
            launch = resolve_maya_launch(self.api, self._project_id, self._repo)
        except MayaEnvError as exc:
            QMessageBox.warning(self, "Maya Publisher", str(exc))
            return

        gui_count = sum(1 for r in ready if r.plan.batch_mode == ticket_store.BATCH_MODE_GUI)
        lines = [f"Publish {len(ready)} scene(s) from repo '{self._repo.name}'?"]
        if gui_count:
            lines.append(f"{gui_count} of them open a Maya window (GUI tickets), one at a time.")
        if blocked:
            lines.append(f"{len(blocked)} row(s) can't publish and will be skipped.")
        if any(r.duplicate for r in ready):
            lines.append("Some rows publish into the same folder — each gets its own version.")
        if QMessageBox.question(self, "Maya Publisher", "\n".join(lines)) != QMessageBox.StandardButton.Yes:
            return

        try:
            work_dir = batch_queue.new_work_dir(self.api.cache_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Maya Publisher", f"Couldn't create the batch folder: {exc}")
            return
        queue = batch_queue.BatchQueue(
            launch,
            work_dir,
            max_standalone=self.spin_parallel.value(),
            keep_gui_open=self.check_keep_gui.isChecked(),
            parent=self,
        )
        note = self.text_note.toPlainText().strip()
        self._queue_rows = {}
        for row in ready:
            row.log = ""
            row.job = queue.add(row.plan, note=note)
            self._queue_rows[row.job] = row
            self._update_item(row)
        queue.job_started.connect(self._on_job_changed)
        queue.job_finished.connect(self._on_job_changed)
        queue.job_output.connect(self._on_job_output)
        queue.finished.connect(self._on_batch_finished)

        self._queue = queue
        self.progress.setMaximum(len(ready))
        self.progress.setValue(0)
        self.label_summary.setText("")
        self._set_running_ui(True)
        self._clock.start()
        queue.start()

    def stop_batch(self) -> None:
        if not self.is_running():
            return
        answer = QMessageBox.question(self, "Maya Publisher", "Stop the batch? Running Maya jobs are killed; their empty version folders are removed.")
        if answer == QMessageBox.StandardButton.Yes:
            self._queue.cancel()

    def _on_job_changed(self, job) -> None:
        row = self._queue_rows.get(job)
        if row is not None:
            self._update_item(row)
        self._update_progress()

    def _on_job_output(self, job, text: str) -> None:
        row = self._queue_rows.get(job)
        if row is None:
            return
        row.log += text
        selected = self._selected_rows()
        if selected and selected[0] is row:
            self.text_log.moveCursor(QTextCursor.MoveOperation.End)
            self.text_log.insertPlainText(text)
            self.text_log.moveCursor(QTextCursor.MoveOperation.End)

    def _tick(self) -> None:
        for job, row in self._queue_rows.items():
            if job.status == batch_queue.RUNNING:
                item = self._items.get(row)
                if item is not None:
                    item.setText(COL_TIME, _format_elapsed(job.elapsed_sec))

    def _counts(self) -> dict:
        counts = {batch_queue.OK: 0, batch_queue.FAILED: 0, batch_queue.CANCELLED: 0}
        for job in self._queue_rows:
            if job.status in counts:
                counts[job.status] += 1
        return counts

    def _update_progress(self) -> None:
        counts = self._counts()
        self.progress.setValue(sum(counts.values()))
        self.label_summary.setText(
            f"✔ {counts[batch_queue.OK]}   ✘ {counts[batch_queue.FAILED]}   ⊘ {counts[batch_queue.CANCELLED]}"
        )

    def _on_batch_finished(self) -> None:
        self._clock.stop()
        self._tick()
        self._update_progress()
        self._queue = None
        self._set_running_ui(False)
        counts = self._counts()
        self.replan_rows()
        if counts[batch_queue.FAILED]:
            QMessageBox.warning(
                self,
                "Maya Publisher",
                f"Batch finished: {counts[batch_queue.OK]} published, {counts[batch_queue.FAILED]} failed. "
                "Select a failed row to see its Maya log.",
            )
