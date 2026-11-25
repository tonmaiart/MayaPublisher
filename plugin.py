from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QStyle

from plugin_api import SectionSpec

try:
    from plugin_api import FileContextActionSpec
except ImportError:  # UkoreHub older than the file-context-action registry (2026-10-18)
    FileContextActionSpec = None

# Standalone-repo import fix (same as maya_launcher/plugin.py) — makes the
# uniquely-named maya_publisher_batch package importable.
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

TOOL_ID = "maya_publisher"
TOOL_LABEL = "MayaPublisher"
SECTION_LABEL = "Maya Publisher"
SECTION_ORDER = 60
# Convention-only string match with cache/plugins/maya_launcher/plugin.py and
# cache/plugins/PublishApi/plugin.py — both resolve to the same active
# Project's plugin_data via ProjectPluginConfigStore, no coupling API needed.
# Relies on cache/plugins/MayaToolkit (UkoreMaya.core.Pipeline) and
# cache/plugins/PublishApi also being enabled — not imported directly, just
# expected to be on the same merged PYTHONPATH at Maya launch time.
#
# 2026-10-18: the "Maya Publisher" section (maya_publisher_batch/batch_page.py)
# is the UkoreHub-side batch publisher that replaces the Maya window — it
# runs maya-scripts/MayaPublisher/batch_runner.py in mayapy/maya.exe per
# scene. The Maya-side window + its menu hook below stay until that window
# is removed.
MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
ANY_VERSION = "*"


def _register_section(api) -> None:
    holder = {}

    def page_factory():
        from maya_publisher_batch.batch_page import MayaPublisherPage

        holder["page"] = MayaPublisherPage(api)
        return holder["page"]

    def on_repo_changed(_context) -> None:
        page = holder.get("page")
        if page is not None:
            page.refresh_context()

    def on_app_close(_context) -> None:
        page = holder.get("page")
        if page is not None:
            page.shutdown()

    def wire(page, host) -> None:
        holder["host"] = host
        page.ui_commands = host

    def explorer_action_matches(paths) -> bool:
        # Only where the section itself is shown (repo enables this plugin);
        # kept cheap — a folder isn't scanned until the action is clicked.
        context = api.repo_context
        if context is None or TOOL_ID not in context.required_plugin_ids:
            return False
        return any(path.is_dir() or path.suffix.lower() == ".ma" for path in paths)

    def add_from_explorer(paths) -> None:
        page, host = holder.get("page"), holder.get("host")
        if page is None:
            return
        if page.is_running():
            QMessageBox.information(page, SECTION_LABEL, "A batch is publishing — add scenes after it finishes.")
            return
        scenes = page.collect_scenes(paths)
        if not scenes:
            QMessageBox.information(page, SECTION_LABEL, "No .ma files there.")
            return
        added = page.add_paths(paths)
        # Stays in Explorer (no navigate_and_focus) so the artist can keep
        # adding; the new rows are just pre-selected for when they switch over.
        page.browse_to_path(Path(scenes[0]))
        if host is not None:
            skipped = len(scenes) - added
            message = f"Added {added} scene(s) to the Maya publish queue"
            host.set_status_message(message + (f" ({skipped} already queued)" if skipped else ""))

    api.register_section(
        SectionSpec(
            key=TOOL_ID,
            label=SECTION_LABEL,
            order=SECTION_ORDER,
            page_factory=page_factory,
            standard_icon=QStyle.StandardPixmap.SP_ArrowUp,
            wire=wire,
            background_threads=lambda page: page.diagnose_tab.background_threads(),
        )
    )
    if FileContextActionSpec is not None and hasattr(api, "register_file_context_action"):
        api.register_file_context_action(
            FileContextActionSpec(
                key=f"{TOOL_ID}_add_to_queue",
                label="Add to Maya Publish Queue",
                order=50,
                match=explorer_action_matches,
                run=add_from_explorer,
            )
        )

        def run_agent_requests(paths) -> None:
            from maya_publisher_batch import agent_batch

            for path in paths:
                agent_batch.run_request(api, path)

        # match() is always False so it never shows in Explorer's menu: this is
        # the entry point UkoreHub's Agent Bridge `file_action` command calls by
        # key, since plugins can't import each other.
        api.register_file_context_action(
            FileContextActionSpec(
                key="maya_publisher_agent_batch",
                label="Run Maya publish request (agent)",
                order=999,
                match=lambda _paths: False,
                run=run_agent_requests,
            )
        )
    api.on_repo_changed(on_repo_changed)
    api.on_app_close(on_app_close)


def register(api) -> None:
    _register_section(api)
    tool_root = _PLUGIN_DIR

    bridge = api.project_plugin_config_store(MAYA_ENV_BRIDGE_PLUGIN_ID)
    if bridge is None:
        return
    contributions = bridge.get("contributions", {})
    contributions[TOOL_ID] = {
        "PYTHONPATH": {ANY_VERSION: [str(tool_root / "maya-scripts")]},
    }
    bridge.set("contributions", contributions)
    labels = bridge.get("labels", {})
    labels[TOOL_ID] = TOOL_LABEL
    bridge.set("labels", labels)

    # Auto-import MayaPublisher right after Maya opens a file so its
    # UkoreMenu.register_item() call (maya-scripts/MayaPublisher/__init__.py)
    # runs before UkoreMenu itself rebuilds the menu (order 99) — same
    # convention as UkoreReferenceEditor/plugin.py. Without this, the "Maya
    # Publisher..." menu item wouldn't appear until something else happened
    # to import MayaPublisher first (the bug UkoreMenu's README documents
    # historically affecting MayaFileBrowser).
    hooks = bridge.get("launch_hooks", {})
    hooks[TOOL_ID] = {
        "order": 10,
        "post_open_mel": 'python("try:\\n    import MayaPublisher\\nexcept ImportError:\\n    pass");',
    }
    bridge.set("launch_hooks", hooks)
