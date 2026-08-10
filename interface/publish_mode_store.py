from __future__ import annotations

from core.exceptions import NotFoundError

PLUGIN_ID = "maya_publisher"
_MODE_KEY = "publish_mode"

MODES = ("rig", "model", "animation")
MODE_LABELS = {"rig": "Rig", "model": "Model", "animation": "Animation"}


def get_publish_mode(api, project_id: str, repo_id: str) -> str | None:
    """This repo's configured Publish Mode ("rig"/"model"/"animation"), or
    None if a studio admin hasn't set one yet. Lives in this repo's own
    Repo.plugin_data["maya_publisher"] (core/models.py) — per-repo settings-
    page state, unlike PublishApi/tickets.py's ticket storage for tool_id
    "maya_publisher", which lives inside each repo's own .ukorehub/ folder
    instead (see that module's docstring)."""
    return api.metadata.get_repo_plugin_data(project_id, repo_id, PLUGIN_ID).get(_MODE_KEY)


def set_publish_mode(api, project_id: str, repo_id: str, mode: str) -> None:
    data = dict(api.metadata.get_repo_plugin_data(project_id, repo_id, PLUGIN_ID))
    data[_MODE_KEY] = mode
    api.metadata.set_repo_plugin_data(project_id, repo_id, PLUGIN_ID, data)


def migrate_legacy_data(api) -> None:
    """One-time cutover from the old data/plugins/core/maya_publisher.json
    PluginConfigStore blob's "publish_mode" key into each repo's own
    Repo.plugin_data. Only clears that one key afterward — PublishApi's
    tickets.py._migrate_from_shared_store() still reads this same file's
    "tickets"/"repo_publish_target" keys for its own unrelated migration,
    so the rest of the blob must stay intact. Safe to call on every
    register(), not just once — a previous successful run leaves this key
    empty, so there's nothing left to migrate."""
    legacy_store = api.plugin_config_store(PLUGIN_ID, shared=True)
    modes = legacy_store.get(_MODE_KEY, {})
    if not modes:
        return
    for repo_key, mode in modes.items():
        project_id, _, repo_id = repo_key.partition(":")
        try:
            set_publish_mode(api, project_id, repo_id, mode)
        except NotFoundError:
            pass  # project/repo no longer exists — drop the stale entry
    legacy_store.set(_MODE_KEY, {})
