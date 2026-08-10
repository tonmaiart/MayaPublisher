# ================================================
#  MAYA PUBLISHER — MAYA
# ================================================

# This tool created by Natchapon Srisuk, only for Ukore Studio's projects.
# contact : natchapon.18851@gmail.com

# Merges what used to be three separate tools' own MainWindow
# (RigPublisher/ModelPublisher/AnimationPublisher's interface.py, which
# were already ~identical) into one, parameterized by the active repo's
# configured Publish Mode (Repository Setting > MayaPublisher) instead of
# each mode having its own plugin/window. Tickets are user-managed
# (PublishApi.tickets) — each ticket has its own Publish Path and its own
# validation scripts, managed via the "Manage Tickets..." button below
# (PublishApi.ticket_manager_dialog).
# ================================================

import os
import shutil
from importlib import reload

import maya.cmds as cmds
from MayaPublisher import function
from PublishApi import tickets, versioning
from PublishApi.ticket_manager_dialog import TicketManagerDialog
from tmlib.module.PySide import QtGui, QtWidgets
from tmlib.ui.interface_template import ToolkitWindow
from UkoreMaya.core import Pipeline

reload(function)


class MainWindow(ToolkitWindow):
    def __init__(self):
        super(MainWindow, self).__init__(os.path.basename(os.path.dirname(__file__)))

        self.snapshot_path = None
        self._tickets = []
        self._mode = function.get_publish_mode()

        if self._mode is not None:
            self.setWindowTitle("MayaPublisher — {}".format(function.MODE_LABELS[self._mode]))

        self._add_manage_tickets_button()
        self.initialize_ticket_list_widget()
        self.connect_signals()
        self.refresh_publish_destination()

    # ------------------------------------------------------------
    # SIGNALS
    # ------------------------------------------------------------
    def connect_signals(self):
        self.ui.listWidget_ticket.itemSelectionChanged.connect(self.refresh_publish_destination)
        self.ui.pushButton_publish.clicked.connect(self.publish_button)
        self.ui.pushButton_open_dir.clicked.connect(self.open_publish_dir)
        self.ui.pushButton_take_a_snapshot.clicked.connect(self.take_a_snapshot)

    def _add_manage_tickets_button(self):
        """Inserted in code rather than in ui.ui — avoids hand-editing the
        Designer XML for a button this window needs."""
        self.pushButton_manage_tickets = QtWidgets.QPushButton("Manage Tickets...")
        self.pushButton_manage_tickets.clicked.connect(self.open_ticket_manager)
        self.ui.groupBox_ticket.layout().addWidget(self.pushButton_manage_tickets)

    def open_ticket_manager(self):
        if self._mode is None:
            cmds.confirmDialog(
                m="This repo has no Publish Mode configured yet. Ask a studio admin to set one "
                "under Repository Setting > MayaPublisher.",
                button=["Ok"],
            )
            return

        dialog = TicketManagerDialog(
            self,
            tool_id=function.TOOL_ID,
            tool_label="MayaPublisher — {}".format(function.MODE_LABELS[self._mode]),
            show_export_type=(self._mode == "animation"),
            scripts_tool_id=function.MODE_TOOL_IDS[self._mode],
        )
        dialog.exec_()
        self.initialize_ticket_list_widget()
        self.refresh_publish_destination()

    def initialize_ticket_list_widget(self):
        """Repopulates from PublishApi.tickets, keeping whichever ticket
        was selected before (by id) across a Manage Tickets... round trip.
        Stays empty if this repo has no Publish Mode configured yet — see
        refresh_publish_destination for the hint shown in that case."""
        current_id = self.get_current_selected_ticket_id()
        self._tickets = tickets.list_tickets(function.TOOL_ID) if self._mode is not None else []

        self.ui.listWidget_ticket.blockSignals(True)
        self.ui.listWidget_ticket.clear()
        for ticket in self._tickets:
            self.ui.listWidget_ticket.addItem(ticket["name"])

        restore_row = 0
        for index, ticket in enumerate(self._tickets):
            if ticket["id"] == current_id:
                restore_row = index
                break
        if self._tickets:
            self.ui.listWidget_ticket.setCurrentRow(restore_row)
        self.ui.listWidget_ticket.blockSignals(False)

    def get_current_selected_ticket_id(self):
        ticket = self.get_current_selected_ticket()
        return ticket["id"] if ticket else None

    def get_current_selected_ticket(self):
        row = self.ui.listWidget_ticket.currentRow()
        if 0 <= row < len(self._tickets):
            return self._tickets[row]
        return None

    def refresh_publish_destination(self):
        if self._mode is None:
            self.ui.label_publish_root.setText(
                "This repo has no Publish Mode set — ask a studio admin to set it under "
                "Repository Setting > MayaPublisher."
            )
            self.ui.label_job_name.setText("")
            self.ui.label_job_task.setText("")
            self.ui.label_version_publish.setText("")
            return

        ticket = self.get_current_selected_ticket()

        if ticket is None:
            self.ui.label_publish_root.setText("No tickets yet — click 'Manage Tickets...' to create one.")
            self.ui.label_job_name.setText("")
            self.ui.label_job_task.setText("")
            self.ui.label_version_publish.setText("")
            return

        try:
            publish_root = tickets.get_publish_root_for_ticket(function.TOOL_ID, ticket)
        except RuntimeError as exc:
            self.ui.label_publish_root.setText(str(exc))
            self.ui.label_job_name.setText("")
            self.ui.label_job_task.setText(ticket["name"])
            self.ui.label_version_publish.setText("")
            return

        self.ui.label_publish_root.setText(publish_root)
        self.ui.label_job_name.setText(os.path.basename(publish_root))
        self.ui.label_job_task.setText(ticket["name"])

        next_version = versioning.get_new_version(os.path.join(publish_root, ticket["folder_name"]))
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
        ticket = self.get_current_selected_ticket()
        if ticket is None:
            return

        result = cmds.confirmDialog(
            m="โปรดตรวจสอบและยืนยันข้อมูลการพับบลิชนี้\n\nTicket : {}".format(ticket["name"]),
            button=["Ok", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Ok",
        )
        if result == "Cancel":
            return

        try:
            version_dir, version = function.publish(ticket)
        except RuntimeError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        self.ui.label_publish_result.setText("Publish สำเร็จ! : {} v{:03d}".format(ticket["name"], version))
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
        ticket = self.get_current_selected_ticket()
        if ticket is None:
            return

        try:
            publish_root = tickets.get_publish_root_for_ticket(function.TOOL_ID, ticket)
        except RuntimeError as exc:
            cmds.confirmDialog(m=str(exc), button=["Ok"])
            return

        base_dir = os.path.join(publish_root, ticket["folder_name"])
        versioning.make_sure_folder_exist(base_dir)
        os.startfile(base_dir)
