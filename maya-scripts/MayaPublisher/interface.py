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
# in <repo_root>/.MayaPublisher via pushButton_catagory_config. Each ticket
# is now a plain .py file (function.list_ticket_scripts()) instead of a
# JSON-managed ticket dict — no more "Manage Tickets..." dialog.
# ================================================

import os
import shutil

from importlib import reload

import maya.cmds as cmds
from MayaPublisher import function
from PublishApi import versioning
from tmlib.module.PySide import QtGui
from tmlib.ui.interface_template import ToolkitWindow
from UkoreMaya.core import Pipeline

reload(function)


class MainWindow(ToolkitWindow):
    def __init__(self):
        super(MainWindow, self).__init__(os.path.basename(os.path.dirname(__file__)))

        self.snapshot_path = None
        self._current_category = None
        self._scripts = []

        self.connect_signals()
        self.populate_categories()

    # ------------------------------------------------------------
    # SIGNALS
    # ------------------------------------------------------------
    def connect_signals(self):
        self.ui.comboBox_tickets_catagory.currentIndexChanged.connect(self.on_category_changed)
        self.ui.pushButton_catagory_config.clicked.connect(self.save_category_config)
        self.ui.pushButton_create_ticket_scripts.clicked.connect(self.create_ticket_script)
        self.ui.pushButton_edit_selected_script.clicked.connect(self.edit_selected_script)
        self.ui.pushButton_open_ticket_dir.clicked.connect(self.open_ticket_dir)
        self.ui.listWidget_ticket.itemSelectionChanged.connect(self.refresh_publish_destination)
        self.ui.pushButton_publish.clicked.connect(self.publish_button)
        self.ui.pushButton_open_dir.clicked.connect(self.open_publish_dir)
        self.ui.pushButton_take_a_snapshot.clicked.connect(self.take_a_snapshot)

    # ------------------------------------------------------------
    # CATEGORY
    # ------------------------------------------------------------
    def populate_categories(self):
        """comboBox_tickets_catagory <- tickets/ subfolder names. Restores
        this repo's saved category (.MayaPublisher) if it's still present
        on disk, otherwise falls back to the first one found."""
        categories = function.list_categories()

        self.ui.comboBox_tickets_catagory.blockSignals(True)
        self.ui.comboBox_tickets_catagory.clear()
        self.ui.comboBox_tickets_catagory.addItems(categories)

        default_category = function.get_default_category()
        if default_category in categories:
            self.ui.comboBox_tickets_catagory.setCurrentText(default_category)
        self.ui.comboBox_tickets_catagory.blockSignals(False)

        self.on_category_changed()

    def on_category_changed(self, *_args):
        category = self.ui.comboBox_tickets_catagory.currentText()
        self._current_category = category or None
        self.setWindowTitle(
            "MayaPublisher — {}".format(self._current_category) if self._current_category else "MayaPublisher"
        )
        self.initialize_ticket_list_widget()
        self.refresh_publish_destination()

    def save_category_config(self):
        if self._current_category is None:
            return
        try:
            function.set_default_category(self._current_category)
        except RuntimeError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return
        cmds.confirmDialog(
            m="Saved '{}' as this repo's default category.".format(self._current_category), button=["Ok"]
        )

    # ------------------------------------------------------------
    # TICKET SCRIPTS
    # ------------------------------------------------------------
    def initialize_ticket_list_widget(self):
        """Repopulates from function.list_ticket_scripts(), keeping
        whichever script was selected before (by name) across a category
        switch/refresh. Stays empty if no category is selected."""
        current_name = self.get_current_selected_script_name()
        self._scripts = function.list_ticket_scripts(self._current_category) if self._current_category else []

        self.ui.listWidget_ticket.blockSignals(True)
        self.ui.listWidget_ticket.clear()
        for name in self._scripts:
            self.ui.listWidget_ticket.addItem(name)

        if self._scripts:
            restore_row = self._scripts.index(current_name) if current_name in self._scripts else 0
            self.ui.listWidget_ticket.setCurrentRow(restore_row)
        self.ui.listWidget_ticket.blockSignals(False)

    def get_current_selected_script_name(self):
        item = self.ui.listWidget_ticket.currentItem()
        return item.text() if item else None

    def select_script_by_name(self, name):
        for row in range(self.ui.listWidget_ticket.count()):
            if self.ui.listWidget_ticket.item(row).text() == name:
                self.ui.listWidget_ticket.setCurrentRow(row)
                return

    def create_ticket_script(self):
        if self._current_category is None:
            cmds.confirmDialog(m="Pick a category first.", button=["Ok"])
            return

        result = cmds.promptDialog(
            title="New Ticket Script",
            message="Script name ({}):".format(self._current_category),
            button=["Create", "Cancel"],
            defaultButton="Create",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Create":
            return

        name = cmds.promptDialog(query=True, text=True).strip()
        if not name or os.sep in name or "/" in name:
            cmds.confirmDialog(m="Invalid script name.", button=["Ok"])
            return

        try:
            path = function.create_ticket_script(self._current_category, name)
        except ValueError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        self.initialize_ticket_list_widget()
        self.select_script_by_name(path.stem)
        self.refresh_publish_destination()

    def edit_selected_script(self):
        script_name = self.get_current_selected_script_name()
        if self._current_category is None or script_name is None:
            return

        path = function.script_path(self._current_category, script_name)
        if not path.exists():
            cmds.confirmDialog(m="Script file no longer exists.", button=["Ok"])
            return
        os.startfile(str(path))

    def open_ticket_dir(self):
        if self._current_category is None:
            return
        folder = function.category_dir(self._current_category)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    # ------------------------------------------------------------
    # PUBLISH
    # ------------------------------------------------------------
    def refresh_publish_destination(self):
        script_name = self.get_current_selected_script_name()

        if self._current_category is None or script_name is None:
            self.ui.label_publish_root.setText("Pick a category and a ticket script to see the publish destination.")
            self.ui.label_job_name.setText("")
            self.ui.label_version_publish.setText("")
            return

        try:
            publish_root = function.get_publish_root_for_category(self._current_category)
        except RuntimeError as exc:
            self.ui.label_publish_root.setText(str(exc))
            self.ui.label_job_name.setText("")
            self.ui.label_version_publish.setText("")
            return

        self.ui.label_publish_root.setText(publish_root)
        self.ui.label_job_name.setText(os.path.basename(publish_root))

        next_version = versioning.get_new_version(os.path.join(publish_root, script_name))
        self.ui.label_version_publish.setText("v{:03d}".format(next_version))

    def take_a_snapshot(self):
        self.snapshot_path = Pipeline.playblast_screenshot_to_project_folder()

        pixmap = QtGui.QPixmap(self.snapshot_path)
        if pixmap.isNull():
            return

        btn = self.ui.pushButton_take_a_snapshot
        btn.setIcon(QtGui.QIcon(pixmap))
        btn.setIconSize(btn.size())

    def publish_button(self):
        script_name = self.get_current_selected_script_name()
        if self._current_category is None or script_name is None:
            return

        result = cmds.confirmDialog(
            m="โปรดตรวจสอบและยืนยันข้อมูลการพับบลิชนี้\n\nCategory : {}\nTicket Script : {}".format(
                self._current_category, script_name
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
        self.refresh_publish_destination()

        note_text = self.ui.textBrowser_publish_info.toPlainText()
        if note_text:
            with open(os.path.join(version_dir, "note.txt"), "w") as f:
                f.write(note_text)

        if self.snapshot_path:
            shutil.copyfile(self.snapshot_path, os.path.join(version_dir, "thumbnail.jpg"))

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
            publish_root = function.get_publish_root_for_category(self._current_category)
        except RuntimeError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        base_dir = os.path.join(publish_root, script_name)
        versioning.make_sure_folder_exist(base_dir)
        os.startfile(base_dir)
