# ================================================
#  MAYA PUBLISHER — MAYA
# ================================================

# This tool created by Natchapon Srisuk, only for Ukore Studio's projects.
# contact : natchapon.18851@gmail.com

# Category (Rig/Anim/Model/...) + "Choose Ticket Scripts" list replace the
# old repo-wide Publish Mode Setting + PublishApi.tickets.json-managed
# tickets (2026-08-19) — see function.py's module docstring for the full
# rationale. Categories are just tickets/ subfolder names
# (function.list_categories()); this repo's last-picked one is remembered
# in <repo_root>/.MayaPublisher (removed 2026-10-11). Each ticket
# is now a plain .py file (function.list_ticket_scripts()) instead of a
# JSON-managed ticket dict — no more "Manage Tickets..." dialog.
#
# 2026-10-11: the category combo box is gone — treeWidget_ticket now lists
# every ticket (function.list_tickets()) as one flat row with its own
# thumbnail icon, and picking a row picks exactly that ticket. Each row
# carries its (category, script_name) in TICKET_ROLE on COL_TICKET. The
# From/To columns show the ticket's from_program/to_program icons, picked
# from UkoreHub's Program DB via ProgramPickerDialog (double-click a cell);
# From Repo/To Repo columns show its from_repo/to_repo (Edit...). The
# "Show match current repo" checkbox (always on when the window opens)
# hides tickets whose from_repo isn't the active repo — this replaced the
# old UkoreHub-side "Ticket Visibility" setting.
# ================================================

import os

from importlib import reload

import maya.cmds as cmds
from MayaPublisher import function
from PublishApi import versioning
from tmlib.module.PySide import QtCore, QtGui, QtWidgets
from tmlib.ui.interface_template import ToolkitWindow

reload(function)

TICKET_ROLE = QtCore.Qt.UserRole
COL_TICKET, COL_FROM, COL_FROM_REPO, COL_TO, COL_TO_REPO = 0, 1, 2, 3, 4
PROGRAM_COLUMNS = {COL_FROM: "from_program", COL_TO: "to_program"}
REPO_COLUMNS = {COL_FROM_REPO: "from_repo", COL_TO_REPO: "to_repo"}


