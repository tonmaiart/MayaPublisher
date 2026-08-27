"""Publish logic for MayaPublisher.

**2026-08-19: Publish Mode / PublishApi.tickets.json retired.** This tool
used to have one repo-wide "Publish Mode" (Rig/Model/Animation) picked in a
UkoreHub-side Repository Setting tab, plus user-managed **tickets** stored
per repo in PublishApi's own <repo_root>/.ukorehub/maya_publisher_tickets.json
(publish_target + attached script_names, edited via PublishApi's
ticket_manager_dialog.TicketManagerDialog). Both are gone. A "ticket" is now
just a plain .py file — a **Ticket Script** — sitting under this plugin's own
bundled tickets_root() (maya-scripts/MayaPublisher/tickets/<Category>/), authored/
committed to MayaPublisher's own git history like PythonPipeline code, not
per-repo pipeline config. Category = tickets/ subfolder name (e.g. "Rig",
"Anim", "Model"), picked in the Maya window's comboBox_tickets_catagory
(interface.py); each repo remembers its own last-picked category in a
<repo_root>/.MayaPublisher JSON file (set via pushButton_catagory_config —
see set_default_category/get_default_category below).

Publish-target resolution no longer reads a per-ticket publish_target — it
matches the selected category's name against a Custom Path *label* declared
in Project Editor (PublishApi.repo_paths.get_custom_paths), checked first on
the active repo itself, then on every repo the active repo has a pipeline
connection to (PublishApi.repo_paths.get_pipeline_refs/resolve_ref) — see
get_publish_root_for_category(). This still leans entirely on PublishApi for
active-repo/pipeline metadata, per this plugin's own working convention (see
README.md).

publish() itself still never exports or copies a file on its own (unchanged
since 2026-08-05) — it only resolves the destination and creates the
versioned folder, then runs the selected Ticket Script's own validate()
with a `context` dict (version_dir, version, category, script_name,
tool_id). A script decides entirely for itself what to export/copy into
context["version_dir"], using UkoreMaya.core.Pipeline's export functions
directly if it wants to, or anything else. A Ticket Script with no
validate() defined, or one that does no file work, produces an **empty**
version folder — there is no default export.

**2026-08-19: per-asset/shot block-name subfolder.** publish() now inserts
an extra subfolder between the ticket script's own folder and its version
folder, named after the active scene's filename's first "_"-separated
block (get_scene_block_name(), e.g. "KBA030_rig_v012.ma" -> "KBA030") — so
the on-disk layout is `<publish_root>/<script_name>/<block_name>/vXXX`
instead of `<publish_root>/<script_name>/vXXX`. get_scene_block_name()
raises the same artist-facing RuntimeError publish() already raised for an
unsaved scene (now also covering a filename that yields no usable block
name), so Publish is refused outright when the current scene has never
been saved."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path

import maya.cmds as mc
from PublishApi import repo_paths, versioning

TOOL_ID = "maya_publisher"
CONFIG_FILE_NAME = ".MayaPublisher"

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
    }
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


def get_hidden_ticket_scripts(category: str) -> set[str]:
    """Ticket Script names this repo has hidden via its UkoreHub-side
    Repository Setting > MayaPublisher > "Ticket Visibility" tab
    (ticket_visibility_settings_page.py, this plugin's own root — not
    imported here, Maya only sees maya-scripts/). Reads Repo.plugin_data
    straight off disk the same way get_pipeline_refs()/get_custom_paths()
    in PublishApi.repo_paths do, since this Maya-side code has no live
    PluginAPI to go through. Empty set if there's no active repo or
    nothing's hidden for this category."""
    _project, repo, _repo_path = repo_paths.get_active_repo()
    if repo is None:
        return set()
    hidden_by_category = repo.plugin_data.get(TOOL_ID, {}).get("hidden_tickets", {})
    return set(hidden_by_category.get(category, []))


