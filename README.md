# plugins/repo_internal/MayaPublisher/

Maya-side tool that resolves/versions a publish destination for the
active scene — merges what used to be three separate, near-identical
plugins (`RigPublisher`, `ModelPublisher`, `AnimationPublisher`, each
split out of the original `UkorePublisher` on 2026-07-19) into one. Their
`plugin.py`, `interface.py`, and `ui.ui` were byte-for-byte the same
pattern; only the actual export logic differed by scene type. A repo now
picks **one** Publish Mode (Rig / Model / Animation) under Repository
Setting > MayaPublisher instead of enabling a separate plugin per scene
type.

**2026-08-05: this plugin does not export or copy any file itself
anymore.** It used to call a mode-specific `UkoreMaya.core.Pipeline`
export function automatically once validation passed — confirmed with the
user this should be a fully manual step instead: `function.py`'s
`publish()` only resolves the destination and creates the versioned
folder, then hands both to the ticket's own attached scripts (see
"Publish is a scripted step" below). A ticket with no scripts attached, or
whose scripts do no file work, now produces an **empty** version folder —
there is no default export.

## Files

- `manifest.json` — plugin id `maya_publisher`, entry point `plugin.py`.
- `plugin.py` — `register(api)`: contributes `maya-scripts/` to the shared
  `maya_launcher_env_bridge` `PluginConfigStore` (same convention every
  other Maya tool plugin here uses), and registers the CATEGORY_REPO
  "MayaPublisher" settings tab. Relies on `plugins/repo_internal/MayaToolkit`
  (for `UkoreMaya.core.Pipeline`'s export functions — no longer imported by
  this plugin's own `function.py`, but still what an attached script would
  typically call to actually export something) and
  `plugins/repo_internal/PublishApi` (for path resolution/versioning/tickets)
  also being enabled — not imported directly, just expected on the same
  merged PYTHONPATH once Maya launches.
- `interface/publish_mode_store.py` — UkoreHub-side (non-Maya) store:
  `get_publish_mode`/`set_publish_mode` via
  `api.metadata.get_repo_plugin_data`/`set_repo_plugin_data(project_id,
  repo_id, "maya_publisher")`, key `"publish_mode"` — lives in
  `core/models.py`'s `Repo.plugin_data["maya_publisher"]`
  (`data/projects/<project_id>.json`) now, not a standalone blob.
  `migrate_legacy_data(api)` in this same file does a one-time cutover from
  the old `data/plugins/core/maya_publisher.json`'s `"publish_mode"` key,
  leaving the rest of that file alone — it's still the same file
  `PublishApi/tickets.py`'s ticket storage for this tool_id lands its own
  unrelated `"tickets"`/`"repo_publish_target"` keys in.
- `interface/publish_mode_settings_page.py` — `PublishModeSettingsPage`:
  Repository Setting > MayaPublisher, three radio buttons (Rig / Model /
  Animation), self-persisting on click — same self-resolving-active-repo
  `refresh()` pattern every CATEGORY_REPO tab in this app uses (e.g.
  `UkoreShot/interface/repo_video_settings_page.py`).
- `maya-scripts/MayaPublisher/function.py` — `TOOL_ID = "maya_publisher"`,
  `get_publish_mode()`: reads the active repo's configured mode off its own
  `plugin_data["maya_publisher"]` (`core/models.py`'s `Repo`, fetched via
  `PublishApi.repo_paths.get_active_repo()` — Maya's Python has no
  `PluginAPI` instance to go through). `publish(ticket: dict)`: resolves the
  publish root/next version via `PublishApi`, builds a `context` dict
  (`version_dir`, `version`, `ticket`, `mode`, `tool_id`), then runs the
  ticket's attached scripts with that context
  (`PublishApi.tickets.run_validation_scripts`) — **those scripts decide
  what to export/copy into `context["version_dir"]`**, not this function
  (see "Publish is a scripted step" below). Validation-script folders are
  still looked up per the repo's mode under the **old** per-tool ids
  (`PublishValidation/rig_publisher/`, `/model_publisher/`,
  `/animation_publisher/`) via `MODE_TOOL_IDS` — kept that way deliberately
  so no studio repo's already-committed scripts needed to move.
- `maya-scripts/MayaPublisher/interface.py` — `MainWindow`
  (`tmlib.ui.interface_template.ToolkitWindow`): ticket list +
  snapshot/publish/open-folder buttons, "Manage Tickets..." button, window
  title `"MayaPublisher — {Rig/Model/Animation}"` based on the repo's
  configured mode. Passes `show_export_type=True` to
  `PublishApi.ticket_manager_dialog.TicketManagerDialog` only when mode is
  `"animation"`, and `scripts_tool_id=MODE_TOOL_IDS[mode]` so the dialog's
  validation-script picker still looks in the right old-named folder while
  ticket storage itself uses the unified `maya_publisher` id.
- `maya-scripts/MayaPublisher/ui.ui` — Qt Designer layout, unchanged from
  the three merged plugins' own `ui.ui` (they were byte-identical) — loaded
  via `importlib.import_module("MayaPublisher")` + `__path__[0]/ui.ui`.

## Publish is a scripted step

`function.py`'s `publish(ticket)` never exports or copies a file itself —
it only resolves `context["version_dir"]` (already created on disk) and
runs the ticket's attached scripts
(`PublishApi.tickets.run_validation_scripts(scripts_tool_id, ticket,
context)`, see that function's own docstring for the full `context`
shape). A script's `validate(context)` is free to do whatever it wants —
call `UkoreMaya.core.Pipeline.export_maya_common`/`export_fbx_common`/
`export_playblast` directly, run some other export entirely, or just
check something and return `True`/`False` with no file work at all (the
original, still-supported zero-argument `validate()` contract). Returning
`False` (or raising) still blocks the publish from being reported
successful — these scripts are now both the gate *and* the mechanism, so
a ticket needs at least one script that actually produces output for
publishing it to do anything. Scripts run in the order listed, and every
attached script runs even if an earlier one already failed (existing
"see everything wrong at once" behavior, unchanged) — a script that
writes files partway through, then fails, can leave partial output behind.

## Migration from RigPublisher/ModelPublisher/AnimationPublisher

Any repo that had one of the three old plugins enabled needs to be
switched to `maya_publisher` in Requirements & Plugins, and have its
Publish Mode set once under Repository Setting > MayaPublisher. `RigTeam`
was migrated this way when this plugin was added (2026-08-05): its old
`rig_publisher` tickets were copied into `data/plugins/core/maya_publisher.json`
and its Publish Mode set to Rig — see that file and
`data/projects.json`. `data/plugins/core/rig_publisher.json` itself was
left in place, unused, rather than deleted.

Both of RigTeam's carried-over tickets ("SkeletonToUnity" with no scripts
attached, "ToAnimator" with `rig_to_anim_validate.py` attached) used to
get their `.ma` export for free from the old automatic `Pipeline.export_maya_common`
call. Since publish no longer exports anything on its own (see "Publish is
a scripted step" above), both need a script under RigTeam's own
`PublishValidation/rig_publisher/` that actually copies/exports the scene
— `rig_to_anim_validate.py` needs updating to do that in addition to
whatever it currently checks, and "SkeletonToUnity" needs a script
attached at all, or publishing either ticket now produces an empty
version folder. This wasn't done as part of this migration — that file
lives inside RigTeam's own git clone, outside this repo.

## Working on this plugin

Read/edit only files under this folder unless the change is specifically
about `PublishApi`'s API surface (a genuine cross-plugin task) — see the
`ukorehub-plugin` skill.
# MayaPublisher
