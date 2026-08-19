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

import os

import maya.cmds as cmds
import maya.mel as mel

from UkoreMaya.core import Pipeline
from UkoreMaya.menu import General

from tmlib.core import Validate, Utility, Connection, Scene


def clean_up_scene():
    Scene.import_all_references()
    Scene.remove_all_namespaces()


def validate(context):
    clean_up_scene()
    return export_shot_to_unity(
        export_path=context["version_dir"],
        prefix_seperate=True,
        version=context["version"],
        validate_material=True,
        export_anim=True,
        export_camera=True,
        export_head_locator=True,
        pick_character_enable=False,
        pick_character=["Kafka"],
    )


def export_shot_to_unity(
    export_path,
    prefix_seperate=True,
    version="",
    validate_material=True,
    export_anim=True,
    export_camera=True,
    export_head_locator=True,
    pick_character_enable=False,
    pick_character=["Kafka"],
):

    print("# Export Shot to Unity #")
    print("- Export Path : ", export_path)
    print("- Prefix Seperate : ", prefix_seperate)
    print("- Validate Material : ", validate_material)
    print("- Export Anim : ", export_anim)
    print("- Export Camera : ", export_camera)
    print("- Export Head Locator : ", export_head_locator)
    print("- Pick Character Enable : ", pick_character_enable)
    print("- Pick Character : ", pick_character)

    # shot name prefix, taken from the current scene's parent folder name
    current_file_path = cmds.file(sn=True, q=1)
    current_file_dir_name = os.path.basename(os.path.dirname(current_file_path))

    # validate face set
    if validate_material:
        dict_mesh = Pipeline.get_character_meshes(
            pick_character_enable=pick_character_enable, pick_character=pick_character
        )

        for key in dict_mesh.keys():
            geos = dict_mesh[key]
            Validate.fix_material_names(selection=geos)
            Validate.validate_material_face_set(selection=geos)

    # export anim fbx and camera
    if export_anim:
        export_anim_fbx(
            export_path,
            prefix_seperate=prefix_seperate,
            pick_character_enable=pick_character_enable,
            pick_character=pick_character,
            version=version,
            prefix_shot=current_file_dir_name,
        )

    if export_camera:
        export_anim_camera(
            export_fbx_path=os.path.join(
                export_path, "{}_camera.fbx".format(current_file_dir_name)
            )
        )

    return True


def _move_to_world(list_node):
    """Reparent each given transform to world, skipping ones already there."""

    for node in list_node:
        if cmds.listRelatives(node, parent=True, fullPath=True):
            cmds.parent(node, world=True)


def _remove_non_joint_descendants(root_joint):
    """Recheck root_joint's whole hierarchy and delete any node under it
    that isn't a joint (e.g. a leftover locator), so only the joint chain
    remains for FBX export. Cameras are spared — a shot cam (e.g. "camshot")
    is sometimes parented directly under a joint (handheld/POV setups), and
    export_anim_camera() still needs to find it after this runs."""

    descendants = (
        cmds.listRelatives(root_joint, allDescendents=True, fullPath=True, type="transform")
        or []
    )

    for node in descendants:
        if not cmds.objExists(node) or cmds.objectType(node, isa="joint"):
            continue
        if cmds.listRelatives(node, shapes=True, type="camera"):
            continue
        cmds.delete(node)


def bake_and_detach_skeleton():
    """
    Bake the shot's skeleton animation onto its joints, break every
    remaining incoming connection driving them (constraints, rig controls),
    clear any visibility keyframes and force every joint's visibility on,
    strip any non-joint node left under a root joint, then move each
    skeleton root joint to world — same treatment
    RigUnityPublish/UnityRigSetup.py's bake_and_detach_skeleton() applies,
    just with no mesh involved (this ticket exports skeleton only).
    """

    start_frame = cmds.playbackOptions(q=True, min=True)
    end_frame = cmds.playbackOptions(q=True, max=True)

    cmds.select(clear=True)
    cmds.select("DeformSet", add=True)
    General.sort_by_type(typ="joint")
    list_joint = cmds.ls(sl=True, long=True) or []

    if not list_joint:
        cmds.warning("Not found any joint in DeformSet")
        return

    cmds.bakeResults(
        list_joint,
        simulation=True,
        t=(start_frame, end_frame),
        sampleBy=1,
        disableImplicitControl=True,
        preserveOutsideKeys=True,
    )

    # only break the visibility connection here — breaking rot/pos/scl too
    # would also disconnect the animCurves bakeResults just created above,
    # wiping out the baked animation
    for joint in list_joint:
        Connection.break_connection_transform(joint, rot=False, pos=False, scl=False, v=True)
        cmds.cutKey(joint, attribute="visibility", clear=True)
        cmds.setAttr(f"{joint}.visibility", True)

    # move each skeleton's root joint (no joint parent within the set) to world
    list_root_joint = []
    for joint in list_joint:
        parent = cmds.listRelatives(joint, parent=True, fullPath=True)
        if not parent or parent[0] not in list_joint:
            list_root_joint.append(joint)

    for root_joint in list_root_joint:
        _remove_non_joint_descendants(root_joint)

    _move_to_world(list_root_joint)