def list_ticket_scripts(category: str) -> list[str]:
    """Every .py file's stem directly under tickets/<category>/, sorted,
    minus any this repo has hidden (get_hidden_ticket_scripts) — these
    become the "Choose Ticket Scripts" list's rows."""
    folder = category_dir(category)
    if not folder.is_dir():
        return []
    hidden = get_hidden_ticket_scripts(category)
    return sorted(
        p.stem
        for p in folder.iterdir()
        if p.suffix == ".py" and p.stem != "__init__" and p.stem not in hidden
    )


def script_path(category: str, script_name: str) -> Path:
    return category_dir(category) / f"{script_name}.py"


def create_ticket_script(category: str, name: str) -> Path:
    """Seeds a new tickets/<category>/<name>.py from _TICKET_SCRIPT_TEMPLATE.
    Raises ValueError if a script with this name already exists under this
    category — never silently overwrites a TD's existing work."""
    folder = category_dir(category)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    if path.exists():
        raise ValueError(f"A ticket script named '{name}' already exists under '{category}'.")
    path.write_text(_TICKET_SCRIPT_TEMPLATE, encoding="utf-8")
    return path


def _config_path() -> Path | None:
    """<active repo root>/.MayaPublisher — None if there's no active repo."""
    _project, _repo, repo_path = repo_paths.get_active_repo()
    if repo_path is None:
        return None
    return repo_path / CONFIG_FILE_NAME


def get_default_category() -> str | None:
    """This repo's last-saved category (pushButton_catagory_config), or
    None if there's no active repo or nothing's been saved yet."""
    path = _config_path()
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("default_category")


def set_default_category(category: str) -> None:
    path = _config_path()
    if path is None:
        raise RuntimeError("No active repo selected in UkoreHub.")
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data["default_category"] = category
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_active_repo_display() -> tuple[str | None, str | None]:
    """(repo_name, repo_path) for the active repo in UkoreHub, or
    (None, None) if nothing's active — for label_active_repo_name/
    label_active_repo_path."""
    _project, repo, repo_path = repo_paths.get_active_repo()
    if repo is None:
        return None, None
    return repo.name, str(repo_path)


def get_scene_block_name() -> str:
    """First "_"-separated block of the active scene's filename (no
    extension), e.g. "KBA030_rig_v012.ma" -> "KBA030" — becomes the
    per-asset/shot subfolder created under the ticket script's own
    folder, right before the version folder. Raises RuntimeError (meant to
    be shown directly to the artist) if the scene hasn't been saved yet, or
    its filename doesn't yield a usable block name."""
    scene_path = mc.file(q=True, sn=True)
    if not scene_path:
        raise RuntimeError("กรุณาบันทึกไฟล์ก่อนดำเนินการ Publish!")

    block_name = os.path.splitext(os.path.basename(scene_path))[0].split("_")[0].strip()
    if not block_name:
        raise RuntimeError("ไม่สามารถระบุชื่อโฟลเดอร์ Publish จากชื่อไฟล์ปัจจุบันได้ กรุณาตรวจสอบชื่อไฟล์!")
    return block_name


