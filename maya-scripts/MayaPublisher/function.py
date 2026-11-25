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
"Anim", "Model"). See the 2026-10-11 note at the bottom for how tickets
are picked in the Maya window now.

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
been saved.

**2026-10-11: flat ticket list + per-ticket thumbnail.** The Maya window no
longer has a category combo box with a script list under it — every
(category, script) pair under tickets/ is now listed as one flat row
(list_tickets()), so picking a row picks exactly that ticket. On disk
nothing moved: a ticket is still tickets/<Category>/<script>.py, and
<Category> still resolves the publish root via its Custom Path label. Each
ticket can carry an optional icon next to its script,
tickets/<Category>/<script>.png (thumbnail_path()/set_ticket_thumbnail()),
shown in the list. (The per-repo default ticket/category saved in
<repo_root>/.MayaPublisher was removed the same day — the window just
selects the first listed ticket; an old .MayaPublisher file is ignored.) A ticket can also name a From and To program
(tickets/<Category>/<script>.json, get_ticket_meta()/set_ticket_meta()),
picked by name from the active Project's UkoreHub Program DB
(list_programs()) and shown with that Program's icon — plus a From and To
repo (by name): from_repo refuses the ticket from any other active repo
(check_ticket_from_repo()), to_repo narrows the Custom Path search to that
one repo (get_publish_root_for_ticket())."""

from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path

import maya.cmds as mc
from PublishApi import repo_paths, versioning

TOOL_ID = "maya_publisher"
THUMBNAIL_SIZE = 256

# Ticket files on disk live in ticket_store.py (no Maya/PublishApi), re-exported
# here so existing function.xxx callers keep working.
from MayaPublisher.ticket_store import (  # noqa: F401
    tickets_root,
    list_categories,
    category_dir,
    list_ticket_scripts,
    list_tickets,
    ticket_key,
    script_path,
    thumbnail_path,
    clear_ticket_thumbnail,
    info_path,
    get_ticket_info,
    ensure_ticket_info,
    ticket_meta_path,
    get_ticket_meta,
    get_ticket_batch_mode,
    set_ticket_meta,
    create_ticket_script,
    THUMBNAIL_SUFFIX,
    TICKET_PROGRAM_KEYS,
    TICKET_REPO_KEYS,
    TICKET_BATCH_MODE_KEY,
    TICKET_META_KEYS,
    BATCH_MODE_STANDALONE,
    BATCH_MODE_GUI,
    BATCH_MODES,
    BATCH_MODE_DEFAULT,
)


def set_ticket_thumbnail(category: str, script_name: str, image_path: str) -> Path:
    """Scales `image_path` down to THUMBNAIL_SIZE (keeping aspect) and saves
    it as this ticket's thumbnail_path(). Raises ValueError if the image
    can't be read or written."""
    from tmlib.module.PySide import QtCore, QtGui

    pixmap = QtGui.QPixmap(image_path)
    if pixmap.isNull():
        raise ValueError(f"Can't read image: {image_path}")
    if pixmap.width() > THUMBNAIL_SIZE or pixmap.height() > THUMBNAIL_SIZE:
        pixmap = pixmap.scaled(
            THUMBNAIL_SIZE, THUMBNAIL_SIZE, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )

    path = thumbnail_path(category, script_name)
    if not pixmap.save(str(path), "PNG"):
        raise ValueError(f"Can't write thumbnail: {path}")
    return path


def list_project_repos() -> list[str]:
    """Every repo name in the active Project — the From Repo choices."""
    project, _repo, _repo_path = repo_paths.get_active_repo()
    if project is None:
        return []
    return [repo.name for repo in project.repos]


def list_target_repos() -> list[str]:
    """To Repo choices: every repo in the active Project, plus any repo the
    active repo has a pipeline connection to (which may live in another
    Project) — the same candidate set get_publish_root_for_category()
    searches."""
    names = list_project_repos()
    for ref in repo_paths.get_pipeline_refs():
        resolved = repo_paths.resolve_ref(ref)
        if resolved is not None and resolved[1].name not in names:
            names.append(resolved[1].name)
    return names


def ticket_matches_active_repo(category: str, script_name: str) -> bool:
    """True if this ticket's from_repo is the active repo, or it has no
    from_repo at all (usable from any repo) — the "Show match current
    repo" filter in the Maya window."""
    return check_ticket_from_repo(category, script_name) is None


def check_ticket_from_repo(category: str, script_name: str) -> str | None:
    """Artist-facing reason this ticket can't be used from the active repo
    (its from_repo names a different repo), or None if it's fine — a
    ticket with no from_repo works from any repo."""
    from_repo = get_ticket_meta(category, script_name).get("from_repo")
    if not from_repo:
        return None
    _project, repo, _repo_path = repo_paths.get_active_repo()
    active_name = repo.name if repo is not None else None
    if active_name == from_repo:
        return None
    return f"Ticket '{category}' publishes from repo '{from_repo}', but the active repo is '{active_name or '(none)'}'."


def _program_icons_dir() -> Path:
    """<UKOREHUB_DATA_DIR>/assets/program_icons — same folder UkoreHub's
    MetadataStore.program_icons_dir points at (launcher.py passes
    data_dir / "assets" as its assets_dir)."""
    return repo_paths.find_data_dir() / "assets" / "program_icons"


