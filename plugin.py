from __future__ import annotations

from pathlib import Path

TOOL_ID = "maya_publisher"
TOOL_LABEL = "MayaPublisher"
# Convention-only string match with cache/plugins/maya_launcher/plugin.py and
# cache/plugins/PublishApi/plugin.py — both resolve to the same active
# Project's plugin_data via ProjectPluginConfigStore, no coupling API needed.
# Relies on cache/plugins/MayaToolkit (UkoreMaya.core.Pipeline) and
# cache/plugins/PublishApi also being enabled — not imported directly, just
# expected to be on the same merged PYTHONPATH at Maya launch time. No
# UkoreHub-side UI of its own anymore — category selection now happens
# entirely in the Maya-side window's comboBox_tickets_catagory (see
# maya-scripts/MayaPublisher/interface.py and this plugin's README).
MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
ANY_VERSION = "*"


def register(api) -> None:
    tool_root = Path(__file__).resolve().parent

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
