"""The "Diagnose References" tab next to the publish queue: for every scene
in the queue, which references it uses and whether each is the latest
version on disk (scene_refs.py). Scans run on a QThread, so a big queue or
a slow network drive doesn't freeze UkoreHub; they start automatically
when the tab is shown after the queue changed, or via Rescan."""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QMenu,
    QPushButton,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import scene_refs

COL_NAME, COL_FILE, COL_VERSION, COL_LATEST, COL_STATUS = range(5)
HEADERS = ("Scene / Namespace", "File", "Version", "Latest", "Status")
DATA_ROLE = Qt.ItemDataRole.UserRole
PROBLEM_STATUSES = (scene_refs.OUTDATED, scene_refs.MISSING)
_STATUS_TEXT = {
    scene_refs.LATEST: "Latest",
    scene_refs.OUTDATED: "Outdated",
    scene_refs.MISSING: "Missing",
    scene_refs.UNVERSIONED: "No version",
}


def interactive_columns(tree: QTreeWidget, widths: dict[int, int]) -> None:
    """Every column user-resizable (drag the divider; double-click it to
    fit) with a starting width — no Stretch column, since Qt won't let you
    drag one and it squeezes its neighbours (Status got too narrow). The
    tree scrolls sideways when the columns outgrow it."""
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumSectionSize(40)
    for column, width in widths.items():
        tree.setColumnWidth(column, width)


def _version_text(number) -> str:
    return f"v{number:03d}" if number is not None else ""


class _ScanThread(QThread):
    scene_scanned = Signal(object)  # scene_refs.SceneReport

    def __init__(self, scenes, project_root, workspace_root, parent=None):
        super().__init__(parent)
        self._scenes = list(scenes)
        self._project_root = project_root
        self._workspace_root = workspace_root

    def run(self) -> None:
        cache = scene_refs.new_dir_cache()
        for scene in self._scenes:
            if self.isInterruptionRequested():
                return
            self.scene_scanned.emit(scene_refs.scan_scene(scene, self._project_root, self._workspace_root, cache))


