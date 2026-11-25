"""Which Maya to run and with what env — the same recipe
cache/plugins/maya_launcher/plugin.py uses to open Maya from UkoreHub, so a
batch job sees exactly what an artist's Maya session sees.

Convention-only duplicate of maya_launcher's _maya_programs_for_repo /
_find_linked_maya / _prepare_env_and_plugins / _build_maya_env /
_force_load_plugin_names / _build_launch_commands, and of its
link_resolution.linked_key / pinned_version (themselves a duplicate of
software_linker's) — plugins can't import each other, and the bridge store
only holds data. Keep these in sync if maya_launcher's recipe changes, same
discipline as MAYA_ENV_BRIDGE_PLUGIN_ID.

Differences from a normal launch, both deliberate:
- PYTHONHOME/PYTHONEXECUTABLE/PYTHONSTARTUP are dropped from the inherited
  env — they'd point mayapy at UkoreHub's own Python.
- This plugin's own maya-scripts/ is always on PYTHONPATH (batch_runner.py
  lives there), even if the repo doesn't list maya_publisher.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from plugin_api import NotFoundError

from .tickets import MAYA_SCRIPTS_DIR

SOFTWARE_LINKER_PLUGIN_ID = "software_linker"
MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
PUBLISH_API_TOOL_ID = "publish_api"
ANY_VERSION = "*"
_PLUGIN_FILE_EXTENSIONS = {".py", ".mll", ".pyd", ".so"}
_DROPPED_ENV_VARS = ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONSTARTUP")


class MayaEnvError(RuntimeError):
    """No usable Maya for this repo — message is user-facing."""


@dataclass
class MayaLaunch:
    maya_exe: str
    mayapy: str
    version: str
    project_dir: str
    env: dict
    plugin_names: list = field(default_factory=list)
    cli_flags: list = field(default_factory=list)
    pre_open_mel: list = field(default_factory=list)
    post_open_mel: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)


    def gui_args(self, job_path: str) -> list[str]:
        """maya.exe argv for a GUI job — maya_launcher's own launch command
        (force-load plug-ins, diagnostics, pre hooks, setProject, post hooks)
        with batch_runner.run_in_gui() in place of opening a scene."""
        runner_call = f"import MayaPublisher.batch_runner as r; r.run_in_gui({_py_str(job_path)})"
        mel = _force_load_plugins_command(self.plugin_names)
        mel += "".join(f'print("[MayaLauncher] {diag}\\n");' for diag in self.diagnostics)
        mel += " ".join(self.pre_open_mel)
        mel += f'setProject "{_mel_string(self.project_dir)}";'
        mel += " ".join(self.post_open_mel)
        mel += f'python("{_mel_escape(runner_call)}");'
        return [self.maya_exe, *self.cli_flags, "-command", mel]


def _mel_string(path) -> str:
    return str(path).replace("\\", "/").replace('"', '\\"')


def _mel_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _py_str(path) -> str:
    """A path as a Python string literal (forward slashes, no escapes needed)."""
    return repr(str(path).replace("\\", "/"))


def _linked_key(program, version: str = "") -> str:
    if len(program.versions) <= 1:
        return program.id
    return f"{program.id}:{version}"


def _pinned_version(repo, program) -> str:
    pin = repo.program_version_pins.get(program.id)
    if pin and pin in program.versions:
        return pin
    return program.versions[0] if program.versions else ""


def _maya_programs_for_repo(api, project_id, repo) -> list:
    programs = []
    for program_id in repo.required_program_ids:
        try:
            program = api.metadata.get_program(project_id, program_id)
        except NotFoundError:
            continue
        if "maya" in program.name.lower():
            programs.append(program)
    return programs


def find_linked_maya(api, project_id, repo) -> tuple[str, str] | None:
    """(maya_exe, version) of the repo's required Maya Program, as linked on
    this machine under Settings > Software Linker — or None."""
    linked = api.plugin_config_store(SOFTWARE_LINKER_PLUGIN_ID, shared=False)
    for program in _maya_programs_for_repo(api, project_id, repo):
        version = _pinned_version(repo, program)
        candidate = linked.get(_linked_key(program, version))
        if candidate:
            return candidate, version
    return None


def _read_bridge(api) -> tuple[dict, dict]:
    bridge = api.project_plugin_config_store(MAYA_ENV_BRIDGE_PLUGIN_ID)
    if bridge is None:
        return {}, {}
    return bridge.get("contributions", {}), bridge.get("launch_hooks", {})


def _build_maya_env(base_env: dict, contributions: dict, maya_version: str) -> dict:
    env = dict(base_env)
    for tool_id in sorted(contributions):
        for var_name, by_version in contributions[tool_id].items():
            paths = list(by_version.get(ANY_VERSION, [])) + list(by_version.get(maya_version, []))
            for new_entry in paths:
                existing = env.get(var_name)
                env[var_name] = f"{new_entry}{os.pathsep}{existing}" if existing else new_entry
    return env


def _force_load_plugin_names(contributions: dict, maya_version: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for tool_id in sorted(contributions):
        by_version = contributions[tool_id].get("MAYA_PLUG_IN_PATH", {})
        paths = list(by_version.get(ANY_VERSION, [])) + list(by_version.get(maya_version, []))
        for path in paths:
            folder = Path(path)
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if entry.is_file() and entry.suffix.lower() in _PLUGIN_FILE_EXTENSIONS and entry.stem not in seen:
                    seen.add(entry.stem)
                    names.append(entry.stem)
    return names


def _force_load_plugins_command(plugin_names: list[str]) -> str:
    return "".join('catch(`loadPlugin -quiet "{}"`);'.format(name) for name in plugin_names)


def _ensure_on_path(env: dict, var_name: str, entry: str) -> None:
    parts = [p for p in env.get(var_name, "").split(os.pathsep) if p]
    if os.path.normcase(entry) not in {os.path.normcase(p) for p in parts}:
        env[var_name] = os.pathsep.join([entry, *parts])


def resolve_maya_launch(api, project_id: str, repo) -> MayaLaunch:
    """Everything needed to start Maya for `repo` the way maya_launcher
    would. Raises MayaEnvError (user-facing) if no Maya is linked, or the
    linked maya.exe has no mayapy.exe next to it."""
    found = find_linked_maya(api, project_id, repo)
    if found is None:
        raise MayaEnvError(
            "No linked Maya executable found for this repo's required Maya version. "
            "Configure it in Settings > Software Linker."
        )
    maya_exe, version = found
    mayapy = Path(maya_exe).with_name("mayapy.exe")
    if not mayapy.is_file():
        raise MayaEnvError(f"mayapy.exe not found next to the linked Maya: {maya_exe}")

    # Same tool filter as maya_launcher's _prepare_env_and_plugins: a bridge
    # entry only counts if the repo enables that tool AND it's a plugin
    # actually discovered this launch (PublishApi always counts).
    all_contributions, all_hooks = _read_bridge(api)
    discovered = {p.manifest.id for p in api.plugin_catalog}
    enabled = set(repo.required_plugin_ids) & set(all_contributions) & discovered
    contributions = {tid: c for tid, c in all_contributions.items() if tid in enabled}
    if PUBLISH_API_TOOL_ID in all_contributions and PUBLISH_API_TOOL_ID in discovered:
        contributions[PUBLISH_API_TOOL_ID] = all_contributions[PUBLISH_API_TOOL_ID]
    hooks = sorted((h for tid, h in all_hooks.items() if tid in enabled), key=lambda h: h.get("order", 100))

    base_env = {k: v for k, v in os.environ.items() if k.upper() not in _DROPPED_ENV_VARS}
    env = _build_maya_env(base_env, contributions, version or "")
    _ensure_on_path(env, "PYTHONPATH", str(MAYA_SCRIPTS_DIR))
    # Output goes to a QProcess pipe: without these, Python picks cp1252
    # (a print of "✔"/Thai text raises) and block-buffers (lines before a
    # crash never reach the log). batch_runner also reconfigures its own
    # streams; these cover everything that prints before it runs.
    env["PYTHONIOENCODING"] = "utf-8:replace"
    env["PYTHONUNBUFFERED"] = "1"

    launch = MayaLaunch(
        maya_exe=str(maya_exe),
        mayapy=str(mayapy),
        version=version or "",
        project_dir=str(Path(api.local_config.workspace_root) / repo.local_path),
        env=env,
        plugin_names=_force_load_plugin_names(contributions, version or ""),
    )
    for hook in hooks:
        if isinstance(hook.get("cli_flags"), list):
            launch.cli_flags.extend(hook["cli_flags"])
        if hook.get("pre_open_mel"):
            launch.pre_open_mel.append(hook["pre_open_mel"].strip())
        if hook.get("post_open_mel"):
            launch.post_open_mel.append(hook["post_open_mel"].strip())
        if hook.get("diagnostic_msg"):
            launch.diagnostics.append(hook["diagnostic_msg"].strip())
    return launch
