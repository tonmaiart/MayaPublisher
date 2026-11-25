"""Runs one MayaPublisher publish without an artist — the Maya-side half of
UkoreHub's batch publish. UkoreHub writes a job .json, starts Maya with it,
and reads the result .json back; one Maya process publishes one scene, so
whatever a Ticket Script does to the scene can't leak into the next file,
and the scene is never saved back.

Two ways to start it, picked by the ticket's batch_mode
(function.get_ticket_batch_mode()):

    "standalone" — headless, no viewport:
        mayapy.exe <maya-scripts>/MayaPublisher/batch_runner.py --job <job.json>

    "gui" — a real maya.exe window, for a ticket that needs a viewport:
        maya.exe -command "python(\"import MayaPublisher.batch_runner as r; r.run_in_gui(r'<job.json>')\")"
        (quits Maya by itself when done, unless the job sets keep_open)

Either way Maya needs the same env Maya Launcher gives it (the
maya_launcher_env_bridge contributions) so MayaPublisher, PublishApi,
MayaToolkit, ... are importable.

Job .json:

    {
      "scene_path": str,          # required — .ma/.mb to open
      "category": str,            # required — ticket category (tickets/ subfolder)
      "script_name": str,         # required — ticket script name (no .py)
      "result_path": str,         # required — where the result .json is written
      "version_dir": str | null,  # pre-created vNNN folder; null = resolve + create here (active repo)
      "version": int | null,      # null = parsed from version_dir's name
      "project_dir": str | null,  # Maya project (workspace) to set before opening
      "load_plugins": [str],      # Maya plug-ins to load first (fbxmaya is always tried)
      "startup_mel": str | null,  # MEL to run before opening (e.g. launch hooks' pre_open_mel)
      "note": str | null,         # written to <version_dir>/note.txt on success
      "keep_open": bool           # gui mode only — leave Maya open afterwards (debugging)
    }

Result .json: {"ok", "message", "mode", "scene_path", "category",
"script_name", "version_dir", "version", "dialogs", "started_at",
"finished_at", "elapsed_sec"}. Exit code: 0 published, 1 failed, 2 the job
file itself was unusable (no result .json can be written then).
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import sys
import time
import traceback

LOG_PREFIX = "[MayaPublisherBatch]"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_JOB = 2
REQUIRED_KEYS = ("scene_path", "category", "script_name", "result_path")
ALWAYS_LOAD_PLUGINS = ("fbxmaya",)


class JobError(Exception):
    """The job .json is missing/unreadable/incomplete."""


def _log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


def load_job(job_path: str) -> dict:
    try:
        with open(job_path, encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError) as exc:
        raise JobError(f"Can't read job file {job_path}: {exc}") from exc
    if not isinstance(job, dict):
        raise JobError(f"Job file {job_path} isn't a JSON object.")
    missing = [key for key in REQUIRED_KEYS if not job.get(key)]
    if missing:
        raise JobError(f"Job file {job_path} is missing: {', '.join(missing)}")
    return job


def _write_result(path: str, result: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def _load_plugins(names) -> None:
    import maya.cmds as cmds

    for name in list(ALWAYS_LOAD_PLUGINS) + [n for n in names if n not in ALWAYS_LOAD_PLUGINS]:
        try:
            if not cmds.pluginInfo(name, query=True, loaded=True):
                cmds.loadPlugin(name, quiet=True)
        except RuntimeError as exc:
            _log(f"warning: couldn't load plug-in {name!r}: {exc}")


def _open_scene(scene_path: str) -> None:
    """Opens scene_path, tolerating the RuntimeError Maya raises for a scene
    that loads with errors (unknown nodes, a failing scriptNode, ...) as
    long as the scene did open."""
    import maya.cmds as cmds

    if not os.path.isfile(scene_path):
        raise RuntimeError(f"Scene file not found: {scene_path}")
    try:
        cmds.file(scene_path, open=True, force=True, prompt=False, ignoreVersion=True)
    except RuntimeError as exc:
        opened = cmds.file(query=True, sceneName=True)
        if not opened or os.path.normcase(os.path.abspath(opened)) != os.path.normcase(os.path.abspath(scene_path)):
            raise RuntimeError(f"Couldn't open {scene_path}: {exc}") from exc
        _log(f"warning: scene opened with errors: {exc}")


def run_job(job: dict, mode: str) -> int:
    """Publishes job["scene_path"] with its ticket inside the current Maya
    session (already initialized, either mode), writes job["result_path"],
    and returns the exit code."""
    import maya.cmds as cmds
    import maya.mel as mel

    from MayaPublisher import batch_guard

    started = time.time()
    dialogs: list = []
    result = {
        "ok": False,
        "message": "",
        "mode": mode,
        "scene_path": job["scene_path"],
        "category": job["category"],
        "script_name": job["script_name"],
        "version_dir": job.get("version_dir"),
        "version": job.get("version"),
        "dialogs": dialogs,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
    }

    try:
        batch_guard.install(dialogs)
        from MayaPublisher import function

        category, script_name = job["category"], job["script_name"]
        ticket_mode = function.get_ticket_batch_mode(category, script_name)
        if ticket_mode == function.BATCH_MODE_GUI and cmds.about(batch=True):
            raise RuntimeError(f"Ticket '{category}/{script_name}' needs a viewport (batch_mode \"gui\") — run it in GUI mode.")

        _load_plugins(job.get("load_plugins") or [])
        if job.get("startup_mel"):
            try:
                mel.eval(job["startup_mel"])
            except RuntimeError as exc:
                _log(f"warning: startup MEL failed: {exc}")
        if job.get("project_dir"):
            cmds.workspace(job["project_dir"], openWorkspace=True)

        _log(f"opening {job['scene_path']}")
        _open_scene(job["scene_path"])

        _log(f"publishing with ticket {category}/{script_name}")
        version_dir, version = function.publish(category, script_name, job.get("version_dir"), job.get("version"))
        result.update(version_dir=version_dir, version=version)

        if job.get("note"):
            with open(os.path.join(version_dir, "note.txt"), "w", encoding="utf-8") as f:
                f.write(job["note"])

        result.update(ok=True, message=f"Published {script_name} v{version:03d}")
    # BaseException too: a tool calling sys.exit() mid-ticket must still
    # produce a result instead of silently ending the process.
    except BaseException as exc:  # noqa: BLE001 - every failure must end up in the result file
        result["message"] = str(exc) or exc.__class__.__name__
        try:
            traceback.print_exc()
        except Exception:  # noqa: BLE001 - printing the traceback must never stop the result being written
            pass

    finished = time.time()
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(finished))
    result["elapsed_sec"] = round(finished - started, 1)
    _write_result(job["result_path"], result)
    try:
        _log(("OK: " if result["ok"] else "FAILED: ") + result["message"])
    except Exception:  # noqa: BLE001 - same as above
        pass
    return EXIT_OK if result["ok"] else EXIT_FAILED


def run_in_gui(job_path: str) -> None:
    """GUI-mode entry point, called from maya.exe's -command. Deferred to
    idle so the main window and its viewport are fully up before the
    ticket runs."""
    import maya.cmds as cmds

    _setup_output()
    cmds.evalDeferred(lambda: _gui_main(job_path), lowestPriority=True)


def _gui_main(job_path: str) -> None:
    import maya.cmds as cmds

    try:
        job = load_job(job_path)
    except JobError as exc:
        _log(str(exc))
        cmds.quit(force=True, exitCode=EXIT_BAD_JOB)
        return

    code = run_job(job, "gui")
    if job.get("keep_open"):
        _log("keep_open is set — leaving Maya open.")
        return
    cmds.quit(force=True, exitCode=code)


def _setup_output() -> None:
    """Output piped to UkoreHub must never raise and never get lost.
    UTF-8: tickets/tools print "✔" and Thai text, which the default cp1252
    pipe encoding can't encode — the print itself raised, failing the
    publish (2026-11-02). Line-buffered: a pipe is block-buffered, so the
    last lines before a crash used to vanish from the log. faulthandler: a
    hard crash dumps the Python stack into the log instead of nothing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass  # GUI Maya's own output streams
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
    except (AttributeError, ValueError, OSError, RuntimeError):
        pass


def main(argv=None) -> int:
    _setup_output()
    parser = argparse.ArgumentParser(description="Publish one Maya scene with a MayaPublisher ticket (mayapy).")
    parser.add_argument("--job", required=True, help="path to the job .json")
    args = parser.parse_args(argv)

    try:
        job = load_job(args.job)
    except JobError as exc:
        _log(str(exc))
        return EXIT_BAD_JOB

    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        return run_job(job, "standalone")
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:  # noqa: BLE001 - result is already written; don't let teardown change the exit code
            pass


if __name__ == "__main__":
    # Run as a script, sys.path[0] is this package's own folder — drop it so
    # function.py/interface.py can't be imported as top-level modules (and
    # shadow anything else named that); MayaPublisher comes from PYTHONPATH.
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _here]
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit: skip interpreter teardown, which can hang/crash after
    # maya.standalone on some Maya versions and would lose the exit code.
    os._exit(exit_code)
