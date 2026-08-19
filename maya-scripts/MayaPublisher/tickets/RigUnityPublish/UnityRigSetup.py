import os

import maya.cmds as cmds
import maya.mel as mel

from UkoreMaya.core import Pipeline
from UkoreMaya.menu import General

from tmlib.core import Validate,Scene,Utility,Connection


def validate(context):
    clean_up_scene()
    return export_skeleton_mesh(export_path=context["version_dir"])


def clean_up_scene():
    # Scene.clean_up_scene()
    Scene.import_all_references()
    Scene.remove_all_namespaces()
    Scene.delete_all_delete_grp()

def _move_to_world(list_node):
    """Reparent each given transform to world, skipping ones already there."""

    for node in list_node:
        if cmds.listRelatives(node, parent=True, fullPath=True):
            cmds.parent(node, world=True)


def _remove_non_joint_descendants(root_joint):
    """Recheck root_joint's whole hierarchy and delete any node under it
    that isn't a joint (e.g. a leftover locator), so only the joint chain
    remains for FBX export."""

    descendants = (
        cmds.listRelatives(root_joint, allDescendents=True, fullPath=True, type="transform")
        or []
    )

    for node in descendants:
        if cmds.objExists(node) and not cmds.objectType(node, isa="joint"):
            cmds.delete(node)


def bake_and_detach_skeleton(dict_mesh):
    """
    Bake the skeleton's animation onto its joints, break every remaining
    incoming connection driving them (constraints, rig controls), clear any
    visibility keyframes and force every joint's visibility on, then move
    the skeleton root joint(s) to world. Each character's mesh gets the same
    connection-breaking treatment plus its keyframes cleared and visibility
    forced on, before it's moved to world too — leaving a clean, standalone
    joint + mesh hierarchy for FBX export.

    Returns an updated {character: [mesh, ...]} dict — moving a mesh to
    world changes its full DAG path, so dict_mesh's own paths go stale the
    moment that happens. Callers must use the returned dict afterward, not
    the one they passed in.
    """

    start_frame = cmds.playbackOptions(q=True, min=True)
    end_frame = cmds.playbackOptions(q=True, max=True)

    # get every skeleton joint via the same DeformSet convention used for export
    cmds.select(clear=True)
    cmds.select("DeformSet", add=True)
    General.sort_by_type(typ="joint")
    list_joint = cmds.ls(sl=True, long=True) or []

    if not list_joint:
        cmds.warning("Not found any joint in DeformSet")
        return dict_mesh

    # bake skeleton animation
    cmds.bakeResults(
        list_joint,
        simulation=True,
        t=(start_frame, end_frame),
        sampleBy=1,
        disableImplicitControl=True,
        preserveOutsideKeys=True,
    )

    # break every remaining incoming connection driving the joints, and make
    # sure every joint's visibility is on with no leftover keyframes on it
    for joint in list_joint:
        Connection.break_connection_transform(joint, rot=True, pos=True, scl=True, v=True)
        cmds.cutKey(joint, attribute="visibility", clear=True)
        cmds.setAttr(f"{joint}.visibility", True)

    # move each skeleton's root joint (no joint parent within the set) to world
    list_root_joint = []
    for joint in list_joint:
        parent = cmds.listRelatives(joint, parent=True, fullPath=True)
        if not parent or parent[0] not in list_joint:
            list_root_joint.append(joint)

    # recheck each root joint's hierarchy — strip any non-joint node left
    # attached under it (leftover locators, ikHandles, etc.) — do this before
    # moving to world, while these are still each root joint's original,
    # valid full path
    for root_joint in list_root_joint:
        _remove_non_joint_descendants(root_joint)

    _move_to_world(list_root_joint)

    # move each character's mesh (suffix "_Geo") straight to world — not its group
    list_mesh = []
    for key in dict_mesh.keys():
        for mesh in dict_mesh[key]:
            if str(mesh).split("|")[-1].endswith("_Geo") and mesh not in list_mesh:
                list_mesh.append(mesh)

    # clean up each mesh before moving: clear keyframes, break every
    # remaining incoming connection, then force visibility back on
    for mesh in list_mesh:
        cmds.cutKey(mesh, clear=True)
        Connection.break_connection_transform(mesh, rot=True, pos=True, scl=True, v=True)
        cmds.setAttr(f"{mesh}.visibility", True)

    _move_to_world(list_mesh)

    # dict_mesh's stored paths are now stale — rebuild from each mesh's new,
    # moved-to-world path (same short name, now parented directly under root)
    updated_dict_mesh = {}
    for key in dict_mesh.keys():
        updated_dict_mesh[key] = [
            "|" + str(mesh).split("|")[-1] for mesh in dict_mesh[key]
        ]

    return updated_dict_mesh


def export_skeleton_mesh(export_path):
    """Use to Export Skeleton mesh for Unity"""

    print("# Export Skeleton Mesh to Unity (Fbx Version) #")

    # validate face set
    dict_mesh = Pipeline.get_character_meshes(
        pick_character_enable=False, pick_character=False
    )

    for key in dict_mesh.keys():
        geos = dict_mesh[key]
        Validate.fix_material_names(selection=geos)
        Validate.validate_material_face_set(selection=geos)

    # =======================================
    # bake skeleton, detach from rig, move to root
    # =======================================
    dict_mesh = bake_and_detach_skeleton(dict_mesh)

    # =================
    # export skeleton fbx
    # =================
    print("# Exporting Skeleton Fbx #")

    # FBX export settings (shared for every character)
    mel.eval('FBXExportSmoothingGroups -v false')
    mel.eval('FBXExportHardEdges -v false')
    mel.eval('FBXExportTangents -v false')
    mel.eval('FBXExportSmoothMesh -v false')
    mel.eval('FBXExportInstances -v false')
    mel.eval('FBXExportBakeComplexAnimation -v true')
    mel.eval('FBXExportSkeletonDefinitions -v true')
    mel.eval('FBXExportSkins -v true')
    mel.eval('FBXExportShapes -v true')  # blendshapes
    mel.eval('FBXExportConstraints -v false')
    mel.eval('FBXExportCameras -v false')
    mel.eval('FBXExportLights -v false')
    mel.eval('FBXExportEmbeddedTextures -v false')
    mel.eval('FBXExportInputConnections -v false')
    mel.eval('FBXExportUpAxis y')

    # =======================
    # iterate for each character
    # =======================

    for key in dict_mesh.keys():
        list_valid_mesh = dict_mesh[key]

        # Disable smooth mesh preview on all meshes before export
        for mesh in list_valid_mesh:
            shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
            for shape in shapes:
                if cmds.attributeQuery('displaySmoothMesh', node=shape, exists=True):
                    cmds.setAttr(f'{shape}.displaySmoothMesh', 0)

        export_fbx_file_path = os.path.join(export_path, f"{key}.fbx").replace("\\", "/")

        print("fbx export path : ", export_fbx_file_path)

        cmds.select(clear=True)
        cmds.select("DeformSet", add=True)
        General.sort_by_type(typ="joint")
        cmds.select(list_valid_mesh, add=True)

        # Export selected
        mel.eval(f'FBXExport -f "{export_fbx_file_path}" -s')
        print(f"Exported: {export_fbx_file_path}")

    return True
