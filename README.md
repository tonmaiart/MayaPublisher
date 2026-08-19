# plugins/repo_internal/MayaPublisher/

Maya-side tool that resolves/versions a publish destination for the
active scene — merges what used to be three separate, near-identical
plugins (`RigPublisher`, `ModelPublisher`, `AnimationPublisher`, each
split out of the original `UkorePublisher` on 2026-07-19) into one.

**2026-08-05: this plugin does not export or copy any file itself
anymore.** It used to call a mode-specific `UkoreMaya.core.Pipeline`
export function automatically once validation passed — confirmed with the
user this should be a fully manual step instead: `function.py`'s
`publish()` only resolves the destination and creates the versioned
folder, then hands both to the selected Ticket Script (see "Publish is a
scripted step" below). A Ticket Script that does no file work now produces
an **empty** version folder — there is no default export.

**2026-08-19: Publish Mode Setting Tab and PublishApi.tickets.json-managed
tickets retired.** A repo used to pick **one** Publish Mode (Rig / Model /
Animation) under Repository Setting > MayaPublisher, and tickets were
JSON dicts (`publish_target`, `script_names`) managed via PublishApi's
`ticket_manager_dialog.TicketManagerDialog`. Both are gone — see "Category
+ Ticket Scripts (current design)" below for what replaced them. This
plugin now has **no UkoreHub-side UI of its own** — everything happens in
the Maya-side window.

## Category + Ticket Scripts (current design)

- **Category** = an immediate subfolder name under this plugin's own
  `maya-scripts/MayaPublisher/tickets/` (e.g. `Rig`, `Anim`, `Model`) —
  `function.list_categories()` just lists these folders, so adding a new
  category studio-wide means adding a folder here (committed to
  MayaPublisher's own git history) and re-pulling this plugin. Shown in
  the Maya window's `comboBox_tickets_catagory`.
- **Ticket Script** = a single `.py` file directly under
  `tickets/<Category>/` (e.g. `tickets/Anim/UnityShot.py`) —
  `function.list_ticket_scripts(category)` lists these by filename (no
  `.py`) for the "Choose Ticket Scripts" list. A ticket and its script are
  now the same file — there's no separate JSON ticket entry pointing at
  attached scripts anymore. Must define `def validate(context):` — see
  `function._TICKET_SCRIPT_TEMPLATE` for the exact contract (same
  `validate(context)`/`context` dict shape PublishApi's own
  `tickets._SCRIPT_TEMPLATE` established, just with `category`/
  `script_name` keys instead of `ticket`/`mode`).
- **`pushButton_create_ticket_scripts`** prompts for a name
  (`cmds.promptDialog`) and seeds a new `tickets/<category>/<name>.py`
  from that template (`function.create_ticket_script`) — raises if a
  script with that name already exists under the category.
- **`pushButton_edit_selected_script`** / **`pushButton_open_ticket_dir`**
  just `os.startfile()` the selected script / its category folder — no
  special editor integration.
- **Publish-target resolution** (`function.get_publish_root_for_category`)
  no longer reads a per-ticket `publish_target`. It matches the selected
  category's name against a Custom Path **label** declared in Project
  Editor (`PublishApi.repo_paths.get_custom_paths`) — checked first on the
  active repo itself, then on every repo the active repo has a pipeline
  connection to (`get_pipeline_refs`/`resolve_ref`). A studio admin makes a
  category resolvable simply by declaring a Custom Path labeled e.g. "Rig"
  somewhere reachable from the repo — no MayaPublisher-specific config
  needed for this part at all, per this plugin's own working convention of
  leaning on `PublishApi` for anything it already does (see "Working on
  this plugin" below).
- **`pushButton_catagory_config`** ("Save") persists the currently-picked
  category as this repo's default, written to
  `<active_repo_root>/.MayaPublisher` (JSON, `{"default_category": "..."}`)
  — `function.set_default_category`/`get_default_category`. Committed to
  the repo's own git history like `PublishValidation/` used to be, so
  every artist on that repo opens the tool with the right category
  pre-selected. Replaces the old Repository Setting > MayaPublisher radio
  buttons; unlike those, this is a plain per-repo file this plugin owns
  outright, not UkoreHub's own `Repo.plugin_data`.

## Files

- `manifest.json` — plugin id `maya_publisher`, entry point `plugin.py`.
- `plugin.py` — `register(api)`: contributes `maya-scripts/` to the shared
  `maya_launcher_env_bridge` `PluginConfigStore` (same convention every
  other Maya tool plugin here uses, e.g. `cache/plugins/PublishApi/plugin.py`)
  — nothing else; no Settings tab, no `plugin_api`/`core`/`interface`
  imports needed. Relies on `cache/plugins/MayaToolkit` (for
  `UkoreMaya.core.Pipeline`'s export functions — no longer imported by this
  plugin's own `function.py`, but still what a Ticket Script would
  typically call to actually export something) and `cache/plugins/PublishApi`
  (for path resolution/versioning) also being enabled — not imported
  directly, just expected on the same merged PYTHONPATH once Maya
  launches.
- `maya-scripts/MayaPublisher/function.py` — `TOOL_ID = "maya_publisher"`.
  `list_categories()`/`list_ticket_scripts()`/`category_dir()`/
  `script_path()`: read `tickets/` straight off disk. `create_ticket_script()`:
  seeds a new Ticket Script from `_TICKET_SCRIPT_TEMPLATE`.
  `get_default_category()`/`set_default_category()`: this repo's
  `.MayaPublisher` file. `get_active_repo_display()`: `(repo_name,
  repo_path)` for the UkoreHub-active repo, or `(None, None)` — feeds
  `label_active_repo_name`/`label_active_repo_path`.
  `get_publish_root_for_category()`: Custom Path label match (see above),
  returns `(publish_root, target_repo_name)` — `target_repo_name` feeds
  `label_repo_target_name` and can differ from the active repo when the
  match comes from a pipeline connection. `get_version_info()`:
  `(latest_version, next_version)` for a category+ticket's own publish
  subfolder (`latest_version` is `None` if nothing's published there yet)
  — feeds `label_lastest_publish_version`/`label_next_publish_version`.
  `run_ticket_script()`: same `importlib.util.spec_from_file_location`/
  `exec_module` dispatch `PublishApi.tickets.run_validation_scripts` uses,
  just pointed at `tickets/<category>/<script_name>.py` instead of a
  repo's own `PublishValidation/<tool_id>/`. `publish(category,
  script_name)`: resolves the publish root/next version via `PublishApi`,
  builds a `context` dict (`version_dir`, `version`, `category`,
  `script_name`, `tool_id`), then runs the selected Ticket Script with
  that context — **that script decides what to export/copy into
  `context["version_dir"]`**, not this function (see "Publish is a
  scripted step" below).
- `maya-scripts/MayaPublisher/interface.py` — `MainWindow`
  (`tmlib.ui.interface_template.ToolkitWindow`): category combobox +
  ticket-script list + snapshot/publish/open-folder buttons, plus a
  read-only info block (`populate_repo_info()`/`refresh_publish_destination()`)
  showing the active repo, resolved publish root/target repo, and
  latest/next version. `pushButton_RefreshScripts` (`refresh_ticket_scripts()`)
  rescans `tickets/<category>/` off disk without switching category, for a
  Ticket Script a teammate added/removed while the window was already
  open. Window title `"MayaPublisher — {Category}"` once a category is
  picked. No "Manage Tickets..." button anymore — that whole dialog/
  workflow is retired for this plugin.
- `maya-scripts/MayaPublisher/tickets/` — bundled Ticket Scripts, one
  subfolder per category (`Rig/`, `Anim/`, `Model/`, ...), each holding
  that category's `.py` Ticket Scripts. Committed to this plugin's own git
  history — adding a category or a studio-wide Ticket Script here ships to
  every repo that has MayaPublisher enabled, the next time this plugin's
  clone is pulled.
- `maya-scripts/MayaPublisher/ui.ui` — Qt Designer layout. Key widgets:
  `comboBox_tickets_catagory` + `pushButton_catagory_config` (category
  picker/save); `listWidget_ticket` + `pushButton_RefreshScripts` /
  `pushButton_create_ticket_scripts` / `pushButton_edit_selected_script` /
  `pushButton_open_ticket_dir` ("Choose Ticket Scripts" group); the
  "Publish Metadata"/"Publish Info" read-only labels
  (`label_active_repo_name`, `label_active_repo_path`, `label_publish_root`,
  `label_repo_target_name`, `label_lastest_publish_version`,
  `label_next_publish_version`); plus the pre-existing publish/snapshot/
  open-dir controls. Loaded via `importlib.import_module("MayaPublisher")`
  + `__path__[0]/ui.ui`.

## Publish is a scripted step

`function.py`'s `publish(category, script_name)` never exports or copies a
file itself — it only resolves `context["version_dir"]` (already created
on disk) and runs the selected Ticket Script
(`run_ticket_script(category, script_name, context)`). A script's
`validate(context)` is free to do whatever it wants — call
`UkoreMaya.core.Pipeline.export_maya_common`/`export_fbx_common`/
`export_playblast` directly, run some other export entirely, or just check
something and return `True`/`False` with no file work at all (the
original, still-supported zero-argument `validate()` contract). Returning
`False` (or raising) still blocks the publish from being reported
successful — a Ticket Script is now both the gate *and* the mechanism, so
it needs to actually produce output for publishing it to do anything.

## Migration from RigPublisher/ModelPublisher/AnimationPublisher

Any repo that had one of the three old plugins enabled needs to be
switched to `maya_publisher` in Requirements & Plugins. Its old Publish
Mode (`Repo.plugin_data["maya_publisher"]["publish_mode"]`, set under the
now-removed Repository Setting > MayaPublisher tab) is inert leftover data
now — nothing reads it anymore; it was deliberately left in place rather
than migrated, same as `data/plugins/core/rig_publisher.json` was left in
place unused after the original 2026-08-05 merge. A repo migrating now
just needs its artists to pick (and optionally Save) a category in the
Maya window instead.

## Working on this plugin

Read/edit only files under this folder unless the change is specifically
about `PublishApi`'s API surface (a genuine cross-plugin task) — see the
`ukorehub-plugin` skill.