class ProgramPickerDialog(QtWidgets.QDialog):
    """Icon list over the active Project's Program DB (function.list_programs()),
    plus "(None)" to clear. Double-click or OK picks."""

    def __init__(self, programs, current, icon_factory, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(300, 360)

        self._list = QtWidgets.QListWidget()
        self._list.setIconSize(QtCore.QSize(32, 32))
        none_item = QtWidgets.QListWidgetItem("(None)")
        none_item.setData(QtCore.Qt.UserRole, None)
        self._list.addItem(none_item)
        self._list.setCurrentItem(none_item)
        for name, icon_path in programs:
            item = QtWidgets.QListWidgetItem(icon_factory(name, icon_path), name)
            item.setData(QtCore.Qt.UserRole, name)
            self._list.addItem(item)
            if name == current:
                self._list.setCurrentItem(item)
        self._list.itemDoubleClicked.connect(self.accept)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addWidget(buttons)

    def selected_program(self):
        item = self._list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None


class TicketSettingsDialog(QtWidgets.QDialog):
    """Right-click > Edit Ticket... — edits one ticket's From/To program
    (UkoreHub Program DB) and From/To repo. Each combo's first entry clears
    the value; a saved value that's no longer offered (e.g. a repo from
    another Project) stays listed and selected so OK never silently drops
    it."""

    def __init__(self, ticket, meta, programs, from_repos, to_repos, icon_factory, open_script, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Ticket — {}".format(ticket[0]))
        self.setMinimumWidth(360)

        self._combos = {}
        form = QtWidgets.QFormLayout()
        program_choices = [(name, icon_factory(name, icon_path)) for name, icon_path in programs]
        rows = [
            ("from_program", "From Program :", "(None)", program_choices),
            ("to_program", "To Program :", "(None)", program_choices),
            ("from_repo", "From Repo :", "(Any repo)", [(name, None) for name in from_repos]),
            ("to_repo", "To Repo :", "(First Custom Path match)", [(name, None) for name in to_repos]),
        ]
        for key, label, empty_text, choices in rows:
            combo = QtWidgets.QComboBox()
            combo.setIconSize(QtCore.QSize(20, 20))
            combo.addItem(empty_text, None)
            for name, icon in choices:
                if icon is not None:
                    combo.addItem(icon, name, name)
                else:
                    combo.addItem(name, name)

            current = meta.get(key)
            if current and combo.findData(current) < 0:
                combo.addItem("{} (not available here)".format(current), current)
            combo.setCurrentIndex(max(0, combo.findData(current)) if current else 0)
            form.addRow(label, combo)
            self._combos[key] = combo

        hint = QtWidgets.QLabel(
            "From Repo: this ticket only works when that repo is active.\n"
            "To Repo: publish only into that repo's Custom Path named '{}'.".format(ticket[0])
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgb(160, 160, 160);")

        open_script_btn = QtWidgets.QPushButton("Open Script...")
        open_script_btn.clicked.connect(open_script)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(open_script_btn)
        button_row.addStretch(1)
        button_row.addWidget(buttons)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(button_row)

    def values(self):
        return {key: combo.currentData() for key, combo in self._combos.items()}


class MainWindow(ToolkitWindow):
    def __init__(self):
        super(MainWindow, self).__init__(os.path.basename(os.path.dirname(__file__)))

        self._current_category = None
        self._active_repo_path = None

        self._setup_layout()
        self._setup_ticket_tree()
        self.connect_signals()
        self.populate_repo_info()
        self.initialize_ticket_list_widget()
        self._register_scene_refresh_job()
        # Hidden until the first successful publish — otherwise its 40px
        # minimum height leaves an empty strip under both groups.
        self.ui.label_publish_result.hide()

    # ------------------------------------------------------------
    # SIGNALS
    # ------------------------------------------------------------
    def connect_signals(self):
        self.ui.pushButton_refresh.clicked.connect(self.refresh_all)
        self.ui.pushButton_copy_repo_path.clicked.connect(self.copy_active_repo_path)
        self.ui.checkBox_show_match_repo.toggled.connect(self.refresh_ticket_scripts)
        self.ui.treeWidget_ticket.itemSelectionChanged.connect(self.on_ticket_changed)
        self.ui.treeWidget_ticket.itemDoubleClicked.connect(self.on_ticket_double_clicked)
        self.ui.treeWidget_ticket.customContextMenuRequested.connect(self.show_ticket_context_menu)
        self.ui.pushButton_publish.clicked.connect(self.publish_button)
        self.ui.pushButton_open_dir.clicked.connect(self.open_publish_dir)

    # ------------------------------------------------------------
    # REFRESH
    # ------------------------------------------------------------
    def refresh_all(self, *_args):
        """pushButton_refresh — full refresh, same as closing and
        reopening the tool: repo info, plus a rescan of tickets/ off disk
        (scripts, icons and .json a teammate added/changed while this window
        was open) and the publish destination."""
        self.populate_repo_info()
        self.initialize_ticket_list_widget()

    def _register_scene_refresh_job(self):
        """Keeps this window in sync with whichever scene gets opened
        next (repo/category/ticket list can all depend on the active
        scene), without the artist having to close and reopen the tool.
        parent=WINDOW_OBJECT ties the job's lifetime to this window's own
        control, which uitools.deleteControl() (in __init__, on the next
        reopen) or a normal close already tears down — so Maya kills the
        job itself instead of it leaking/stacking across reopens."""
        cmds.scriptJob(event=["SceneOpened", self.refresh_all], parent=self.WINDOW_OBJECT, protected=True)
        cmds.scriptJob(event=["NewSceneOpened", self.refresh_all], parent=self.WINDOW_OBJECT, protected=True)

    # ------------------------------------------------------------
    # REPO INFO
    # ------------------------------------------------------------
    def populate_repo_info(self):
        """label_active_repo_name <- the repo currently active in UkoreHub
        (unrelated to label_repo_target_name, which reflects the resolved
        publish destination's own repo). Its path isn't shown — it's in the
        name's tooltip and pushButton_copy_repo_path copies it."""
        repo_name, repo_path = function.get_active_repo_display()
        self._active_repo_path = repo_path
        self.ui.label_active_repo_name.setText(repo_name or "(no active repo)")
        self.ui.label_active_repo_name.setToolTip(repo_path or "")
        self.ui.pushButton_copy_repo_path.setEnabled(bool(repo_path))

    def copy_active_repo_path(self):
        if self._active_repo_path:
            QtWidgets.QApplication.clipboard().setText(self._active_repo_path)

    # ------------------------------------------------------------
    # TICKETS
    # ------------------------------------------------------------
    def _setup_layout(self):
        """Left (tickets) : right (publish info) at 3:2, and stop the long,
        space-less target path from widening the right group — it wraps at
        path separators instead (see _set_publish_root_text)."""
        grid = self.ui.centralwidget.layout()
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        policy = self.ui.label_publish_root.sizePolicy()
        policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
        self.ui.label_publish_root.setSizePolicy(policy)

    def _set_publish_root_text(self, text):
        """label_publish_root shows `text` with zero-width spaces after each
        path separator so word-wrap can break a long path; the clean path
        stays in the tooltip."""
        zwsp = "\u200b"
        self.ui.label_publish_root.setText(text.replace("\\", "\\" + zwsp).replace("/", "/" + zwsp))
        self.ui.label_publish_root.setToolTip(text)

    def _setup_ticket_tree(self):
        tree = self.ui.treeWidget_ticket
        header = tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_TICKET, QtWidgets.QHeaderView.Stretch)
        program_col_width = tree.iconSize().width() + 24
        for col in (COL_FROM, COL_TO):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.Fixed)
            tree.setColumnWidth(col, program_col_width)
            tree.headerItem().setTextAlignment(col, QtCore.Qt.AlignCenter)
        for col in REPO_COLUMNS:
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)

    def initialize_ticket_list_widget(self):
        """Repopulates treeWidget_ticket from function.list_tickets(), one
        row per ticket: its thumbnail + name, then its From/To program
        icons and From/To repo. With checkBox_show_match_repo on, only
        tickets usable from the active repo are listed
        (function.ticket_matches_active_repo). Keeps whichever ticket was
        selected before across a refresh, otherwise the first row."""
        restore = self.get_selected_ticket()
        tickets = function.list_tickets()
        if self.ui.checkBox_show_match_repo.isChecked():
            tickets = [ticket for ticket in tickets if function.ticket_matches_active_repo(*ticket)]
        program_icons = dict(function.list_programs())

        tree = self.ui.treeWidget_ticket
        tree.blockSignals(True)
        tree.clear()
        restore_item = None
        for category, script_name in tickets:
            item = QtWidgets.QTreeWidgetItem(tree)
            item.setData(COL_TICKET, TICKET_ROLE, [category, script_name])
            item.setIcon(COL_TICKET, self._ticket_icon(category, script_name))
            self._apply_ticket_meta(item, program_icons)
            if restore_item is None or (category, script_name) == restore:
                restore_item = item

        if restore_item is not None:
            tree.setCurrentItem(restore_item)
        tree.blockSignals(False)

        self.on_ticket_changed()

    def _apply_ticket_meta(self, item, program_icons=None):
        """Fills item's label, dims it when its from_repo doesn't match the
        active repo, and fills the From/To program cells (icon only — the
        columns are narrow — name in the tooltip) and From/To repo cells."""
        if program_icons is None:
            program_icons = dict(function.list_programs())
        category, script_name = item.data(COL_TICKET, TICKET_ROLE)
        meta = function.get_ticket_meta(category, script_name)

        item.setText(COL_TICKET, category if category == script_name else "{}\n{}".format(category, script_name))

        blocked = function.check_ticket_from_repo(category, script_name)
        tooltip = function.ticket_key(category, script_name)
        item.setToolTip(COL_TICKET, tooltip + ("\n\n⚠ " + blocked if blocked else ""))
        grey = QtGui.QBrush(QtGui.QColor(120, 120, 120))
        item.setForeground(COL_TICKET, grey if blocked else QtGui.QBrush())

        for col, key in REPO_COLUMNS.items():
            repo_name = meta.get(key)
            if key == "from_repo":
                label, empty_text, empty_tip = "From Repo", "(any)", "any repo"
            else:
                label, empty_text, empty_tip = "To Repo", "(auto)", "first Custom Path match"
            item.setText(col, repo_name or empty_text)
            item.setToolTip(col, "{} : {}\n(double-click to edit)".format(label, repo_name or empty_tip))
            item.setForeground(col, grey if blocked or not repo_name else QtGui.QBrush())

        for col, key in PROGRAM_COLUMNS.items():
            program_name = meta.get(key)
            label = "From" if key == "from_program" else "To"
            if program_name:
                item.setIcon(col, self._program_icon(program_name, program_icons.get(program_name)))
                item.setToolTip(col, "{} : {}\n(double-click to change)".format(label, program_name))
            else:
                item.setIcon(col, QtGui.QIcon())
                item.setToolTip(col, "{} : (none)\n(double-click to set)".format(label))

    def _ticket_icon(self, category, script_name):
        """This ticket's own thumbnail if it has one, otherwise a plain
        tile with the ticket's initial."""
        path = function.thumbnail_path(category, script_name)
        return self._image_icon(path) or self._letter_icon(category)

    def _program_icon(self, program_name, icon_path):
        """A Program DB icon, or a letter tile when that Program has no icon
        (or UkoreHub hasn't pulled it to this machine yet)."""
        return self._image_icon(icon_path) or self._letter_icon(program_name, QtGui.QColor(60, 90, 120))

    def _image_icon(self, path):
        if path is None or not path.is_file():
            return None
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            return None
        size = self.ui.treeWidget_ticket.iconSize()
        return QtGui.QIcon(pixmap.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    def _letter_icon(self, text, color=None):
        size = self.ui.treeWidget_ticket.iconSize()
        pixmap = QtGui.QPixmap(size)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color or QtGui.QColor(80, 80, 80))
        painter.drawRoundedRect(pixmap.rect().adjusted(2, 2, -2, -2), 6, 6)
        font = painter.font()
        font.setPointSize(max(8, int(size.height() * 0.35)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(220, 220, 220))
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, text[:1].upper())
        painter.end()
        return QtGui.QIcon(pixmap)

    def get_selected_ticket(self):
        """(category, script_name) of the selected row, or None."""
        item = self.ui.treeWidget_ticket.currentItem()
        ticket = item.data(COL_TICKET, TICKET_ROLE) if item else None
        return tuple(ticket) if ticket else None

    def get_current_selected_script_name(self):
        ticket = self.get_selected_ticket()
        return ticket[1] if ticket else None

    def on_ticket_changed(self, *_args):
        ticket = self.get_selected_ticket()
        self._current_category = ticket[0] if ticket else None
        self.setWindowTitle("MayaPublisher — {}".format(ticket[0]) if ticket else "MayaPublisher")
        self.show_ticket_info()
        self.refresh_publish_destination()

    def show_ticket_info(self):
        """textBrowser_ticket_info <- the selected ticket's step-by-step
        <script>.md (function.get_ticket_info), rendered as Markdown."""
        browser = self.ui.textBrowser_ticket_info
        ticket = self.get_selected_ticket()
        if ticket is None:
            browser.clear()
            return

        text = function.get_ticket_info(*ticket)
        if text is None:
            text = "_No step info yet — right-click the ticket > Edit Info... to write `{}.md`._".format(ticket[1])
        if hasattr(browser, "setMarkdown"):  # Qt 5.14+ (Maya 2022+)
            browser.setMarkdown(text)
        else:
            browser.setPlainText(text)

    def edit_ticket_info(self):
        ticket = self.get_selected_ticket()
        if ticket is None:
            return
        os.startfile(str(function.ensure_ticket_info(*ticket)))

    def refresh_ticket_scripts(self, *_args):
        """checkBox_show_match_repo toggled — relists tickets with/without
        the active-repo filter."""
        self.initialize_ticket_list_widget()

    def select_ticket(self, category, script_name):
        tree = self.ui.treeWidget_ticket
        for row in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(row)
            ticket = item.data(COL_TICKET, TICKET_ROLE)
            if ticket and tuple(ticket) == (category, script_name):
                tree.setCurrentItem(item)
                return

    def create_ticket_script(self):
        result = cmds.promptDialog(
            title="New Ticket",
            message="Ticket name (also the Custom Path label it publishes to):",
            button=["Create", "Cancel"],
            defaultButton="Create",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Create":
            return

        name = cmds.promptDialog(query=True, text=True).strip()
        if not name or os.sep in name or "/" in name:
            cmds.confirmDialog(m="Invalid ticket name.", button=["Ok"])
            return

        try:
            category, path = function.create_ticket_script(name)
        except ValueError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        self.initialize_ticket_list_widget()
        self.select_ticket(category, path.stem)

    def edit_ticket_settings(self):
        """Right-click > Edit Ticket... — TicketSettingsDialog for the
        selected ticket's From/To program and From/To repo."""
        ticket = self.get_selected_ticket()
        item = self.ui.treeWidget_ticket.currentItem()
        if ticket is None or item is None:
            return

        programs = function.list_programs()
        dialog = TicketSettingsDialog(
            ticket,
            function.get_ticket_meta(*ticket),
            programs,
            function.list_project_repos(),
            function.list_target_repos(),
            self._program_icon,
            self.edit_selected_script,
            self,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        function.set_ticket_meta(ticket[0], ticket[1], dialog.values())
        if self.ui.checkBox_show_match_repo.isChecked() and not function.ticket_matches_active_repo(*ticket):
            self.initialize_ticket_list_widget()
            return
        self._apply_ticket_meta(item, dict(programs))
        self.refresh_publish_destination()

    def edit_selected_script(self):
        ticket = self.get_selected_ticket()
        if ticket is None:
            return

        path = function.script_path(*ticket)
        if not path.exists():
            cmds.confirmDialog(m="Script file no longer exists.", button=["Ok"])
            return
        os.startfile(str(path))

    def open_ticket_dir(self):
        ticket = self.get_selected_ticket()
        folder = function.category_dir(ticket[0]) if ticket else function.tickets_root()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def open_tickets_root(self):
        folder = function.tickets_root()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    # ------------------------------------------------------------
    # TICKET ICON / FROM-TO PROGRAM
    # ------------------------------------------------------------
    def set_ticket_icon(self):
        """Right-click > Set Icon from File... — pick an image file as the selected
        ticket's thumbnail (saved next to its script as <script>.png)."""
        ticket = self.get_selected_ticket()
        if ticket is None:
            return

        image_path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose Ticket Icon", "", "Images (*.png *.jpg *.jpeg *.bmp *.tga *.tif *.tiff)"
        )
        if image_path:
            self._apply_ticket_icon(ticket, image_path)

    def _apply_ticket_icon(self, ticket, image_path):
        try:
            function.set_ticket_thumbnail(ticket[0], ticket[1], image_path)
        except ValueError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return
        self._refresh_selected_ticket_icon()

    def clear_ticket_icon(self):
        ticket = self.get_selected_ticket()
        if ticket is None:
            return
        function.clear_ticket_thumbnail(*ticket)
        self._refresh_selected_ticket_icon()

    def _refresh_selected_ticket_icon(self):
        item = self.ui.treeWidget_ticket.currentItem()
        ticket = self.get_selected_ticket()
        if item is not None and ticket is not None:
            item.setIcon(COL_TICKET, self._ticket_icon(*ticket))

    def on_ticket_double_clicked(self, item, column):
        if column in PROGRAM_COLUMNS:
            self.ui.treeWidget_ticket.setCurrentItem(item)
            self.pick_ticket_program(PROGRAM_COLUMNS[column])
        elif column in REPO_COLUMNS:
            self.ui.treeWidget_ticket.setCurrentItem(item)
            self.edit_ticket_settings()

    def pick_ticket_program(self, key):
        """Opens ProgramPickerDialog over the active Project's Program DB and
        saves the pick as the selected ticket's from_program/to_program."""
        ticket = self.get_selected_ticket()
        item = self.ui.treeWidget_ticket.currentItem()
        if ticket is None or item is None:
            return

        programs = function.list_programs()
        if not programs:
            cmds.confirmDialog(
                m="No Programs found in this project's Program DB (UkoreHub > Project Editor).", button=["Ok"]
            )
            return

        current = function.get_ticket_meta(*ticket).get(key)
        title = "{} Program — {}".format("From" if key == "from_program" else "To", ticket[0])
        dialog = ProgramPickerDialog(programs, current, self._program_icon, title, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        function.set_ticket_meta(ticket[0], ticket[1], {key: dialog.selected_program()})
        self._apply_ticket_meta(item, dict(programs))

    def show_ticket_context_menu(self, pos):
        """Right-click on a row: that ticket's actions. Right-click on empty
        space below the rows: New Ticket... / Open Tickets Dir..."""
        tree = self.ui.treeWidget_ticket
        item = tree.itemAt(pos)
        menu = QtWidgets.QMenu(self)
        if item is None:
            menu.addAction("New Ticket...", self.create_ticket_script)
            menu.addAction("Open Tickets Dir...", self.open_tickets_root)
        else:
            tree.setCurrentItem(item)
            menu.addAction("Edit Ticket...", self.edit_ticket_settings)
            menu.addSeparator()
            menu.addAction("Set Icon from File...", self.set_ticket_icon)
            menu.addAction("Clear Icon", self.clear_ticket_icon)
            menu.addSeparator()
            menu.addAction("Edit Script...", self.edit_selected_script)
            menu.addAction("Edit Info...", self.edit_ticket_info)
            menu.addAction("Open Ticket Dir...", self.open_ticket_dir)
        menu.exec_(tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------
    # PUBLISH
    # ------------------------------------------------------------
    def refresh_publish_destination(self):
        script_name = self.get_current_selected_script_name()
        self.ui.label_publish_script_name.setText(script_name or "")

        if self._current_category is None or script_name is None:
            self._set_publish_root_text("Pick a ticket to see the publish destination.")
            self.ui.label_publish_prefix.setText("")
            self.ui.label_repo_target_name.setText("")
            self.ui.label_lastest_publish_version.setText("")
            self.ui.label_next_publish_version.setText("")
            return

        try:
            block_name = function.get_scene_block_name()
            publish_root, target_repo_name = function.get_publish_root_for_ticket(self._current_category, script_name)
        except RuntimeError as exc:
            self._set_publish_root_text(str(exc))
            self.ui.label_publish_prefix.setText("")
            self.ui.label_repo_target_name.setText("")
            self.ui.label_lastest_publish_version.setText("")
            self.ui.label_next_publish_version.setText("")
            return

        self._set_publish_root_text(os.path.join(publish_root, script_name, block_name))
        self.ui.label_publish_prefix.setText(block_name)
        self.ui.label_repo_target_name.setText(target_repo_name)

        latest_version, next_version = function.get_version_info(publish_root, script_name, block_name)
        self.ui.label_lastest_publish_version.setText("v{:03d}".format(latest_version) if latest_version else "—")
        self.ui.label_next_publish_version.setText("v{:03d}".format(next_version))

    def publish_button(self):
        script_name = self.get_current_selected_script_name()
        if self._current_category is None or script_name is None:
            return

        meta = function.get_ticket_meta(self._current_category, script_name)
        result = cmds.confirmDialog(
            m="โปรดตรวจสอบและยืนยันข้อมูลการพับบลิชนี้\n\nTicket : {}\nScript : {}\nRepo : {} → {}".format(
                self._current_category,
                script_name,
                meta.get("from_repo") or "(any)",
                self.ui.label_repo_target_name.text() or meta.get("to_repo") or "(auto)",
            ),
            button=["Ok", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Ok",
        )
        if result == "Cancel":
            return

        try:
            version_dir, version = function.publish(self._current_category, script_name)
        except RuntimeError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        self.ui.label_publish_result.setText("Publish สำเร็จ! : {} v{:03d}".format(script_name, version))
        self.ui.label_publish_result.show()
        self.refresh_publish_destination()

        note_text = self.ui.textBrowser_publish_info.toPlainText()
        if note_text:
            with open(os.path.join(version_dir, "note.txt"), "w") as f:
                f.write(note_text)

        result = cmds.confirmDialog(
            m="พับบลิชไฟล์สำเร็จแล้ว!\n : {}".format(version_dir),
            button=["Ok", "Open Publish Folder"],
            defaultButton="Ok",
            cancelButton="Ok",
        )
        if result == "Open Publish Folder":
            os.startfile(version_dir)

    def open_publish_dir(self):
        script_name = self.get_current_selected_script_name()
        if self._current_category is None or script_name is None:
            return

        try:
            block_name = function.get_scene_block_name()
            publish_root, _target_repo_name = function.get_publish_root_for_ticket(self._current_category, script_name)
        except RuntimeError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        base_dir = os.path.join(publish_root, script_name, block_name)
        versioning.make_sure_folder_exist(base_dir)
        os.startfile(base_dir)
