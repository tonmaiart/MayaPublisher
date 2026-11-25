"""Named batch-publish presets — a saved queue (scenes + each one's ticket)
plus the note, e.g. one preset per set of shots.

Stored per repo, shared with the whole team: Repo.plugin_data
["maya_publisher"]["batch_presets"] via api.metadata.get_repo_plugin_data /
set_repo_plugin_data — inside the repo's project blob, so it's cloud-synced
like everything else there (same place the old Ticket Visibility tab kept
its data). Shape:

    {"<name>": {"rows": [{"scene": str, "category": str | None, "script_name": str | None}],
                "note": str,
                "updated_at": "YYYY-MM-DDTHH:MM:SS"}}

`scene` is stored relative to the workspace root ("/"-separated) when the
file is under it, so a preset works on every machine whatever its
workspace folder; anything outside the workspace is stored absolute.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

PLUGIN_ID = "maya_publisher"
DATA_KEY = "batch_presets"


@dataclass
class PresetRow:
    scene_path: str  # absolute, on this machine
    category: str | None
    script_name: str | None


def _workspace_root(api) -> Path | None:
    root = api.local_config.workspace_root
    return Path(root) if root else None


def _to_stored(api, scene_path: str) -> str:
    root = _workspace_root(api)
    path = Path(scene_path)
    if root is not None:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return str(path)


def _from_stored(api, stored: str) -> str:
    path = Path(stored)
    if path.is_absolute():
        return os.path.normpath(stored)
    root = _workspace_root(api)
    return os.path.normpath(str((root or Path.cwd()) / path))


def _entry(api, project_id, repo_id) -> dict:
    return dict(api.metadata.get_repo_plugin_data(project_id, repo_id, PLUGIN_ID) or {})


def list_presets(api, project_id: str, repo_id: str) -> dict:
    """{name: preset dict}, as stored."""
    presets = _entry(api, project_id, repo_id).get(DATA_KEY, {})
    return presets if isinstance(presets, dict) else {}


def load_preset(api, project_id: str, repo_id: str, name: str) -> tuple[list[PresetRow], str]:
    """(rows with paths resolved for this machine, note). KeyError if gone."""
    preset = list_presets(api, project_id, repo_id)[name]
    rows = [
        PresetRow(_from_stored(api, row["scene"]), row.get("category"), row.get("script_name"))
        for row in preset.get("rows", [])
        if row.get("scene")
    ]
    return rows, preset.get("note", "")


def save_preset(api, project_id: str, repo_id: str, name: str, rows: list[PresetRow], note: str = "") -> None:
    """Creates or overwrites preset `name`."""
    entry = _entry(api, project_id, repo_id)
    presets = dict(entry.get(DATA_KEY, {}))
    presets[name] = {
        "rows": [
            {"scene": _to_stored(api, row.scene_path), "category": row.category, "script_name": row.script_name}
            for row in rows
        ],
        "note": note,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    entry[DATA_KEY] = presets
    api.metadata.set_repo_plugin_data(project_id, repo_id, PLUGIN_ID, entry)


def delete_preset(api, project_id: str, repo_id: str, name: str) -> None:
    entry = _entry(api, project_id, repo_id)
    presets = dict(entry.get(DATA_KEY, {}))
    if presets.pop(name, None) is None:
        return
    entry[DATA_KEY] = presets
    api.metadata.set_repo_plugin_data(project_id, repo_id, PLUGIN_ID, entry)
