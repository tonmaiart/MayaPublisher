"""Where each batch publish lands, worked out in UkoreHub before any Maya
starts — so the page can show it up front, and so every vNNN folder is
created here, one at a time, instead of by parallel Maya processes racing
for the same number.

Convention-only duplicate of the Maya side's rules — keep in sync:
- scene_block_name()        <-> function.get_scene_block_name()
- resolve_publish_root()    <-> function.get_publish_root_for_category()
  (Custom Path label match over the repo + its pipeline connections, read
  from Repo.plugin_data["project_editor"] the way PublishApi.repo_paths does)
- check_from_repo()         <-> function.check_ticket_from_repo()
- get_new_version()         <-> PublishApi.versioning.get_new_version()
Layout: <publish_root>/<script_name>/<block_name>/vNNN.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from plugin_api import NotFoundError

from .tickets import ticket_store

PROJECT_EDITOR_PLUGIN_ID = "project_editor"
_VERSION_PATTERN = re.compile(r"^v(\d{3})$")
_MAX_RESERVE_ATTEMPTS = 50


class PlanError(RuntimeError):
    """This scene/ticket can't be published — message is user-facing."""


@dataclass
class PublishPlan:
    scene_path: str
    category: str
    script_name: str
    batch_mode: str = ticket_store.BATCH_MODE_DEFAULT
    block_name: str = ""
    publish_root: str = ""
    target_repo: str = ""
    base_dir: str = ""  # <publish_root>/<script_name>/<block_name>
    next_version: int | None = None  # preview only — reserve_version_dir() decides
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def scene_block_name(scene_path: str) -> str:
    """First "_"-separated block of the file name, e.g. KBA030_rig_v012.ma -> KBA030."""
    block = os.path.splitext(os.path.basename(scene_path))[0].split("_")[0].strip()
    if not block:
        raise PlanError(f"Can't tell the publish folder name from the file name: {os.path.basename(scene_path)}")
    return block


def _repo_entry(api, project_id, repo_id):
    """(project, repo, repo_path) or None if either id is gone."""
    try:
        project = api.metadata.get_project(project_id)
        repo = api.metadata.get_repo(project_id, repo_id)
    except NotFoundError:
        return None
    return project, repo, Path(api.local_config.workspace_root) / repo.local_path


def _project_editor_data(repo) -> dict:
    return (repo.plugin_data or {}).get(PROJECT_EDITOR_PLUGIN_ID, {})


def project_repo_names(api, project_id: str) -> list[str]:
    """Every repo name in the project — a ticket's From Repo choices."""
    try:
        return [repo.name for repo in api.metadata.get_project(project_id).repos]
    except NotFoundError:
        return []


def target_repo_names(api, project_id: str, repo_id: str) -> list[str]:
    """To Repo choices: the project's repos plus every repo `repo_id` has a
    pipeline connection to (which may live in another project) — the same
    candidate set resolve_publish_root() searches."""
    names = project_repo_names(api, project_id)
    entry = _repo_entry(api, project_id, repo_id)
    if entry is not None:
        for ref in _project_editor_data(entry[1]).get("pipeline_inputs", []):
            resolved = _repo_entry(api, ref.get("project_id"), ref.get("repo_id"))
            if resolved is not None and resolved[1].name not in names:
                names.append(resolved[1].name)
    return names


def check_from_repo(category: str, script_name: str, repo_name: str) -> str | None:
    """User-facing reason this ticket can't publish from `repo_name`, or None."""
    from_repo = ticket_store.get_ticket_meta(category, script_name).get("from_repo")
    if from_repo and from_repo != repo_name:
        return f"Ticket '{category}' publishes from repo '{from_repo}', but this batch runs from '{repo_name}'."
    return None