def list_programs() -> list[tuple[str, Path | None]]:
    """(program_name, icon_path) for every Program in the active Project's
    Program DB (Project Editor > Programs), in DB order. icon_path is None
    when the Program has no icon, or UkoreHub hasn't pulled that icon to
    this machine yet (Maya can't pull R2 assets itself)."""
    project, _repo, _repo_path = repo_paths.get_active_repo()
    if project is None:
        return []

    icons_dir = _program_icons_dir()
    programs = []
    for program in project.programs:
        icon_path = icons_dir / program.icon_filename if program.icon_filename else None
        programs.append((program.name, icon_path if icon_path and icon_path.is_file() else None))
    return programs


def program_icon_path(program_name: str) -> Path | None:
    for name, icon_path in list_programs():
        if name == program_name:
            return icon_path
    return None


def get_active_repo_display() -> tuple[str | None, str | None]:
    """(repo_name, repo_path) for the active repo in UkoreHub, or
    (None, None) if nothing's active — for label_active_repo_name and
    pushButton_copy_repo_path."""
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


def get_publish_root_for_ticket(category: str, script_name: str) -> tuple[str, str]:
    """get_publish_root_for_category() for one ticket, honoring its
    from_repo (refuses if the active repo doesn't match) and to_repo (only
    that repo's Custom Paths are searched). Same RuntimeError contract."""
    reason = check_ticket_from_repo(category, script_name)
    if reason:
        raise RuntimeError(reason)
    to_repo = get_ticket_meta(category, script_name).get("to_repo")
    return get_publish_root_for_category(category, to_repo=to_repo)


def get_publish_root_for_category(category: str, to_repo: str | None = None) -> tuple[str, str]:
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
    connection declares a matching Custom Path. With `to_repo`, only the
    candidate repo with that name is searched (a ticket's own to_repo)."""
    project, repo, repo_path = repo_paths.get_active_repo()
    if project is None:
        raise RuntimeError("No active repo selected in UkoreHub. Open UkoreHub, pick a project/repo, then try again.")

    candidates = [(project.id, repo.id, repo_path, repo.name)]
    for ref in repo_paths.get_pipeline_refs():
        resolved = repo_paths.resolve_ref(ref)
        if resolved is not None:
            ref_project, ref_repo, ref_repo_path = resolved
            candidates.append((ref_project.id, ref_repo.id, ref_repo_path, ref_repo.name))

    if to_repo:
        candidates = [c for c in candidates if c[3] == to_repo]
        if not candidates:
            raise RuntimeError(
                f"Ticket '{category}' publishes to repo '{to_repo}', which isn't the active repo or one of its "
                "pipeline connections. Add a pipeline connection to it in Project Editor."
            )

    for target_project_id, target_repo_id, target_repo_path, target_repo_name in candidates:
        for custom_path in repo_paths.get_custom_paths(target_project_id, target_repo_id):
            if custom_path.get("label", "").strip().lower() == category.strip().lower():
                if not target_repo_path.is_dir():
                    raise RuntimeError(
                        f"Publish Path target repo for category '{category}' hasn't been cloned yet — "
                        "clone it in UkoreHub first."
                    )
                return str(target_repo_path / custom_path["path"].lstrip("/\\")), target_repo_name

    where = f"repo '{to_repo}'" if to_repo else "this repo or its pipeline connections"
    raise RuntimeError(
        f"No Custom Path named '{category}' found on {where}. Ask a studio admin to declare one in Project Editor."
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


def _parse_version_dir(version_dir: str) -> int:
    """3 for ".../v003" — RuntimeError if the folder isn't named vNNN."""
    name = os.path.basename(os.path.normpath(version_dir))
    if len(name) == 4 and name[0] == "v" and name[1:].isdigit():
        return int(name[1:])
    raise RuntimeError(f"Version folder must be named vNNN: {version_dir}")


def _remove_if_empty(path: str) -> None:
    """Drops a version folder a failed publish left empty — one with files
    in it is kept, so a half-finished export stays inspectable."""
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def publish(category: str, script_name: str, version_dir: str | None = None, version: int | None = None):
    """Runs the selected Ticket Script (run_ticket_script) with a `context`
    dict (version_dir, version, category, script_name, tool_id + ticket
    meta) so it can export/copy whatever it decides belongs there — see the
    module docstring. Returns (version_dir, version_number).

    With no `version_dir` (the Maya window's Publish button), resolves this
    ticket's publish root against the active repo and creates the next
    vNNN itself, removing it again if the ticket fails and left it empty.
    With `version_dir` (batch_runner.py — UkoreHub already resolved the
    destination and created the folder, so parallel jobs can't race for the
    same vNNN), uses that folder as-is and leaves cleanup to the caller;
    `version` defaults to the folder's own vNNN number.

    Raises RuntimeError — meant to be shown directly to the artist — if the
    scene hasn't been saved (or its filename yields no usable block name),
    the category's Custom Path can't be resolved, or the Ticket Script
    blocks the publish (returns False/raises)."""
    created_here = version_dir is None
    if created_here:
        block_name = get_scene_block_name()
        publish_root, _target_repo_name = get_publish_root_for_ticket(category, script_name)
        version_dir, version = versioning.get_version_directory(os.path.join(publish_root, script_name), block_name)
    else:
        if not os.path.isdir(version_dir):
            raise RuntimeError(f"Version folder doesn't exist: {version_dir}")
        if version is None:
            version = _parse_version_dir(version_dir)

    context = {
        "version_dir": version_dir,
        "version": version,
        "category": category,
        "script_name": script_name,
        "tool_id": TOOL_ID,
    }
    meta = get_ticket_meta(category, script_name)
    for key in TICKET_META_KEYS:
        context[key] = meta.get(key)
    ok, message = run_ticket_script(category, script_name, context)
    if not ok:
        if created_here:
            _remove_if_empty(version_dir)
        raise RuntimeError(message)

    return version_dir, version
