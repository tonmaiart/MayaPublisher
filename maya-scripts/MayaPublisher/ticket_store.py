"""Ticket files on disk — the part of MayaPublisher that needs neither Maya
nor PublishApi, so UkoreHub's own process can load it too (by file path,
see the plugin root's tickets.py) as well as function.py inside Maya.

A ticket is tickets/<Category>/<script>.py plus optional siblings:
<script>.json (meta: from/to program, from/to repo, batch_mode),
<script>.md (step list), <script>.png (icon). See function.py's module
docstring for the history."""

from __future__ import annotations

import json
from pathlib import Path

THUMBNAIL_SUFFIX = ".png"

_TICKET_SCRIPT_TEMPLATE = '''"""Ticket script for MayaPublisher.

validate() runs when this ticket script is selected in MayaPublisher's
"Choose Ticket Scripts" list and Publish is pressed. Return True to let the
publish proceed, False to block it and show an artist-facing error. Raising
an exception also blocks the publish, with the exception message shown the
same way.

Accept one optional argument, `context`, to also do real publish work
(copy/export files) instead of just checking something — a script with a
plain validate() (no arguments) still works exactly like a check-only
script:

    context = {
        "version_dir": str,   # already-created destination folder for this publish
        "version": int,       # version number just created, e.g. 3 for v003
        "category": str,      # this ticket script's category, e.g. "Rig"
        "script_name": str,   # this ticket script's own file name (no .py)
        "tool_id": str,       # "maya_publisher"
        "from_program": str | None,  # this ticket's From program (Program DB name)
        "to_program": str | None,    # this ticket's To program (Program DB name)
        "from_repo": str | None,     # repo this ticket publishes from (None = any)
        "to_repo": str | None,       # repo this ticket publishes into (None = first Custom Path match)
        "batch_mode": str | None,    # "standalone" (headless mayapy) or "gui" (needs a viewport)
    }

Batch publish runs this in a freshly opened copy of the scene, which is
never saved back — so it's safe to modify the scene before exporting. A
"standalone" ticket has no viewport (no playblast/modelPanel); dialogs
(confirmDialog etc.) are auto-answered with their defaultButton.
"""

# import os
# from UkoreMaya.core import Pipeline


def validate(context):
    # Example — copy the current scene as a Maya Ascii file into this
    # publish's version folder. Replace with whatever this ticket script
    # actually needs to check and/or export, or remove the context
    # argument entirely for a check-only script.
    #
    # version = context["version"]
    # script_name = context["script_name"]
    # ma_path = os.path.join(context["version_dir"], f"{script_name}_v{version:03d}.ma")
    # Pipeline.export_maya_common(export_file_path=ma_path)

    return True
'''


def tickets_root() -> Path:
    """maya-scripts/MayaPublisher/tickets/ — bundled with this plugin's own
    git clone, not per active-repo."""
    return Path(__file__).resolve().parent / "tickets"


def list_categories() -> list[str]:
    """Every immediate subfolder of tickets_root(), sorted — these become
    comboBox_tickets_catagory's options."""
    root = tickets_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def category_dir(category: str) -> Path:
    return tickets_root() / category


def list_ticket_scripts(category: str) -> list[str]:
    """Every .py file's stem directly under tickets/<category>/, sorted.
    (The per-repo "Ticket Visibility" hidden list was removed 2026-10-11 —
    each ticket's own from_repo + the window's "Show match current repo"
    filter replace it, see ticket_matches_active_repo().)"""
    folder = category_dir(category)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.iterdir() if p.suffix == ".py" and p.stem != "__init__")


def list_tickets() -> list[tuple[str, str]]:
    """Every (category, script_name) pair across all categories,
    in category then script order — one row each in the flat ticket
    list."""
    return [(category, name) for category in list_categories() for name in list_ticket_scripts(category)]


def ticket_key(category: str, script_name: str) -> str:
    return f"{category}/{script_name}"


def script_path(category: str, script_name: str) -> Path:
    return category_dir(category) / f"{script_name}.py"


def thumbnail_path(category: str, script_name: str) -> Path:
    """tickets/<category>/<script_name>.png — may not exist."""
    return category_dir(category) / f"{script_name}{THUMBNAIL_SUFFIX}"