def resolve_publish_root(api, project_id: str, repo_id: str, category: str, to_repo: str | None = None) -> tuple[str, str]:
    """(publish_root, target_repo_name) — the cloned path of whichever
    candidate repo (this one first, then its pipeline connections in order)
    declares a Custom Path labeled `category` (case-insensitive), joined with
    that Custom Path's relative path. Raises PlanError."""
    entry = _repo_entry(api, project_id, repo_id)
    if entry is None:
        raise PlanError("The repo this batch publishes from no longer exists.")
    _project, repo, repo_path = entry

    candidates = [(repo, repo_path)]
    for ref in _project_editor_data(repo).get("pipeline_inputs", []):
        resolved = _repo_entry(api, ref.get("project_id"), ref.get("repo_id"))
        if resolved is not None:
            candidates.append((resolved[1], resolved[2]))

    if to_repo:
        candidates = [c for c in candidates if c[0].name == to_repo]
        if not candidates:
            raise PlanError(
                f"Ticket '{category}' publishes to repo '{to_repo}', which isn't this repo or one of its "
                "pipeline connections. Add a pipeline connection to it in Project Editor."
            )

    wanted = category.strip().lower()
    for target_repo, target_path in candidates:
        for custom_path in _project_editor_data(target_repo).get("custom_paths", []):
            if custom_path.get("label", "").strip().lower() == wanted:
                if not target_path.is_dir():
                    raise PlanError(
                        f"Publish Path target repo for category '{category}' hasn't been cloned yet — clone it first."
                    )
                return str(target_path / custom_path["path"].lstrip("/\\")), target_repo.name

    where = f"repo '{to_repo}'" if to_repo else "this repo or its pipeline connections"
    raise PlanError(f"No Custom Path named '{category}' found on {where}. Ask a studio admin to declare one in Project Editor.")


def get_new_version(base_dir: str) -> int:
    """Next free vNNN number under base_dir (1 if none yet)."""
    if not os.path.isdir(base_dir):
        return 1
    highest = 0
    for name in os.listdir(base_dir):
        match = _VERSION_PATTERN.match(name)
        if match and os.path.isdir(os.path.join(base_dir, name)):
            highest = max(highest, int(match.group(1)))
    return highest + 1


def plan_publish(api, project_id: str, repo_id: str, scene_path: str, category: str, script_name: str) -> PublishPlan:
    """Resolves one scene + ticket. Never raises — a problem lands in .error."""
    plan = PublishPlan(scene_path=scene_path, category=category, script_name=script_name)
    try:
        if not os.path.isfile(scene_path):
            raise PlanError(f"Scene file not found: {scene_path}")
        if not ticket_store.script_path(category, script_name).is_file():
            raise PlanError(f"Ticket '{category}/{script_name}' no longer exists.")
        entry = _repo_entry(api, project_id, repo_id)
        if entry is None:
            raise PlanError("The repo this batch publishes from no longer exists.")
        reason = check_from_repo(category, script_name, entry[1].name)
        if reason:
            raise PlanError(reason)

        plan.batch_mode = ticket_store.get_ticket_batch_mode(category, script_name)
        plan.block_name = scene_block_name(scene_path)
        to_repo = ticket_store.get_ticket_meta(category, script_name).get("to_repo")
        plan.publish_root, plan.target_repo = resolve_publish_root(api, project_id, repo_id, category, to_repo)
        plan.base_dir = os.path.join(plan.publish_root, script_name, plan.block_name)
        plan.next_version = get_new_version(plan.base_dir)
    except PlanError as exc:
        plan.error = str(exc)
    return plan


def duplicate_destinations(plans) -> set[str]:
    """base_dirs that more than one plan publishes into — each still gets
    its own vNNN, but it's usually a mistake (same asset/shot twice)."""
    counts = Counter(os.path.normcase(p.base_dir) for p in plans if p.ok)
    return {p.base_dir for p in plans if p.ok and counts[os.path.normcase(p.base_dir)] > 1}


def reserve_version_dir(base_dir: str) -> tuple[str, int]:
    """Creates the next free <base_dir>/vNNN and returns (version_dir,
    version). os.mkdir fails if another machine took that number in the
    meantime, so it just tries the next one."""
    os.makedirs(base_dir, exist_ok=True)
    for _ in range(_MAX_RESERVE_ATTEMPTS):
        version = get_new_version(base_dir)
        version_dir = os.path.join(base_dir, f"v{version:03d}")
        try:
            os.mkdir(version_dir)
        except FileExistsError:
            continue
        return version_dir, version
    raise PlanError(f"Couldn't reserve a new version folder under {base_dir}.")


def release_version_dir(version_dir: str) -> bool:
    """Removes a reserved vNNN a failed job left empty (one with files in it
    is kept for inspection). True if removed."""
    try:
        if os.path.isdir(version_dir) and not os.listdir(version_dir):
            os.rmdir(version_dir)
            return True
    except OSError:
        pass
    return False
