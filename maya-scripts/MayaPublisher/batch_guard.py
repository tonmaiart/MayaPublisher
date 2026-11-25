"""Unattended-mode stand-ins for Maya's blocking UI commands.

A batch publish (batch_runner.py) has nobody sitting at Maya to click a
dialog — in headless mayapy there's no UI at all, and in a GUI maya.exe a
modal dialog would just hang the job until UkoreHub's timeout kills it.
Ticket Scripts and the libraries they call were written for an artist at
the keyboard, though — e.g. tmlib.core.Scene.clean_up_scene() (used by
RigToAnimPublish/Proxy) asks "Yes/No" with confirmDialog, and several
tmlib/UkoreMaya helpers show an inViewMessage when they finish.

install() swaps those maya.cmds functions for non-blocking versions:

- confirmDialog: answered automatically with its defaultButton (else its
  first button) — the answer the dialog itself suggests. Every auto-answer
  is recorded so it shows up in the job's result.
- inViewMessage: printed to the log instead of drawn.
- promptDialog / fileDialog / fileDialog2 / layoutDialog: need real input,
  so they raise RuntimeError — a ticket that hits one can't run unattended
  and has to be fixed rather than guessed at.

Only code that calls these through the `maya.cmds` module (`cmds.x(...)`,
the convention everywhere here) is covered — a `from maya.cmds import x`
taken before install(), or a MEL-side dialog, is not.
"""

from __future__ import annotations

import re

LOG_PREFIX = "[MayaPublisherBatch]"
INPUT_DIALOGS = ("promptDialog", "fileDialog", "fileDialog2", "layoutDialog")

_installed = False


def _flag(kwargs: dict, long_name: str, short_name: str, default=None):
    if long_name in kwargs:
        return kwargs[long_name]
    return kwargs.get(short_name, default)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _strip_markup(text) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def install(dialog_log: list) -> None:
    """Patches maya.cmds in place for the rest of this Maya session.
    Appends one dict per auto-answered/suppressed dialog to `dialog_log`.
    Calling it twice is a no-op (the first log list keeps receiving)."""
    global _installed
    if _installed:
        return

    import maya.cmds as cmds

    def confirm_dialog(*args, **kwargs):
        if _flag(kwargs, "query", "q") or _flag(kwargs, "edit", "e"):
            return None
        buttons = _as_list(_flag(kwargs, "button", "b"))
        answer = _flag(kwargs, "defaultButton", "db") or (buttons[0] if buttons else "Confirm")
        entry = {
            "dialog": "confirmDialog",
            "title": _flag(kwargs, "title", "t", ""),
            "message": _flag(kwargs, "message", "m", ""),
            "answer": answer,
        }
        dialog_log.append(entry)
        print(f"{LOG_PREFIX} confirmDialog auto-answered {answer!r}: {_strip_markup(entry['message'])[:200]}")
        return answer

    def in_view_message(*args, **kwargs):
        text = _flag(kwargs, "assistMessage", "amg") or _flag(kwargs, "message", "msg") or ""
        print(f"{LOG_PREFIX} inViewMessage: {_strip_markup(text)}")

    def refuse(name):
        def _refuse(*args, **kwargs):
            dialog_log.append({"dialog": name, "answer": None})
            raise RuntimeError(f"{name} needs user input, which a batch publish can't give - fix the ticket script.")

        return _refuse

    cmds.confirmDialog = confirm_dialog
    cmds.inViewMessage = in_view_message
    for name in INPUT_DIALOGS:
        setattr(cmds, name, refuse(name))
    _installed = True
