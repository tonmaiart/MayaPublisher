"""Publish logic — merges what used to be three near-identical
function.py files (RigPublisher/ModelPublisher/AnimationPublisher, each
split out of the original UkorePublisher's function.py on 2026-07-19)
into one, dispatching on the active repo's configured **Publish Mode**
(Repository Setting > MayaPublisher) instead of each mode having its own
separate plugin. Path resolution/versioning still goes through PublishApi
(tickets.get_publish_root_for_ticket(TOOL_ID, ticket) +
versioning.get_version_directory()) — see
plugins/repo_internal/PublishApi/README.md. `ticket` is a user-managed
dict (PublishApi.tickets, created/edited via ticket_manager_dialog.py's
"Manage Tickets..." button in this tool's own Maya window).

Ticket *storage* is unified under TOOL_ID = "maya_publisher" (one
<active repo>/.ukorehub/maya_publisher_tickets.json for every mode, since a
repo only ever uses one mode at a time — see PublishApi/tickets.py's own
docstring for why this lives inside the repo rather than under UkoreHub's
own data/). Validation-script *folders* deliberately keep using the old
per-mode tool ids (MODE_TOOL_IDS below) so an already-migrated repo's
committed PublishValidation/<old_tool_id>/ scripts don't need to move.

**2026-08-05: publish() no longer exports or copies any file itself.**
Before this, publish() called a mode-specific UkoreMaya.core.Pipeline
export function (export_maya_common/export_fbx_common/export_playblast)
automatically once validation passed — an artist had no control over
which files actually got sent to the publish folder, and the plugin baked
in one fixed shape per mode. Confirmed with the user: this is now a fully
manual step delegated to the ticket's own attached scripts under
PublishValidation/<old_tool_id>/ — publish() only resolves the
destination, creates the versioned folder, and hands both to those
scripts via `run_validation_scripts`'s `context` argument (see
PublishApi/tickets.py). A script decides entirely for itself what to
export/copy into `context["version_dir"]`, using
UkoreMaya.core.Pipeline's export functions directly if it wants to, or
anything else. A ticket with no scripts attached (or whose scripts do no
file work) now produces an **empty** version folder — there is no default
export anymore."""

import maya.cmds as mc
from PublishApi import tickets, versioning

TOOL_ID = "maya_publisher"

# Folder-key only (see module docstring) — never used for ticket storage.
MODE_TOOL_IDS = {
    "rig": "rig_publisher",
    "model": "model_publisher",
    "animation": "animation_publisher",
}
MODE_LABELS = {"rig": "Rig", "model": "Model", "animation": "Animation"}


def get_publish_mode() -> str | None:
    """This repo's configured Publish Mode, read off the active Repo's own
    plugin_data["maya_publisher"] (core/models.py's Repo). None if there's
    no active repo, or a studio admin hasn't set a mode for it yet under
    Repository Setting > MayaPublisher."""
    from PublishApi import repo_paths

    _, repo, _ = repo_paths.get_active_repo()
    if repo is None:
        return None
    return repo.plugin_data.get(TOOL_ID, {}).get("publish_mode")


def publish(ticket: dict):
    """Resolves this ticket's own publish root/next version, then runs its
    attached scripts (PublishApi.tickets.run_validation_scripts) with a
    `context` dict (`version_dir`, `version`, `ticket`, `mode`, `tool_id`)
    so they can export/copy whatever they decide belongs there — see the
    module docstring for why this plugin no longer does that itself.
    Raises RuntimeError — meant to be shown directly to the artist — if
    the scene hasn't been saved, this repo has no Publish Mode configured,
    one of the ticket's attached scripts blocks the publish (returns
    False/raises), or the ticket's own Publish Path can't be resolved.
    Returns (version_dir, version_number)."""
    scene_path = mc.file(q=True, sn=True)
    if not scene_path:
        raise RuntimeError("กรุณาบันทึกไฟล์ก่อนดำเนินการ Publish!")

    mode = get_publish_mode()
    if mode is None:
        raise RuntimeError(
            "This repo has no Publish Mode configured yet. Ask a studio admin to set one "
            "under Repository Setting > MayaPublisher."
        )

    publish_root = tickets.get_publish_root_for_ticket(TOOL_ID, ticket)
    version_dir, version = versioning.get_version_directory(publish_root, ticket["folder_name"])

    context = {
        "version_dir": version_dir,
        "version": version,
        "ticket": ticket,
        "mode": mode,
        "tool_id": TOOL_ID,
    }
    ok, message = tickets.run_validation_scripts(MODE_TOOL_IDS[mode], ticket, context)
    if not ok:
        raise RuntimeError(message)

    return version_dir, version