class DiagnoseTab(QWidget):
    def __init__(
        self,
        get_scenes: Callable[[], list[str]],
        get_roots: Callable[[], tuple[str | None, str | None]],
        show_in_explorer: Callable[[str], None] | None,
        is_busy: Callable[[], bool] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._get_scenes = get_scenes
        self._get_roots = get_roots
        self._show_in_explorer = show_in_explorer
        self._is_busy = is_busy or (lambda: False)
        self._project_root: str | None = None
        self._thread: _ScanThread | None = None
        self._dirty = True
        self._workspace_root: str | None = None
        self._expected = 0
        self._reports: list[scene_refs.SceneReport] = []

        style = self.style()
        self._icons = {
            scene_refs.LATEST: style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
            scene_refs.OUTDATED: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
            scene_refs.MISSING: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical),
            scene_refs.UNVERSIONED: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion),
        }

        self.button_rescan = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Rescan")
        self.button_rescan.clicked.connect(self.rescan)
        self.check_problems = QCheckBox("Only outdated / missing")
        self.check_problems.toggled.connect(self._apply_filter)
        self.button_update_all = QPushButton("Update All to Latest")
        self.button_update_all.setToolTip(
            "Point every outdated reference in every scene here at its latest version "
            "(rewrites the reference paths in the .ma files)."
        )
        self.button_update_all.setEnabled(False)
        self.button_update_all.clicked.connect(lambda: self._update(self._update_plan(self._reports)))
        self.label_summary = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.button_rescan)
        top.addWidget(self.button_update_all)
        top.addWidget(self.check_problems)
        top.addStretch(1)
        top.addWidget(self.label_summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        interactive_columns(self.tree, {COL_NAME: 220, COL_FILE: 380, COL_VERSION: 60, COL_LATEST: 60, COL_STATUS: 220})

        hint = QLabel(
            "Reads each queued .ma's references from the file itself (no Maya). "
            "Latest = the highest vNNN next to the referenced file right now."
        )
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        layout.addWidget(hint)

    # ------------------------------------------------------------
    # SCAN
    # ------------------------------------------------------------
    def mark_dirty(self) -> None:
        """The queue changed — rescan next time this tab is shown (or now, if it is)."""
        self._dirty = True
        if self.isVisible():
            self.rescan()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self.rescan()

    def rescan(self) -> None:
        self._stop_thread()
        self._dirty = False
        self.tree.clear()
        self._reports = []
        scenes = self._get_scenes()
        self._project_root, self._workspace_root = self._get_roots()
        self._expected = len(scenes)
        if not scenes:
            self.label_summary.setText("Queue is empty.")
            return
        self.label_summary.setText(f"Scanning 0 / {len(scenes)}...")
        self.button_rescan.setEnabled(False)
        self.button_update_all.setEnabled(False)
        self._thread = _ScanThread(scenes, self._project_root, self._workspace_root, self)
        self._thread.scene_scanned.connect(self._on_scene_scanned)
        self._thread.finished.connect(self._on_scan_finished)
        self._thread.start()

    def _stop_thread(self) -> None:
        if self._thread is not None:
            self._thread.scene_scanned.disconnect(self._on_scene_scanned)
            self._thread.finished.disconnect(self._on_scan_finished)
            self._thread.requestInterruption()
            self._thread.wait()
            self._thread.deleteLater()
            self._thread = None
            self.button_rescan.setEnabled(True)

    def shutdown(self) -> None:
        self._stop_thread()

    def background_threads(self) -> list:
        return [self._thread] if self._thread is not None else []

    def _on_scan_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
        self.button_rescan.setEnabled(True)
        self._update_summary(final=True)

    # ------------------------------------------------------------
    # RESULTS
    # ------------------------------------------------------------
    def _relative(self, path: str | None) -> str:
        if not path:
            return ""
        if self._workspace_root:
            try:
                return os.path.relpath(path, self._workspace_root).replace("\\", "/")
            except ValueError:
                pass
        return path

    def _on_scene_scanned(self, report: scene_refs.SceneReport) -> None:
        self._reports.append(report)
        scene_item = QTreeWidgetItem(self.tree)
        scene_item.setText(COL_NAME, os.path.basename(report.scene_path))
        scene_item.setToolTip(COL_NAME, report.scene_path)
        scene_item.setText(COL_FILE, self._relative(report.scene_path))
        scene_item.setData(COL_NAME, DATA_ROLE, ("scene", report.scene_path, report))

        if report.error:
            scene_item.setText(COL_STATUS, f"Can't read: {report.error}")
            scene_item.setIcon(COL_STATUS, self._icons[scene_refs.MISSING])
        else:
            for ref in report.references:
                self._add_reference_item(scene_item, ref)
            scene_item.setText(COL_STATUS, self._scene_summary(report))
            worst = next((s for s in (scene_refs.MISSING, scene_refs.OUTDATED) if report.count(s)), None)
            scene_item.setIcon(COL_STATUS, self._icons[worst or scene_refs.LATEST])
            scene_item.setExpanded(worst is not None)

        self._apply_filter()
        self._update_summary(final=False)

    def _add_reference_item(self, parent: QTreeWidgetItem, ref: scene_refs.ReferenceInfo) -> None:
        item = QTreeWidgetItem(parent)
        name = ref.namespace or ref.ref_node or "(no namespace)"
        if ref.nested and ":" in ref.ref_node:
            # A nested reference's own -ns is relative to its parent; its
            # reference node ("chr:propRN") carries the full path.
            name = ref.ref_node[:-2] if ref.ref_node.endswith("RN") else ref.ref_node
        item.setText(COL_NAME, name + ("  (nested)" if ref.nested else ""))
        item.setToolTip(COL_NAME, f"Reference node: {ref.ref_node}" if ref.ref_node else "")
        item.setText(COL_FILE, self._relative(ref.resolved_path) or ref.raw_path)
        tooltip = [f"In scene: {ref.raw_path}"]
        if ref.resolved_path:
            tooltip.append(f"On this machine: {ref.resolved_path}")
        if ref.rebased:
            tooltip.append("(the scene stores another machine's path — found under this workspace)")
        if ref.status == scene_refs.OUTDATED and ref.latest_path:
            tooltip.append(f"Latest: {ref.latest_path}")
        item.setToolTip(COL_FILE, "\n".join(tooltip))
        item.setText(COL_VERSION, _version_text(ref.version))
        item.setText(COL_LATEST, _version_text(ref.latest_version))
        status = _STATUS_TEXT[ref.status]
        if ref.status == scene_refs.OUTDATED:
            status += f" ({ref.latest_version - ref.version} behind)"
        item.setText(COL_STATUS, status)
        item.setIcon(COL_STATUS, self._icons[ref.status])
        item.setData(COL_NAME, DATA_ROLE, ("ref", ref.resolved_path, ref))

    @staticmethod
    def _scene_summary(report: scene_refs.SceneReport) -> str:
        total = len(report.references)
        if not total:
            return "No references"
        problems = [f"{report.count(s)} {_STATUS_TEXT[s].lower()}" for s in PROBLEM_STATUSES if report.count(s)]
        return f"{total} ref(s) — " + (", ".join(problems) if problems else "all up to date")

    def _update_summary(self, final: bool) -> None:
        outdated = sum(r.count(scene_refs.OUTDATED) for r in self._reports)
        missing = sum(r.count(scene_refs.MISSING) for r in self._reports)
        refs = sum(len(r.references) for r in self._reports)
        prefix = "" if final else f"Scanning {len(self._reports)} / {self._expected}... "
        self.label_summary.setText(f"{prefix}{len(self._reports)} scene(s), {refs} ref(s): {outdated} outdated, {missing} missing")
        updatable = sum(len(refs) for refs in self._update_plan(self._reports).values())
        self.button_update_all.setEnabled(final and updatable > 0)
        self.button_update_all.setText(f"Update All to Latest ({updatable})" if updatable else "Update All to Latest")

    def _apply_filter(self, *_args) -> None:
        only_problems = self.check_problems.isChecked()
        for index in range(self.tree.topLevelItemCount()):
            scene_item = self.tree.topLevelItem(index)
            visible_children = 0
            for child_index in range(scene_item.childCount()):
                child = scene_item.child(child_index)
                ref = child.data(COL_NAME, DATA_ROLE)[2]
                hide = only_problems and ref.status not in PROBLEM_STATUSES
                child.setHidden(hide)
                visible_children += not hide
            scene_item.setHidden(only_problems and visible_children == 0)

    # ------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------
    def _on_double_clicked(self, item, _column) -> None:
        _kind, path, _ref = item.data(COL_NAME, DATA_ROLE)
        if path and self._show_in_explorer is not None:
            self._show_in_explorer(path)

    def _show_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind, path, payload = item.data(COL_NAME, DATA_ROLE)
        ref = payload if kind == "ref" else None
        latest = ref.latest_path if ref is not None and ref.status == scene_refs.OUTDATED else None
        menu = QMenu(self)
        if kind == "scene":
            plan = self._update_plan([payload])
            action = menu.addAction(
                f"Update Outdated in This Scene ({sum(len(r) for r in plan.values())})", lambda: self._update(plan)
            )
            action.setEnabled(bool(plan) and self._thread is None)
            menu.addSeparator()
        else:
            scene_path = item.parent().data(COL_NAME, DATA_ROLE)[1]
            action = menu.addAction("Update to Latest", lambda: self._update({scene_path: {ref.ref_node: ref.latest_path}}))
            action.setEnabled(scene_refs.can_update(ref) and self._thread is None)
            if ref.nested:
                action.setToolTip("Nested — update the referenced file instead.")
            menu.addSeparator()
        can_show = self._show_in_explorer is not None
        action = menu.addAction("Show in Explorer", lambda: self._show_in_explorer(path))
        action.setEnabled(can_show and bool(path))
        action = menu.addAction("Show Latest Version in Explorer", lambda: self._show_in_explorer(latest))
        action.setEnabled(can_show and bool(latest and os.path.isfile(latest)))
        action.setVisible(ref is not None)
        menu.addSeparator()
        action = menu.addAction("Open Folder", lambda: os.startfile(os.path.dirname(path)))
        action.setEnabled(bool(path and os.path.isdir(os.path.dirname(path))))
        copy_text = path or (ref.raw_path if ref is not None else "")
        menu.addAction("Copy Path", lambda: QApplication.clipboard().setText(copy_text))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------
    @staticmethod
    def _update_plan(reports) -> dict[str, dict[str, str]]:
        """{scene_path: {ref_node: latest file}} for every updatable reference."""
        plan = {}
        for report in reports:
            refs = {ref.ref_node: ref.latest_path for ref in report.references if scene_refs.can_update(ref)}
            if refs:
                plan[report.scene_path] = refs
        return plan

    def _update(self, plan: dict[str, dict[str, str]]) -> None:
        if not plan:
            return
        if self._is_busy():
            QMessageBox.information(self, "Diagnose References", "A batch is publishing — update references after it finishes.")
            return
        ref_count = sum(len(refs) for refs in plan.values())
        answer = QMessageBox.question(
            self,
            "Diagnose References",
            f"Point {ref_count} reference(s) in {len(plan)} scene(s) at their latest version?\n\n"
            "This rewrites the reference paths inside those .ma files (like Replace Reference + Save in Maya). "
            "Paths inside the repo are written repo-relative. Anyone with one of these scenes open in Maya "
            "should reopen it before saving, or their save will undo this.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        updated, failures = 0, []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for scene_path, refs in plan.items():
                new_paths = {node: scene_refs.path_for_scene(path, self._project_root) for node, path in refs.items()}
                try:
                    updated += scene_refs.update_references(scene_path, new_paths, self._project_root)
                except OSError as exc:
                    failures.append(f"{os.path.basename(scene_path)}: {exc}")
        finally:
            QApplication.restoreOverrideCursor()

        self.rescan()
        if failures:
            QMessageBox.warning(
                self,
                "Diagnose References",
                f"Updated {updated} reference(s). These scenes couldn't be written:\n" + "\n".join(failures),
            )
        else:
            self.label_summary.setText(f"Updated {updated} reference(s) — rescanning...")
