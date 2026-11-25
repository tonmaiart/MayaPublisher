"""Runs a list of planned publishes, one Maya process per scene.

Each job's ticket batch_mode picks the process: "standalone" runs
`mayapy batch_runner.py --job <job.json>`, "gui" runs maya.exe with
batch_runner.run_in_gui() (MayaLaunch.gui_args). The two kinds have
separate concurrency limits — a GUI Maya is far heavier. The vNNN folder
is reserved (publish_plan.reserve_version_dir) right before a job starts,
in this thread, so parallel jobs never race for a number; if the job fails
and leaves it empty, it's removed again.

Files per job, in the queue's work_dir: <id>.job.json (input),
<id>.result.json (batch_runner's output — the source of truth for
success, since a GUI Maya's exit code isn't reliable), <id>.log (merged
stdout/stderr).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .maya_env import MayaLaunch
from .publish_plan import PlanError, PublishPlan, release_version_dir, reserve_version_dir
from .tickets import RUNNER_PATH, ticket_store

PENDING = "pending"
RUNNING = "running"
OK = "ok"
FAILED = "failed"
CANCELLED = "cancelled"
DONE_STATUSES = (OK, FAILED, CANCELLED)

DEFAULT_STANDALONE_TIMEOUT_SEC = 30 * 60
DEFAULT_GUI_TIMEOUT_SEC = 60 * 60


def new_work_dir(cache_dir) -> Path:
    """<cache_dir>/maya_publisher_batch/<timestamp> — one folder per batch run."""
    path = Path(cache_dir) / "maya_publisher_batch" / time.strftime("%Y%m%d-%H%M%S")
    suffix = 1
    candidate = path
    while candidate.exists():
        suffix += 1
        candidate = path.with_name(f"{path.name}-{suffix}")
    candidate.mkdir(parents=True)
    return candidate


@dataclass(eq=False)
class BatchJob:
    job_id: str
    plan: PublishPlan
    note: str = ""
    status: str = PENDING
    message: str = ""
    version_dir: str = ""
    version: int | None = None
    result: dict = field(default_factory=dict)
    job_path: str = ""
    result_path: str = ""
    log_path: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def mode(self) -> str:
        return self.plan.batch_mode

    @property
    def elapsed_sec(self) -> float | None:
        if self.started_at is None:
            return None
        return round((self.finished_at or time.time()) - self.started_at, 1)


class BatchQueue(QObject):
    job_started = Signal(object)  # BatchJob
    job_output = Signal(object, str)  # BatchJob, text chunk
    job_finished = Signal(object)  # BatchJob (status is OK/FAILED/CANCELLED)
    finished = Signal()  # nothing pending or running anymore

    def __init__(
        self,
        launch: MayaLaunch,
        work_dir,
        max_standalone: int = 2,
        max_gui: int = 1,
        standalone_timeout_sec: int = DEFAULT_STANDALONE_TIMEOUT_SEC,
        gui_timeout_sec: int = DEFAULT_GUI_TIMEOUT_SEC,
        keep_gui_open: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.launch = launch
        self.work_dir = Path(work_dir)
        self.max_slots = {ticket_store.BATCH_MODE_STANDALONE: max(1, max_standalone), ticket_store.BATCH_MODE_GUI: max(1, max_gui)}
        self.timeouts = {ticket_store.BATCH_MODE_STANDALONE: standalone_timeout_sec, ticket_store.BATCH_MODE_GUI: gui_timeout_sec}
        self.keep_gui_open = keep_gui_open
        self.jobs: list[BatchJob] = []
        self._running: dict[BatchJob, QProcess] = {}
        self._timers: dict[BatchJob, QTimer] = {}
        self._cancel_requested: set[BatchJob] = set()
        self._timed_out: set[BatchJob] = set()
        self._started = False

    # ------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------
    def add(self, plan: PublishPlan, note: str = "") -> BatchJob:
        """Queues a plan from publish_plan.plan_publish(). A plan with an
        error is rejected — fix or drop it first."""
        if not plan.ok:
            raise ValueError(f"Can't queue {plan.scene_path}: {plan.error}")
        job = BatchJob(job_id=f"{len(self.jobs) + 1:03d}_{plan.block_name}_{plan.script_name}", plan=plan, note=note)
        base = self.work_dir / job.job_id
        job.job_path, job.result_path, job.log_path = (f"{base}.job.json", f"{base}.result.json", f"{base}.log")
        self.jobs.append(job)
        if self._started:
            self._pump()
        return job

    def start(self) -> None:
        self._started = True
        self._pump()

    def cancel(self) -> None:
        """Drops every pending job and kills every running one."""
        for job in self.jobs:
            if job.status == PENDING:
                self._finish(job, CANCELLED, "Cancelled before it started.")
        for job, process in list(self._running.items()):
            self._cancel_requested.add(job)
            process.kill()
        self._check_all_done()

    def is_running(self) -> bool:
        return bool(self._running) or any(job.status == PENDING for job in self.jobs)

    # ------------------------------------------------------------
    # SCHEDULING
    # ------------------------------------------------------------
    def _running_count(self, mode: str) -> int:
        return sum(1 for job in self._running if job.mode == mode)

    def _pump(self) -> None:
        for job in self.jobs:
            if job.status == PENDING and self._running_count(job.mode) < self.max_slots.get(job.mode, 1):
                self._start_job(job)
        self._check_all_done()

    def _check_all_done(self) -> None:
        if self._started and not self.is_running():
            self._started = False
            self.finished.emit()

    def _start_job(self, job: BatchJob) -> None:
        try:
            job.version_dir, job.version = reserve_version_dir(job.plan.base_dir)
        except (PlanError, OSError) as exc:
            self._finish(job, FAILED, str(exc))
            return

        gui = job.mode == ticket_store.BATCH_MODE_GUI
        payload = {
            "scene_path": job.plan.scene_path,
            "category": job.plan.category,
            "script_name": job.plan.script_name,
            "result_path": job.result_path,
            "version_dir": job.version_dir,
            "version": job.version,
            "project_dir": self.launch.project_dir,
            "load_plugins": [] if gui else self.launch.plugin_names,  # GUI's launch MEL force-loads them already
            # Headless jobs run no launch hooks: they set up an artist's
            # interactive Maya (menus, pickers, scene-open UI). In mayapy
            # that's at best useless and at worst fatal — UkoreReferenceEditor's
            # scene-open callback loads Dreamwall Picker's Qt UI, which aborted
            # mayapy (0xC0000409) on repos enabling dreamwall_picker
            # (2026-11-02). GUI jobs get the hooks via gui_args, like Maya Launcher.
            "startup_mel": None,
            "note": job.note or None,
            "keep_open": gui and self.keep_gui_open,
        }
        try:
            for stale in (job.result_path, job.log_path):
                if os.path.exists(stale):
                    os.remove(stale)
            with open(job.job_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            self._finish(job, FAILED, f"Couldn't write job file: {exc}")
            return

        if gui:
            program, *args = self.launch.gui_args(job.job_path)
        else:
            program, args = self.launch.mayapy, [str(RUNNER_PATH), "--job", job.job_path]

        process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in self.launch.env.items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(self.work_dir))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._on_output(job, process))
        process.finished.connect(lambda code, status: self._on_process_finished(job, code, status))
        process.errorOccurred.connect(lambda error: self._on_process_error(job, error))

        self._running[job] = process
        job.status = RUNNING
        job.started_at = time.time()
        self.job_started.emit(job)
        process.start(program, args)

        timeout = self.timeouts.get(job.mode)
        if timeout and not payload["keep_open"]:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._on_timeout(job))
            timer.start(int(timeout * 1000))
            self._timers[job] = timer

    # ------------------------------------------------------------
    # PROCESS EVENTS
    # ------------------------------------------------------------
    def _on_output(self, job: BatchJob, process: QProcess) -> None:
        text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self._append_log(job, text)

    def _append_log(self, job: BatchJob, text: str) -> None:
        try:
            with open(job.log_path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        self.job_output.emit(job, text)

    def _on_timeout(self, job: BatchJob) -> None:
        process = self._running.get(job)
        if process is not None:
            self._timed_out.add(job)
            process.kill()

    def _on_process_error(self, job: BatchJob, error) -> None:
        # FailedToStart never gets a finished() signal; every other error does.
        if error == QProcess.ProcessError.FailedToStart and job in self._running:
            process = self._running.pop(job)
            self._stop_timer(job)
            self._finish(job, FAILED, f"Couldn't start Maya: {process.errorString()}")
            process.deleteLater()
            self._pump()

    def _on_process_finished(self, job: BatchJob, exit_code: int, exit_status=None) -> None:
        process = self._running.pop(job, None)
        if process is None:
            return
        self._on_output(job, process)
        self._stop_timer(job)
        process.deleteLater()

        # Windows reports a crash as a negative NTSTATUS code — hex is what
        # can be looked up (0xC0000005 access violation, 0xC0000409 ...).
        code_text = f"0x{exit_code & 0xFFFFFFFF:08X}" if exit_code < 0 or exit_code > 0xFFFF else str(exit_code)
        crashed = exit_status == QProcess.ExitStatus.CrashExit or code_text.startswith("0xC")
        self._append_log(job, f"\n[MayaPublisherBatch queue] Maya process ended: exit code {code_text}{' (crash)' if crashed else ''}\n")

        result = self._read_result(job)
        if job in self._cancel_requested:
            status, message = CANCELLED, "Cancelled while running."
        elif job in self._timed_out:
            status, message = FAILED, f"Timed out after {self.timeouts.get(job.mode)} s — Maya was killed."
        elif result is None:
            what = "Maya crashed" if crashed else "Maya exited"
            status, message = FAILED, f"{what} (exit code {code_text}) without writing a result — see the log."
        else:
            job.result = result
            status = OK if result.get("ok") else FAILED
            message = result.get("message", "")
        self._finish(job, status, message)
        self._pump()

    # ------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------
    def _stop_timer(self, job: BatchJob) -> None:
        timer = self._timers.pop(job, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    @staticmethod
    def _read_result(job: BatchJob) -> dict | None:
        try:
            with open(job.result_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _finish(self, job: BatchJob, status: str, message: str) -> None:
        job.status = status
        job.message = message
        job.finished_at = time.time()
        if status != OK and job.version_dir and release_version_dir(job.version_dir):
            job.version_dir = ""
            job.version = None
        self.job_finished.emit(job)
