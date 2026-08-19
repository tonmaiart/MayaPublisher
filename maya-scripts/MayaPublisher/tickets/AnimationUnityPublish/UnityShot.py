"""Ticket script for MayaPublisher.

validate() runs when this ticket script is selected in MayaPublisher's
"Choose Ticket Scripts" list and Publish is pressed. Return True to let the
publish proceed, False to block it and show an artist-facing error. Raising
an exception also blocks the publish, with the exception message shown the
same way.

Accept one optional argument, `context`, to also do real publish work
(copy/export files) instead of just checking something — a script with a
plain validate() (no arguments) still works exactly like a check-only
script:

    context = {
        "version_dir": str,   # already-created destination folder for this publish
        "version": int,       # version number just created, e.g. 3 for v003
        "category": str,      # this ticket script's category, e.g. "Rig"
        "script_name": str,   # this ticket script's own file name (no .py)
        "tool_id": str,       # "maya_publisher"
    }
"""

# import os
# from UkoreMaya.core import Pipeline


def validate(context):
    # Example — copy the current scene as a Maya Ascii file into this
    # publish's version folder. Replace with whatever this ticket script
    # actually needs to check and/or export, or remove the context
    # argument entirely for a check-only script.
    #
    # version = context["version"]
    # script_name = context["script_name"]
    # ma_path = os.path.join(context["version_dir"], f"{script_name}_v{version:03d}.ma")
    # Pipeline.export_maya_common(export_file_path=ma_path)

    return True
