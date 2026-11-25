"""Ticket script for MayaPublisher.

validate() runs when this ticket script is selected in MayaPublisher's
"Choose Ticket Scripts" list and Publish is pressed. Return True to let the
publish proceed, False to block it and show an artist-facing error. Raising
an exception also blocks the publish, with the exception message shown the
same way.

Accept one optional argument, `context`, to also do real publish work
(copy/export files) instead of just checking something:

    context = {
        "version_dir": str,   # already-created destination folder for this publish
        "version": int,       # version number just created, e.g. 3 for v003
        "category": str,      # this ticket script's category, e.g. "CHR_PublishPath"
        "script_name": str,   # this ticket script's own file name (no .py)
        "tool_id": str,       # "maya_publisher"
    }
"""

import os

import maya.cmds as cmds
import maya.mel as mel

from tmlib.core import Scene


def validate(context):
    clean_up_scene()

    version = context["version"]
    script_name = context["script_name"]
    ma_path = os.path.join(context["version_dir"], "{}_v{:03d}.ma".format(script_name, version))
    # Export the cleaned scene in memory — Pipeline.export_maya_common()
    # would publish the saved file as it was before this clean-up (same
    # reason RigToAnimPublish/Proxy.py exports this way).
    cmds.file(
        ma_path.replace("\\", "/"),
        force=True,
        exportAll=True,
        type="mayaAscii",
        preserveReferences=True,
    )

    print("Exported Maya Ascii file to: {}".format(ma_path))

    return True


def clean_up_scene():
    Scene.delete_all_delete_grp()
    delete_unused_nodes()
    reset_preview_division_levels()


def delete_unused_nodes():
    """Hypershade > Edit > Delete Unused Nodes — also removes materials
    (and their shading groups/textures) not assigned to anything."""
    mel.eval("MLdeleteUnused;")
    print("Deleted unused nodes and materials.")


def reset_preview_division_levels():
    """Attribute Editor > Smooth Mesh > Preview Division Levels -> 0 on
    every mesh shape in the scene. Skips (with a warning) any shape whose
    smoothLevel is locked or driven by a connection."""
    shapes = cmds.ls(type="mesh", long=True) or []
    changed = 0
    for shape in shapes:
        plug = "{}.smoothLevel".format(shape)
        if cmds.getAttr(plug) == 0:
            continue
        if cmds.getAttr(plug, lock=True) or cmds.listConnections(plug, source=True, destination=False):
            cmds.warning("Can't reset Preview Division Levels on {} (locked/connected).".format(shape))
            continue
        cmds.setAttr(plug, 0)
        changed += 1
    print("Preview Division Levels -> 0 on {} of {} mesh shape(s).".format(changed, len(shapes)))
