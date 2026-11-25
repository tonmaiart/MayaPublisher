# plugins/repo_internal/MayaPublisher/

**2026-11-22: agent script request.** A request `{"script": "<file.py>",
"args", "repo"}` to the same action runs one `mayapy <file.py>` with
`resolve_maya_launch`'s env (+ `UKORE_MAYA_PLUGINS`, `UKORE_MAYA_PROJECT`)
via `QProcess`; log `<request>.log`, status `state`/`exit_code`. Lets the
agent fix scenes headless before a publish.

**2026-11-22: agent batch (`maya_publisher_batch/agent_batch.py`).** The
same headless queue the page runs, started from a request file instead of
clicks, for UkoreHub's Agent Bridge (`file_action key=maya_publisher_agent_batch
paths=[<request>.json]`). Request: `{"scenes", "category", "script_name",
"parallel", "note", "repo"}`; it plans every scene with `plan_publish`
against the active repo and queues nothing unless all plan cleanly, then
uses `resolve_maya_launch` + `BatchQueue` (vNNN reserved per job, empty
folder released on failure). Progress: `<request>.status.json` next to the
request, rewritten on every job change (`state` running/done/rejected, per
job status/version_dir/log_path). Registered as a `FileContextActionSpec`
whose `match` is always False, so Explorer never shows it. First used for
25 KafkaProj UnityShot publishes, 2 in parallel, all OK (about 1 min each).

**2026-11-02: batch jobs failing with no result file — output fixes.** A
real batch (UnityShot on KafkaProj shots) failed every job: the logs
stopped right after "Read N files" and no `.result.json` was written.
Found and fixed: under a QProcess pipe, mayapy's stdout is **cp1252**, so a
ticket/tool printing `✔` (e.g. `tmlib.core.Validate`'s material messages)
or Thai text **raised** mid-publish, and printing the traceback could raise
again before the result was written. It is also **block-buffered**, so the
lines before a failure never reached the log. Now:
`batch_runner._setup_output()` switches stdout/stderr to UTF-8
(`errors="replace"`) + line buffering, and enables `faulthandler`, so a hard
crash dumps its Python stack into the log. `maya_env` also sets
`PYTHONIOENCODING=utf-8:replace` and `PYTHONUNBUFFERED=1` for everything
that prints earlier. `run_job` catches `BaseException` (a tool calling
`sys.exit()`), and a failing traceback print can no longer stop the result
being written. The queue now appends "Maya process ended: exit code …" to
every log, and reports "Maya crashed (exit code 0xC…)" when it did. Verified
with the real queue from a windowless process, 2 jobs in parallel, on
KBA010/020/030: all published (KBA020/030 failed before the fix). 

**Then the real crash: 0xC0000409 on repos enabling `dreamwall_picker`.**
With the new exit-code reporting, the UkoreHub run showed every headless job
aborting with `0xC0000409` right after `[UkoreReferenceEditor] [Dreamwall
Picker] Searching Pickers`. Headless jobs used to run the launch hooks'
`pre_open_mel`. That imports UkoreReferenceEditor, whose scene-open
callback loads Dreamwall Picker's Qt UI, and with no UI in mayapy Qt aborts
the process. It only happens when `dwpicker` is on PYTHONPATH (AnimatorTeam
enables `dreamwall_picker`), which is why earlier tests passed.
Reproduced with AnimatorTeam's exact plugin set. Fix: **headless jobs run
no launch hooks** (`startup_mel: None` in `batch_queue`;
`MayaLaunch.standalone_startup_mel()` removed). Hooks set up an artist's
interactive Maya and add nothing a ticket needs; GUI jobs still get them.
Re-verified with that plugin set: KBA010/020/030 all published, 2 in
parallel.

**Columns** in the queue, Diagnose and ticket lists are all `Interactive`
now (`diagnose_tab.interactive_columns()`): drag a divider to resize,
double-click it to fit, with sensible starting widths and no Stretch column
(Qt won't let you drag one, and it squeezed Status). Rows being added no
longer auto-resize columns over the user's widths.

**2026-10-18: Diagnose References tab.** The queue now sits in a
`QTabWidget` ("Publish Queue" | "Diagnose References"). The diagnose tab
(`maya_publisher_batch/diagnose_tab.py`) lists, per queued scene, every
reference it uses — namespace, file, its version, the latest version on
disk, status **Latest / Outdated (n behind) / Missing / No version** — with
a per-scene summary, an "Only outdated / missing" filter, and right-click
Show in Explorer / Show Latest Version in Explorer / Open Folder / Copy
Path (double-click = Show in Explorer). It rescans on a `QThread`
(`background_threads` on the SectionSpec) whenever the tab is shown after
the queue or repo changed, or via Rescan.

`scene_refs.py` does the work without Maya: it reads only the `.ma` header
(the `file -r` / `file -rdi N` commands before the first `createNode` —
not the first `requires`: plugin requires like `fbxmaya` come before the
references in real scenes), drops the duplicate `-rdi 1` line Maya writes for
every top-level reference, and shows `-rdi 2+` as nested. Each path is
resolved as absolute → project-relative (repo root) → scene-relative,
`$ENV` vars expanded, a `{n}` copy-number suffix dropped, and if it's
another machine's absolute path, re-rooted onto this workspace (shown in the
tooltip). "Latest" is the highest number on disk next to the reference,
for either convention: a `vNNN` **folder** (MayaPublisher's own publish
layout — the latest file is the same name, the name with its version
swapped, or the only file of that type in the newest folder) or a
`_vNNN` **token in the file name** (work files; `prev002` doesn't count).
Anything else is "No version". Publishing doesn't look at it.

**Updating references (2026-11-02).** "Update All to Latest (n)" (top bar),
right-click a scene > "Update Outdated in This Scene", or a reference >
"Update to Latest" — confirmed first, refused while a batch is publishing,
rescans afterwards. Only references `scene_refs.can_update()` allows:
Outdated, top-level (a nested one comes from the referenced file — update
that file), and whose latest is an actual file. `update_references()`
rewrites **only** that reference node's `file -r` and `file -rdi 1` lines
in the `.ma` text (path, `-typ` if the extension changed; a `{n}`
copy-number suffix is kept) and drops the old file's `-rdi 2+` nested lines
under it (Maya re-reads nested references from the new file on open).
Every other byte, line endings included, is untouched; the write goes
through a temp file + `os.replace`. New paths inside the repo are written
**repo-relative** (`publish/...`, `path_for_scene()`), which Maya resolves
against the project UkoreHub sets — so the scene stops depending on
whichever machine/drive last saved it; paths outside the repo stay
absolute. Verified on copies of real KafkaProj shots: Maya 2026 (mayapy)
opened them with every updated reference loaded from the new version.

**2026-10-18: scenes come from Explorer, `.ma` only.** The queue's Add
Files... / Add Folder... buttons are gone. Instead `plugin.py` registers a
`FileContextActionSpec` ("Add to Maya Publish Queue", new
`api.register_file_context_action` in UkoreHub's plugin_api, added the same
day) — right-click `.ma` file(s) or a folder row in Explorer (or empty
space, for the current folder). It only shows when the active repo enables
`maya_publisher`. A folder is scanned recursively when clicked. Rows get
the ticket selected in the Maya Publisher page. Explorer stays on screen
(no `navigate_and_focus` jump, by request): the status bar reports how many
were added, and `MayaPublisherPage.browse_to_path()` pre-selects the first
added row for when the artist switches over. The other direction: a queue
row's right-click **Show in Explorer** switches to UkoreHub's Explorer
(`navigate_and_focus("repo_browser", scene)`, via the `UICommandService`
`plugin.py`'s `wire` hands the page as `page.ui_commands`) with that scene
selected; "Open Scene Folder" still opens the OS file explorer. Drag-drop onto the queue still works. Only Maya
ASCII (`.ma`) is ever queued (`SCENE_EXTENSIONS`) — `.mb` is ignored
everywhere. On an older UkoreHub without that API the action just isn't
registered (`hasattr` guard).

**2026-10-18: batch publish presets.** The queue has a **Preset** bar
(combo + Load / Save As... / Delete): a preset is a saved queue — every
row's scene + ticket — plus the note, e.g. one per set of shots
(`maya_publisher_batch/preset_store.py`). Presets are **per repo and shared
with the whole team** (user's choice): stored in
`Repo.plugin_data["maya_publisher"]["batch_presets"]` via
`api.metadata.get_repo_plugin_data`/`set_repo_plugin_data`, so they're
cloud-synced in the project blob. Scene paths are saved relative to the
workspace root when possible, so a preset loads on any machine; a scene
not on this machine yet still loads, as a "Can't publish: Scene file not
found" row. Load asks Replace Queue / Add to Queue when the queue isn't
empty; Save over an existing name and Delete both confirm (it affects
everyone on the repo).

**2026-10-18: batch publish, phase 3 — the "Maya Publisher" section.**
`plugin.py` now registers a UkoreHub sidebar section (`SectionSpec` key
`maya_publisher`, order 60, `SP_ArrowUp`) whose page is
`maya_publisher_batch/batch_page.py` (`MayaPublisherPage`), plus
`on_repo_changed` (re-plan rows) and `on_app_close` (kill running Maya
jobs). `plugin.py` puts its own folder on `sys.path` like maya_launcher
does, so the uniquely-named `maya_publisher_batch` package imports.

- **Queue (left):** Add Files... / Add Folder... (recursive) / drag-drop
  `.ma`/`.mb`. New rows get the ticket selected on the right; "Use Selected
  Ticket" re-tickets the selected rows (or all). Columns File | Ticket |
  Block | Destination | Status | Time. Rows are re-planned against the
  active repo on every change, so "Can't publish: ..." and "same folder as
  another row" show before Start. Right-click: Run Again, Open Scene/Publish
  Folder, Open Log File, Remove. Double-click a published row opens its
  `vNNN`.
- **Tickets (right):** the old window's list (Ticket | From | From Repo |
  To | To Repo, + Mode) with "Show match current repo", the `.md` step list,
  and every right-click action (Edit Ticket... — `ticket_dialogs.py`, now
  with Batch Mode —, Set/Clear Icon, Edit Script/Info, Open Ticket Dir, New
  Ticket..., Open Tickets Dir). Program choices come straight from
  `api.metadata.list_programs()` (names only — no program icons here yet).
- **Note:** written to `note.txt` in every version the batch publishes.
- **Run:** Start re-plans, confirms, resolves Maya (`maya_env`) and starts a
  `BatchQueue` pinned to the current repo; Parallel = headless job limit
  (GUI jobs always 1); "Keep GUI Maya open (debug)" leaves GUI jobs' Maya
  open. Stop kills running jobs. The log pane shows the selected row's Maya
  output live; a summary box appears if anything failed.

**2026-10-18: batch publish, phase 2 (UkoreHub-side engine, no UI yet).**
New package `maya_publisher_batch/` at the plugin root, running in
UkoreHub's own process (imports only `plugin_api`/PySide6/stdlib).
`plugin.py` doesn't use it yet — phase 3 adds the page:

- `maya-scripts/MayaPublisher/ticket_store.py` — every pure tickets/ helper
  (list/meta/info/icon paths/batch_mode/create) moved out of `function.py`,
  which re-exports them. No Maya/PublishApi imports, so UkoreHub loads the
  same file by path (`maya_publisher_batch/tickets.py`) — one
  implementation for both sides.
- `maya_env.py` — `resolve_maya_launch(api, project_id, repo)` ->
  `MayaLaunch` (maya.exe, mayapy.exe next to it, env, plug-ins to
  force-load, launch hooks). **Convention-only copy of
  `cache/plugins/maya_launcher/plugin.py`'s recipe** (Software Linker exe +
  pinned version, `maya_launcher_env_bridge` contributions filtered by the
  repo's enabled + discovered tools, PublishApi always) — keep in sync.
  Deliberate differences: drops `PYTHONHOME`/`PYTHONEXECUTABLE`/
  `PYTHONSTARTUP`, always adds this plugin's `maya-scripts/` to
  PYTHONPATH. Headless jobs run **no** launch hooks (changed 2026-11-02, see
  the crash note at the top); GUI jobs get maya_launcher's full `-command`
  MEL with `run_in_gui()` instead of opening a scene.
- `publish_plan.py` — `plan_publish()` resolves block name, from_repo
  check, Custom Path destination (repo + pipeline connections, read from
  `Repo.plugin_data["project_editor"]` like `PublishApi.repo_paths`) and the
  preview next version, all before Maya starts; `duplicate_destinations()`
  flags two scenes landing in the same folder;
  `reserve_version_dir()`/`release_version_dir()` create/remove `vNNN`.
  Copies of the Maya-side rules — see its docstring for the pairs to keep
  in sync.
- `batch_queue.py` — `BatchQueue` (QObject): one `QProcess` per scene,
  separate limits for standalone (default 2) and GUI (default 1) jobs,
  per-mode timeouts, cancel/kill. Reserves each job's `vNNN` right before
  starting it (so parallel jobs never race), removes it again if the job
  fails/cancels and left it empty. Per job in
  `<cache_dir>/maya_publisher_batch/<timestamp>/`: `.job.json`,
  `.result.json` (source of truth), `.log`.

**2026-10-18: batch publish, phase 1 (Maya side).** MayaPublisher is moving
to a UkoreHub-side batch publish page (publish many scenes without opening
each in Maya); the Maya window will be removed once that page exists. This
phase adds the Maya-side half only — nothing in UkoreHub calls it yet:

- `maya-scripts/MayaPublisher/batch_runner.py` publishes **one** scene per
  Maya process from a job `.json` and writes a result `.json` (contract in
  its module docstring). Two modes: headless `mayapy batch_runner.py --job
  <job.json>`, or a real `maya.exe -command` window via `run_in_gui()` for a
  ticket that needs a viewport. The scene is opened fresh and never saved.
- `batch_guard.py` makes Maya's blocking dialogs unattended-safe:
  `confirmDialog` is auto-answered with its defaultButton (recorded in the
  result — e.g. `tmlib.core.Scene.clean_up_scene()`'s Yes/No in
  RigToAnimPublish/Proxy), `inViewMessage` goes to the log, and
  prompt/file/layout dialogs raise.
- Each ticket's `.json` has a `batch_mode`: `"standalone"` (default) or
  `"gui"` (`function.get_ticket_batch_mode()`). A `"gui"` ticket refuses to
  run under headless mayapy. Every bundled ticket is `"standalone"` except
  `AnimationUnityPublish/UnityShotFace` (`"gui"`, see 2026-11-02 below).
- `function.publish()` takes an optional pre-created `version_dir`
  (+`version`), so UkoreHub can resolve the destination and create every
  `vNNN` itself before starting parallel jobs (no two jobs racing for the
  same number). Without it, behavior is unchanged except that a failed
  publish now removes the `vNNN` it created if the folder is still empty.
- Snapshot thumbnail removed (button, `take_a_snapshot()`,
  `thumbnail.jpg`) — no longer used.
- UnityShot no longer calls the TEMP `bake_texture_to_image_sequence()`
  (it playblasts, so the ticket couldn't run headless). The function is
  still in the file for a future `"gui"` ticket.

**2026-11-02: UnityShotFace (facial texture bake).** New `"gui"` ticket
`tickets/AnimationUnityPublish/UnityShotFace.py`, published alongside
UnityShot. It imports references and drops namespaces (UnityShot's
`clean_up_scene()`), finds every material on a `*_Geo` mesh whose color
input is animated (an animCurve or expression upstream, or a `file` node with
Use Image Sequence on), and bakes each one with UnityShot's
`bake_texture_to_image_sequence()`. The new `use_subfolder=False` argument
writes flat `<material>.<frame>.png` files into its own
`<animation_publish>/UnityShotFace/<shot>/vNNN/`. Unity's Shot Importer
reads that folder as **Published Textures** and imports the latest bake with
each shot as `{shot}_{material}` Texture2DArrays. UnityShot itself is
unchanged and stays headless.

**2026-10-11: flat ticket list + ticket icons.** The Maya window's
category combo box (`comboBox_tickets_catagory`) is gone. `listWidget_ticket`
now lists every ticket across all categories (`function.list_tickets()`),
one row per `tickets/<Category>/<script>.py`, so picking a row picks
exactly that ticket — there's no category-then-script two-step anymore.
On disk nothing moved, and `<Category>` still resolves the publish root via
its Custom Path label. Each ticket can have an icon at
`tickets/<Category>/<script>.png` (`function.thumbnail_path()` /
`set_ticket_thumbnail()`, scaled to 256px), set via the row's right-click
menu (Set Icon from File... / Clear Icon).

Each ticket also has a hand-written step list,
`tickets/<Category>/<script>.md` (Markdown, numbered 1, 2, 3 …;
`function.info_path()`/`get_ticket_info()`), shown in
`textBrowser_ticket_info` under the ticket list for the selected ticket.
Right-click > Edit Info... opens it (seeding it from a template if
missing); New Ticket... seeds one too. **Keep it in sync by hand** when a
script's steps change — nothing generates it from the code.

The ticket group has **no button row** any more — every ticket action is
in the right-click menu. Right-click a row: Edit Ticket... / Set Icon from
File... / Clear Icon / Edit Script... / Open Ticket Dir.... Right-click the
empty space below the rows: New Ticket... (creates
`tickets/<Name>/<Name>.py`) / Open Tickets Dir.... The old "Refresh
Scripts" rescan is folded into the top **Refresh** button. "Save Default"
and the per-repo `.MayaPublisher` default file are gone — the first listed
ticket is selected; an old `.MayaPublisher` file is ignored.

The list is a `QTreeWidget` (`treeWidget_ticket`) with **Ticket | From |
To** columns. From/To are per-ticket program names stored in
`tickets/<Category>/<script>.json` (`{"from_program": ..., "to_program":
...}`, `function.get_ticket_meta()`/`set_ticket_program()`), picked by
double-clicking the cell (or right-click > Set From/To Program...) from
the active Project's UkoreHub Program DB (`function.list_programs()` —
`project.programs` via `PublishApi.repo_paths.get_active_repo()`, icons
from `<UKOREHUB_DATA_DIR>/assets/program_icons/`). Stored by Program
**name**, not id, since tickets are studio-wide but each Project has its
own Program DB. An icon UkoreHub hasn't pulled to this machine yet shows
as a letter tile. Both values are also passed to the Ticket Script as
`context["from_program"]`/`context["to_program"]`.

The same `.json` also holds an optional **From Repo / To Repo** (repo
names), edited with right-click > **Edit Ticket...** (`TicketSettingsDialog`, which also edits
From/To Program and has an "Open Script..." button; right-click > Edit
Script... still opens the .py directly). `from_repo`: the ticket only
works while that repo is active — otherwise its row is dimmed and Publish
/ the destination preview refuse with a message
(`function.check_ticket_from_repo()`). `to_repo`: only that repo's Custom
Paths are searched for the ticket's label, instead of the first match
across the active repo + pipeline connections; it must be the active repo
or one of its pipeline connections. Both go through
`function.get_publish_root_for_ticket()`, and are passed to the script as
`context["from_repo"]`/`context["to_repo"]`. Unset = old behavior.

Columns are **Ticket | From | From Repo | To | To Repo** (double-click a
program cell to pick a program, a repo cell to open Edit Ticket...). Above the
list, **Show match current repo** (`checkBox_show_match_repo`, checked in
`ui.ui` and never persisted, so it's on every time the window opens) lists
only tickets whose `from_repo` is the active repo or unset
(`function.ticket_matches_active_repo()`); unchecking it shows every
ticket, with non-matching ones dimmed.

Window layout: two side-by-side groups. Left, **Choose Ticket**: an
"Active Repo : <name> [📋] … [Refresh]" row (the old "Publish Metadata"
group is gone — the repo path is no longer shown, only copied by
`pushButton_copy_repo_path` / shown in the name's tooltip), the match
checkbox, the ticket list and its buttons. Right, **Publish Info**:
snapshot + version info, then "Target Publish Path" (`label_publish_root`,
moved here), the note box and Publish. `label_publish_result` spans both
underneath.

**2026-10-11: Ticket Visibility Setting tab removed.** The per-repo
"Ticket Visibility" tab below (`ticket_visibility_settings_page.py`,
`get_hidden_ticket_scripts()`, `Repo.plugin_data["maya_publisher"]
["hidden_tickets"]`) is gone — `plugin.py` no longer registers any
Settings tab, and `list_ticket_scripts()` no longer filters. Each ticket's
own `from_repo` + the "Show match current repo" filter replace it. Any
`hidden_tickets` data still in a repo's plugin_data is simply ignored.
The rest of this paragraph is history:

**2026-08-26: per-repo Ticket Visibility Setting tab.** This plugin has an
UkoreHub-side UI again — one tab, Repository Setting > MayaPublisher >
"Ticket Visibility" (`ticket_visibility_settings_page.py`,
`CATEGORY_REPO`). It lists every bundled Ticket Script grouped by
category and lets a studio admin uncheck the ones this repo's artists
shouldn't see; unchecked names are written to
`Repo.plugin_data["maya_publisher"]["hidden_tickets"]`
(`{category: [script_name, ...]}`) via
`MetadataStore.get_repo_plugin_data`/`set_repo_plugin_data` — inside the
repo's own project blob (`data/projects/<id>.json`), so this is
cloud-synced like everything else there, same convention
`plugins/core/explorer/bookmarks_store.py` uses for bookmarks. Maya-side,
`function.list_ticket_scripts()` now filters through
`function.get_hidden_ticket_scripts()`, which reads that same
`Repo.plugin_data` straight off disk (no live PluginAPI in Maya's
process) the same way `PublishApi.repo_paths.get_custom_paths()`/
`get_pipeline_refs()` already do — so a hidden script simply never
appears in the Maya window's "Choose Ticket Scripts" list for that repo.
Category visibility is unaffected — this only hides individual ticket
scripts within a category, not whole categories.

**2026-08-19: per-asset/shot block-name subfolder.** `publish()` now
creates an extra subfolder between the ticket script's own folder and its
version folder, named after the active scene's filename's first
`_`-separated "block" (`function.get_scene_block_name()`, e.g.
`KBA030_rig_v012.ma` -> `KBA030`) — so a publish now lands at
`<publish_root>/<script_name>/<block_name>/vXXX` instead of
`<publish_root>/<script_name>/vXXX`. `get_scene_block_name()` raises the
same artist-facing error the tool already raised for an unsaved scene
(`publish()` has always refused to run against an unsaved scene — this
just also covers a filename with no usable block name), so Publish is
still refused outright whenever the current scene has no name / hasn't
been saved.

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
+ Ticket Scripts (current design)" below for what replaced them. Category
picking/creating/editing a Ticket Script all still happen entirely in the
Maya-side window; the only UkoreHub-side UI this plugin has is the
per-repo Ticket Visibility Setting tab added 2026-08-26 (see above), which
only controls which of the already-bundled scripts a repo's artists see.

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
  other Maya tool plugin here uses, e.g. `cache/plugins/PublishApi/plugin.py`),
  and registers the `CATEGORY_REPO` "Ticket Visibility" Settings tab
  (`_load_ticket_visibility_page_class` loads
  `ticket_visibility_settings_page.py` by file path via
  `importlib.util.spec_from_file_location`, not a normal import — see that
  file's own docstring for why). Relies on `cache/plugins/MayaToolkit` (for
  `UkoreMaya.core.Pipeline`'s export functions — no longer imported by this
  plugin's own `function.py`, but still what a Ticket Script would
  typically call to actually export something) and `cache/plugins/PublishApi`
  (for path resolution/versioning) also being enabled — not imported
  directly, just expected on the same merged PYTHONPATH once Maya
  launches.
- `ticket_visibility_settings_page.py` — the "Ticket Visibility" Settings
  tab widget itself (`TicketVisibilitySettingsPage`, plain `PySide6`, no
  `plugin_api`/`core`/`interface` imports — everything it needs is passed
  in by `plugin.py`). Its own `_list_categories()`/`_list_ticket_scripts()`
  are a separate copy of `function.py`'s tickets/ scan, not a shared import
  — `function.py` imports `maya.cmds` at module level so it can't be
  imported outside Maya, and the two live on different PYTHONPATHs anyway
  (Maya only sees `maya-scripts/`, not this plugin's own root). See the
  2026-08-26 changelog entry above for the storage format.
- `maya-scripts/MayaPublisher/function.py` — `TOOL_ID = "maya_publisher"`.
  `list_categories()`/`list_ticket_scripts()`/`category_dir()`/
  `script_path()`: read `tickets/` straight off disk.
  `get_hidden_ticket_scripts(category)`: this repo's hidden-script names
  for `category` (2026-08-26, see above) — `list_ticket_scripts()` filters
  through it. `create_ticket_script()`:
  seeds a new Ticket Script from `_TICKET_SCRIPT_TEMPLATE`.
  `get_default_category()`/`set_default_category()`: this repo's
  `.MayaPublisher` file. `get_active_repo_display()`: `(repo_name,
  repo_path)` for the UkoreHub-active repo, or `(None, None)` — feeds
  `label_active_repo_name`/`label_active_repo_path`.
  `get_publish_root_for_category()`: Custom Path label match (see above),
  returns `(publish_root, target_repo_name)` — `target_repo_name` feeds
  `label_repo_target_name` and can differ from the active repo when the
  match comes from a pipeline connection. `get_scene_block_name()`: first
  `_`-separated block of the active scene's filename (no extension), e.g.
  `KBA030_rig_v012.ma` -> `KBA030` — raises the same artist-facing
  RuntimeError as an unsaved scene if there's no usable block name.
  `get_version_info()`: `(latest_version, next_version)` for a
  category+ticket+block's own publish subfolder (`latest_version` is
  `None` if nothing's published there yet) — feeds
  `label_lastest_publish_version`/`label_next_publish_version`.
  `run_ticket_script()`: same `importlib.util.spec_from_file_location`/
  `exec_module` dispatch `PublishApi.tickets.run_validation_scripts` uses,
  just pointed at `tickets/<category>/<script_name>.py` instead of a
  repo's own `PublishValidation/<tool_id>/`. `publish(category,
  script_name)`: resolves the scene's block name, the publish root, and
  the next version via `PublishApi` (creating
  `<publish_root>/<script_name>/<block_name>/vXXX`), builds a `context`
  dict (`version_dir`, `version`, `category`, `script_name`, `tool_id`),
  then runs the selected Ticket Script with that context — **that script
  decides what to export/copy into `context["version_dir"]`**, not this
  function (see "Publish is a scripted step" below).
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
