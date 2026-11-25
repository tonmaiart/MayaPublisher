"""maya-scripts/MayaPublisher/ticket_store.py, loaded by file path.

It's the same file function.py uses inside Maya — no Maya/PublishApi
imports — so both sides read tickets/ through one implementation. Loaded
with spec_from_file_location rather than imported, because maya-scripts/
isn't on UkoreHub's sys.path and importing the MayaPublisher package
would run its Maya-side __init__.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MAYA_SCRIPTS_DIR = PLUGIN_ROOT / "maya-scripts"
RUNNER_PATH = MAYA_SCRIPTS_DIR / "MayaPublisher" / "batch_runner.py"
_TICKET_STORE_PATH = MAYA_SCRIPTS_DIR / "MayaPublisher" / "ticket_store.py"


def _load():
    spec = importlib.util.spec_from_file_location("maya_publisher_ticket_store", _TICKET_STORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ticket_store = _load()