def get_publish_root_for_category(category: str) -> tuple[str, str]:
    """(publish_root, target_repo_name) for this category — publish_root is
    the cloned path of whichever repo (the active one, or one it has a
    pipeline connection to) declares a Custom Path whose *label* matches
    `category` (case-insensitive), joined with that Custom Path's own
    relative path; target_repo_name is that same repo's name (for
    label_repo_target_name — may differ from the active repo when the match
    comes from a pipeline connection). Checks the active repo's own declared
    Custom Paths first, then every resolvable pipeline connection, in
    PublishApi.repo_paths.get_pipeline_refs() order. Raises RuntimeError with
    an artist-facing message if there's no active repo, or no repo/
    connection declares a matching Custom Path."""
    project, repo, repo_path = repo_paths.get_active_repo()
    if project is None:
        raise RuntimeError("No active repo selected in UkoreHub. Open UkoreHub, pick a project/repo, then try again.")

    candidates = [(project.id, repo.id, repo_path, repo.name)]
    for ref in repo_paths.get_pipeline_refs():
        resolved = repo_paths.resolve_ref(ref)
        if resolved is not None:
            ref_project, ref_repo, ref_repo_path = resolved
            candidates.append((ref_project.id, ref_repo.id, ref_repo_path, ref_repo.name))

    for target_project_id, target_repo_id, target_repo_path, target_repo_name in candidates:
        for custom_path in repo_paths.get_custom_paths(target_project_id, target_repo_id):
            if custom_path.get("label", "").strip().lower() == category.strip().lower():
                if not target_repo_path.is_dir():
                    raise RuntimeError(
                        f"Publish Path target repo for category '{category}' hasn't been cloned yet — "
                        "clone it in UkoreHub first."
                    )
                return str(target_repo_path / custom_path["path"].lstrip("/\\")), target_repo_name

    raise RuntimeError(
        f"No Custom Path named '{category}' found on this repo or its pipeline connections. "
        "Ask a studio admin to declare one in Project Editor."
    )


def get_version_info(publish_root: str, script_name: str, block_name: str) -> tuple[int | None, int]:
    """(latest_version, next_version) for this ticket script + block
    (asset/shot code)'s own publish subfolder — latest_version is None if
    nothing's been published there yet, for
    label_lastest_publish_version/label_next_publish_version."""
    next_version = versioning.get_new_version(os.path.join(publish_root, script_name, block_name))
    latest_version = next_version - 1 if next_version > 1 else None
    return latest_version, next_version


def run_ticket_script(category: str, script_name: str, context: dict) -> tuple[bool, str]:
    """Loads tickets/<category>/<script_name>.py (same
    importlib.util.spec_from_file_location + exec_module mechanism
    PublishApi.tickets.run_validation_scripts uses) and calls its
    validate(), passing `context` only if validate() accepts an argument.
    Returns (True, "") on success, (False, error_message) if the script is
    missing, fails to load, raises, or its validate() returns False/no
    validate() at all is treated as a pass-through."""
    path = script_path(category, script_name)
    if not path.exists():
        return False, f"{script_name}: no longer found under tickets/{category}/."

    module_name = f"maya_publisher_ticket_{category}_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken script must not crash publish()
        return False, f"{script_name}: failed to load ({exc})"

    validate = getattr(module, "validate", None)
    if validate is None:
        return True, ""

    try:
        wants_context = len(inspect.signature(validate).parameters) >= 1
        passed = validate(context) if wants_context else validate()
    except Exception as exc:  # noqa: BLE001 - same reasoning as the load try/except above
        return False, f"{script_name}: raised {exc}"

    if not passed:
        return False, f"{script_name}: validate() returned False"
    return True, ""


def publish(category: str, script_name: str):
    """Resolves this category's publish root/next version, then runs the
    selected Ticket Script (run_ticket_script) with a `context` dict
    (version_dir, version, category, script_name, tool_id) so it can
    export/copy whatever it decides belongs there — see the module
    docstring. Raises RuntimeError — meant to be shown directly to the
    artist — if the scene hasn't been saved (or its filename yields no
    usable block name), the category's Custom Path can't be resolved, or
    the Ticket Script blocks the publish (returns False/raises). Returns
    (version_dir, version_number)."""
    block_name = get_scene_block_name()
    publish_root, _target_repo_name = get_publish_root_for_category(category)
    version_dir, version = versioning.get_version_directory(os.path.join(publish_root, script_name), block_name)

    context = {
        "version_dir": version_dir,
        "version": version,
        "category": category,
        "script_name": script_name,
        "tool_id": TOOL_ID,
    }
    ok, message = run_ticket_script(category, script_name, context)
    if not ok:
        raise RuntimeError(message)

    return version_dir, version
