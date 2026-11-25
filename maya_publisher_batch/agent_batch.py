"""Headless batch publish started from a request file instead of the page, so
an agent (UkoreHub Agent Bridge `file_action`, key AGENT_ACTION_KEY) runs the
exact queue the Maya Publisher page does: same plan_publish checks, linked
Maya + repo env (resolve_maya_launch), vNNN reservation, parallel mayapy.

Request <name>.json:
    {"scenes": [str], "category": str, "script_name": str,
     "parallel": int = 2, "note": str = "", "repo": str | null}
Or a script request {"script": "<file.py>", "args": [str], "repo": str | null}:
one `mayapy <file.py>` with the same launch (env + UKORE_MAYA_PLUGINS, the
repo's plug-ins to load, and UKORE_MAYA_PROJECT); output goes to <name>.log,
status {"state", "exit_code", "log_path", "elapsed_sec"}. For agent jobs on
scenes (fix-ups before a publish) without opening a Maya window.

`repo` (name or id) only guards against publishing from the wrong active
repo; the batch always runs from UkoreHub's active repo, like the page.

Progress goes to <name>.status.json next to it, rewritten on every job
change: {"state": "running" | "done" | "rejected", "message", "repo",
"work_dir", "jobs": [{"scene", "status", "message", "version_dir",
"version", "log_path", "elapsed_sec"}]}. Nothing is queued unless every
scene plans cleanly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import batch_queue, publish_plan
from .maya_env import MayaEnvError, resolve_maya_launch

AGENT_ACTION_KEY = "maya_publisher_agent_batch"

# The queue has no parent widget (the page may never have been opened), so
# this keeps it alive until it finishes.
_running: list = []


def _status_path(request_path: Path) -> Path:
    return request_path.with_name(request_path.stem + ".status.json")


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _reject(status_path: Path, message: str) -> None:
    _write(status_path, {"state": "rejected", "message": message, "jobs": []})


def _active_repo(api, status_path: Path, wanted):
    config = api.local_config
    project_id, repo_id = config.active_project_id, config.active_repo_id
    try:
        repo = api.metadata.get_repo(project_id, repo_id) if project_id and repo_id else None
    except Exception:  # noqa: BLE001 - NotFoundError, or a registry mid-sync
        repo = None
    if repo is None:
        _reject(status_path, "no active repo in UkoreHub")
        return None, None
    if wanted and wanted not in (repo.name, repo.id):
        _reject(status_path, f"active repo is '{repo.name}', request wants '{wanted}'")
        return None, None
    return project_id, repo


def _run_script(api, request: dict, request_path: Path, status_path: Path) -> None:
    """{"script": "<file.py>", "args": [str], "repo": ...}: one headless
    `mayapy <script> <args>` with the linked Maya and the repo's tool env, the
    same launch a publish job gets. The script initializes maya.standalone
    itself. Status: state, exit_code, log_path, elapsed_sec."""
    from PySide6.QtCore import QProcess, QProcessEnvironment
    import time

    project_id, repo = _active_repo(api, status_path, request.get("repo"))
    if repo is None:
        return
    script = Path(request["script"])
    if not script.is_file():
        _reject(status_path, f"script not found: {script}")
        return
    try:
        launch = resolve_maya_launch(api, project_id, repo)
    except MayaEnvError as exc:
        _reject(status_path, str(exc))
        return

    log_path = request_path.with_name(request_path.stem + ".log")
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    started = time.time()
    process = QProcess()
    env = QProcessEnvironment()
    for key, value in launch.env.items():
        env.insert(key, value)
    env.insert("UKORE_MAYA_PLUGINS", ";".join(launch.plugin_names))
    env.insert("UKORE_MAYA_PROJECT", str(launch.project_dir))
    process.setProcessEnvironment(env)
    process.setWorkingDirectory(str(script.parent))
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

    def status(state: str, exit_code=None) -> None:
        _write(status_path, {"state": state, "repo": repo.name, "script": str(script),
                             "exit_code": exit_code, "log_path": str(log_path),
                             "elapsed_sec": round(time.time() - started, 1)})

    def on_output() -> None:
        log.write(bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace"))
        log.flush()

    def on_finished(code, _status=None) -> None:
        on_output()
        log.close()
        status("done", code)
        if process in _running:
            _running.remove(process)

    def on_error(error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            log.close()
            _reject(status_path, f"mayapy failed to start: {launch.mayapy}")
            if process in _running:
                _running.remove(process)

    process.readyReadStandardOutput.connect(on_output)
    process.finished.connect(on_finished)
    process.errorOccurred.connect(on_error)
    _running.append(process)
    status("running")
    process.start(launch.mayapy, [str(script), *[str(a) for a in request.get("args", [])]])


def run_request(api, request_path: Path) -> None:
    request_path = Path(request_path)
    status_path = _status_path(request_path)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if "script" in request:
            _run_script(api, request, request_path, status_path)
            return
        scenes = [str(s) for s in request["scenes"]]
        category, script_name = request["category"], request["script_name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _reject(status_path, f"bad request file: {exc}")
        return

    config = api.local_config
    project_id, repo_id = config.active_project_id, config.active_repo_id
    try:
        repo = api.metadata.get_repo(project_id, repo_id) if project_id and repo_id else None
    except Exception:  # noqa: BLE001 - NotFoundError, or a registry mid-sync
        repo = None
    if repo is None:
        _reject(status_path, "no active repo in UkoreHub")
        return
    wanted = request.get("repo")
    if wanted and wanted not in (repo.name, repo.id):
        _reject(status_path, f"active repo is '{repo.name}', request wants '{wanted}'")
        return

    plans = [publish_plan.plan_publish(api, project_id, repo_id, scene, category, script_name) for scene in scenes]
    problems = [f"{p.scene_path}: {p.error}" for p in plans if not p.ok]
    if problems:
        _reject(status_path, "; ".join(problems))
        return
    try:
        launch = resolve_maya_launch(api, project_id, repo)
        work_dir = batch_queue.new_work_dir(api.cache_dir)
    except (MayaEnvError, OSError) as exc:
        _reject(status_path, str(exc))
        return

    queue = batch_queue.BatchQueue(launch, work_dir, max_standalone=int(request.get("parallel", 2)))
    jobs = [queue.add(plan, note=request.get("note", "")) for plan in plans]

    def snapshot(state: str) -> None:
        _write(status_path, {
            "state": state,
            "message": "",
            "repo": repo.name,
            "work_dir": str(work_dir),
            "jobs": [{
                "scene": job.plan.scene_path,
                "status": job.status,
                "message": job.message,
                "version_dir": job.version_dir,
                "version": job.version,
                "log_path": job.log_path,
                "elapsed_sec": job.elapsed_sec,
            } for job in jobs],
        })

    def on_finished() -> None:
        snapshot("done")
        if queue in _running:
            _running.remove(queue)

    queue.job_started.connect(lambda _job: snapshot("running"))
    queue.job_finished.connect(lambda _job: snapshot("running"))
    queue.finished.connect(on_finished)
    _running.append(queue)
    snapshot("running")
    queue.start()