def export_anim_fbx(
    export_path,
    prefix_seperate=True,
    pick_character_enable=False,
    pick_character=["Kafka"],
    version="",
    prefix_shot="",
):
    """
    Export the shot's baked skeleton to per-character FBX files — no mesh.

    This will automatically detect and export each character separately
    - only detects mesh that have suffix "_Geo" (used for character
      grouping/naming only — no mesh is exported)
    - separates character based on prefix name like "Kafka_Eye_Geo" into file Kafka
    """

    print("# Exporting Anim Skeleton Fbx #")

    if prefix_seperate:
        dict_mesh = Pipeline.get_character_meshes(
            pick_character_enable=pick_character_enable, pick_character=pick_character
        )
    else:
        dict_mesh = {"anim": Pipeline.list_meshes_with_suffix_geo()}

    for key in dict_mesh.keys():
        print("Anim Fbx Character : ", key)

        for mesh in dict_mesh[key]:
            print("- ", Utility.cut(mesh))

    bake_and_detach_skeleton()

    # FBX export settings (shared for every character)
    mel.eval('FBXExportSmoothingGroups -v true')
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

    for key in dict_mesh.keys():
        if version:
            export_name = f"{key}_anim_{version:03}.fbx"
        else:
            export_name = f"{key}_anim.fbx"

        if prefix_shot:
            export_name = prefix_shot + "_" + export_name

        export_fbx_file_path = os.path.join(export_path, export_name).replace("\\", "/")

        cmds.select(clear=True)
        cmds.select("DeformSet", add=True)
        General.sort_by_type(typ="joint")

        print("fbx export path : ", export_fbx_file_path)
        mel.eval(f'FBXExport -f "{export_fbx_file_path}" -s')
        print(f"Exported: {export_fbx_file_path}")


def export_anim_camera(export_fbx_path):
    """Designed for camera named RenderCam , this will export camera fbx file"""

    cams = cmds.ls("camshot", "*:camshot", "*:*:camshot", transforms=True)

    if not cams:
        cmds.warning("ไม่พบกล้อง camshot")
    else:
        # Search Cam Shot
        cam_shot = cams[0]
        cam_shot_shape = cmds.listRelatives(cam_shot, shapes=True)[0]

        # Create Render Cam
        cam_render_name = "RenderCam"

        min_time = cmds.playbackOptions(query=True, minTime=True)
        max_time = cmds.playbackOptions(query=True, maxTime=True)

        cam_render, cam_render_shape = cmds.camera(n=cam_render_name)

        constraint_list = cmds.parentConstraint(cam_shot, cam_render)
        cmds.connectAttr(
            "{}.focalLength".format(cam_shot_shape),
            "{}.focalLength".format(cam_render_shape),
            f=True,
        )
        cmds.connectAttr(
            "{}.overscan".format(cam_shot_shape),
            "{}.overscan".format(cam_render_shape),
            f=True,
        )
        cmds.connectAttr(
            "{}.cameraAperture".format(cam_shot_shape),
            "{}.cameraAperture".format(cam_render_shape),
            f=True,
        )

        # Select and rename camera
        cmds.rename(cam_render, cam_render_name)
        cmds.select(cam_render_name, replace=True)

        print(f"Exporting Camera to Path : {export_fbx_path}")
        mel.eval("FBXExportBakeComplexAnimation -v true")
        cmds.file(
            export_fbx_path,
            force=True,  # -force (บังคับเขียนทับไฟล์เดิม)
            options="fbx",  # -options "fbx" (กำหนดตัวเลือกการ Export)
            type="FBX export",  # -typ "FBX export" (ประเภทไฟล์)
            preserveReferences=True,  # -pr (รักษา Reference files ไว้)
            exportSelected=True,  # -es (Export เฉพาะสิ่งที่ถูกเลือก)
        )

        # Delete temp camera
        cmds.delete(cam_render_name)
