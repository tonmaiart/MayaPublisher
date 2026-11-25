"""UkoreHub-side batch publish for MayaPublisher — runs inside UkoreHub's
own process, so it imports only plugin_api/PySide6/stdlib (never core.* /
interface.*, and never the Maya-side maya-scripts/MayaPublisher package,
which imports maya.cmds). See README.md's 2026-10-18 entries.

- tickets.py       — ticket_store.py loaded by file path (shared with Maya)
- maya_env.py      — Maya exe/mayapy + env, same recipe as maya_launcher
- publish_plan.py  — destination resolution + vNNN reservation
- batch_queue.py   — QProcess queue running batch_runner.py per scene
"""
