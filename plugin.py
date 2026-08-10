from __future__ import annotations

from pathlib import Path

from interface.settings_tab_registry import CATEGORY_REPO, SettingsTabSpec
from plugins.repo_internal.MayaPublisher.interface.publish_mode_settings_page import PublishModeSettingsPage
from plugins.repo_internal.MayaPublisher.interface.publish_mode_store import migrate_legacy_data

TOOL_ID = "maya_publisher"
TOOL_LABEL = "MayaPublisher"
# Convention-only string match with plugins/repo_internal/maya_launcher/plugin.py
# — both resolve to the same active Project's plugin_data via
# ProjectPluginConfigStore, no coupling API needed. See that plugin's README
# for the full "contributions"/"labels" shape this writes into. Relies on
# plugins/repo_internal/MayaToolkit (UkoreMaya.core.Pipeline) and
# plugins/repo_internal/PublishApi also being enabled — not imported directly,
# just expected to be on the same merged PYTHONPATH at Maya launch time.
MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
ANY_VERSION = "*"


def register(api) -> None:
    migrate_legacy_data(api)
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

    api.register_settings_tab(
        SettingsTabSpec(
            key=TOOL_ID,
            label=TOOL_LABEL,
            order=126,
            page_factory=lambda: PublishModeSettingsPage(api=api),
            on_activated=lambda page: page.refresh(),
            category=CATEGORY_REPO,
        )
    )