def clear_ticket_thumbnail(category: str, script_name: str) -> None:
    path = thumbnail_path(category, script_name)
    if path.is_file():
        path.unlink()


_TICKET_INFO_TEMPLATE = """**{name}** — one-line summary of what this ticket publishes

1. First step validate() does
2. Next step
3. ...
"""


def info_path(category: str, script_name: str) -> Path:
    """tickets/<category>/<script_name>.md — the ticket's numbered
    step-by-step description (Markdown), shown under the ticket list.
    Hand-written next to the script; keep it in sync when the script's
    steps change."""
    return category_dir(category) / f"{script_name}.md"


def get_ticket_info(category: str, script_name: str) -> str | None:
    path = info_path(category, script_name)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def ensure_ticket_info(category: str, script_name: str) -> Path:
    """info_path(), seeded from _TICKET_INFO_TEMPLATE if it doesn't exist
    yet — never overwrites an existing one."""
    path = info_path(category, script_name)
    if not path.exists():
        path.write_text(_TICKET_INFO_TEMPLATE.format(name=category), encoding="utf-8")
    return path


TICKET_PROGRAM_KEYS = ("from_program", "to_program")
TICKET_REPO_KEYS = ("from_repo", "to_repo")
TICKET_BATCH_MODE_KEY = "batch_mode"
TICKET_META_KEYS = TICKET_PROGRAM_KEYS + TICKET_REPO_KEYS + (TICKET_BATCH_MODE_KEY,)

# How batch_runner.py runs a ticket: "standalone" = headless mayapy (no
# viewport, fast, several in parallel); "gui" = a full maya.exe window, for a
# ticket that needs a viewport (playblast, modelPanel, ...). Missing/unknown
# falls back to BATCH_MODE_DEFAULT.
BATCH_MODE_STANDALONE = "standalone"
BATCH_MODE_GUI = "gui"
BATCH_MODES = (BATCH_MODE_STANDALONE, BATCH_MODE_GUI)
BATCH_MODE_DEFAULT = BATCH_MODE_STANDALONE


def ticket_meta_path(category: str, script_name: str) -> Path:
    """tickets/<category>/<script_name>.json — per-ticket metadata
    (from_program/to_program/from_repo/to_repo), committed alongside the
    script itself."""
    return category_dir(category) / f"{script_name}.json"


def get_ticket_meta(category: str, script_name: str) -> dict:
    path = ticket_meta_path(category, script_name)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_ticket_batch_mode(category: str, script_name: str) -> str:
    """This ticket's batch_mode (one of BATCH_MODES), BATCH_MODE_DEFAULT if
    its .json doesn't set a valid one."""
    mode = get_ticket_meta(category, script_name).get(TICKET_BATCH_MODE_KEY)
    return mode if mode in BATCH_MODES else BATCH_MODE_DEFAULT


def set_ticket_meta(category: str, script_name: str, values: dict) -> None:
    """Sets (or clears, with None/"") any of TICKET_META_KEYS on this
    ticket. Programs and repos are stored by **name**, not id, since
    tickets are studio-wide while each Project keeps its own Program DB /
    repos with their own ids. Removes the .json once nothing's left."""
    meta = get_ticket_meta(category, script_name)
    for key, value in values.items():
        if key not in TICKET_META_KEYS:
            raise ValueError(f"Unknown ticket metadata key: {key}")
        if value:
            meta[key] = value
        else:
            meta.pop(key, None)

    path = ticket_meta_path(category, script_name)
    if meta:
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    elif path.is_file():
        path.unlink()

def create_ticket_script(name: str) -> tuple[str, Path]:
    """Seeds a new ticket — tickets/<name>/<name>.py from
    _TICKET_SCRIPT_TEMPLATE — and returns (category, path). The folder name
    doubles as the category, so a Custom Path labeled `name` is what makes
    it publishable. Raises ValueError if that folder already holds a
    script with this name — never silently overwrites a TD's existing
    work."""
    folder = category_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    if path.exists():
        raise ValueError(f"A ticket named '{name}' already exists.")
    path.write_text(_TICKET_SCRIPT_TEMPLATE, encoding="utf-8")
    ensure_ticket_info(name, name)
    return name, path
