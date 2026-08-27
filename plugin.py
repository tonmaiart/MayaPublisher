from __future__ import annotations

import importlib.util
from pathlib import Path

from plugin_api import CATEGORY_REPO, SettingsTabSpec

TOOL_ID = "maya_publisher"
TOOL_LABEL = "MayaPublisher"
# Convention-only string match with cache/plugins/maya_launcher/plugin.py and
# cache/plugins/PublishApi/plugin.py — both resolve to the same active
# Project's plugin_data via ProjectPluginConfigStore, no coupling API needed.
# Relies on cache/plugins/MayaToolkit (UkoreMaya.core.Pipeline) and
# cache/plugins/PublishApi also being enabled — not imported directly, just
# expected to be on the same merged PYTHONPATH at Maya launch time. Category
# selection itself still happens entirely in the Maya-side window's
# comboBox_tickets_catagory (see maya-scripts/MayaPublisher/interface.py and
# this plugin's README) — the one UkoreHub-side tab this plugin does
# contribute (below) only controls per-ticket visibility, not category.
MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
ANY_VERSION = "*"
TICKET_VISIBILITY_SETTINGS_KEY = "maya_publisher_ticket_visibility"


def _load_ticket_visibility_page_class(tool_root: Path):
    """ticket_visibility_settings_page.py lives beside this file but is
    loaded by file path, not a normal import — a cache/plugins/ folder
    isn't guaranteed to sit under a real plugins.* package, so sibling
    files can't use a dotted import (see
    developer/app/docs/plugins-guide.md's "Working on a single plugin"
    section)."""
    spec = importlib.util.spec_from_file_location(
        "maya_publisher_ticket_visibility_settings_page",
        tool_root / "ticket_visibility_settings_page.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TicketVisibilitySettingsPage


def register(api) -> None:
    tool_root = Path(__file__).resolve().parent

    ticket_visibility_page_cls = _load_ticket_visibility_page_class(tool_root)
    api.register_settings_tab(
        SettingsTabSpec(
            key=TICKET_VISIBILITY_SETTINGS_KEY,
            label="Ticket Visibility",
            order=50,
            page_factory=lambda: ticket_visibility_page_cls(
                metadata_store=api.metadata,
                get_repo_context=lambda: api.repo_context,
                plugin_root=tool_root,
            ),
            on_activated=lambda widget: widget.refresh(),
            category=CATEGORY_REPO,
        )
    )

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
