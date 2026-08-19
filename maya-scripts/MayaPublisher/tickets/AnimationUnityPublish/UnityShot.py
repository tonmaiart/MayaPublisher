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

import maya.cmds as cmds

import os
from UkoreMaya.core import Logic,Pipeline,AnimationExporter
from UkoreMaya.menu import General

from tmlib.core import Validate,File,Scene
import maya.mel as mel



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


    result = export_shot_to_unity(
        export_path=context["version_dir"],
        prefix_seperate=True,
        smooth_mesh=True,
        version=context["version"],
        validate_material=True,
        export_anim=True,
        export_camera=True,
        export_head_locator=True,
        pick_character_enable=False,
        pick_character=["Kafka"],
    )

    return False

def export_shot_to_unity(
    export_path=None,
    prefix_seperate=True,
    smooth_mesh=True,
    version="",
    validate_material=True,
    export_anim=True,
    export_camera=True,
    export_head_locator=True,
    pick_character_enable=False,
    pick_character=["Kafka"],
):

    print("# Export Shot to UE #")
    print("- Export Path : ", export_path)
    print("- Prefix Seperate : ", prefix_seperate)
    print("- Smooth Mesh : ", smooth_mesh)
    print("- Validate Material : ", validate_material)
    print("- Export Anim : ", export_anim)
    print("- Export Camera : ", export_camera)
    print("- Export Head Locator : ", export_head_locator)
    print("- Pick Character Enable : ", pick_character_enable)
    print("- Pick Character : ", pick_character)

    # verify path
    if os.path.isdir(export_path):
        os.makedirs(export_path, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(export_path), exist_ok=True)

    # get current file path
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

    # export anim abc , camera and head locator
    if export_anim:
        export_anim_abc(
            export_path,
            prefix_seperate=prefix_seperate,
            smooth_mesh=smooth_mesh,
            pick_character_enable=pick_character_enable,
            pick_character=pick_character,
            prefix_shot=current_file_dir_name,
        )

    if not os.path.isdir(export_path):
        export_path = os.path.dirname(export_path)

    if export_camera:
        export_anim_camera(
            export_fbx_path=os.path.join(
                export_path, "{}_camera.fbx".format(current_file_dir_name)
            )
        )

    return export_path


def export_anim_fbx(
    export_folder,
    pick_character=["Kafka"]):
    """
    Use to Export Animation to fbx

    pick_character : if False , will select all character , if list ["Kafka","Jacob"] ia mean pick kafka and jacob
    version : if have number input the name will be version
    
    Output example
    KBA030_Kafka_anim.fbx
    KBA030_Jacob_anim.fbx
    KBA030_Jacob_camera.fbx

    """

    print("# Exporting Animation Fbx #")
    # make sure the export path is directory
    # check is export path is directory
    if not os.path.isdir(export_folder):
        export_folder = os.path.dirname(export_folder)

    # ===================================================
    # get mesh dict , seperated by character prefix name
    # ===================================================

    dict_mesh = Pipeline.get_character_meshes(
        pick_character_enable=False, pick_character=pick_character
    )

    for key in dict_mesh.keys():
        print("Anim Alembic Character : ", key)

        for mesh in dict_mesh[key]:
            print("- ", Utility.cut(mesh))

    # =======================
    # iterate for each character
    # =======================

    for key in dict_mesh.keys():
        list_mesh_anim = dict_mesh[key]
        root_flags = " ".join(f"-root {obj}" for obj in list_mesh_anim)
        start_frame = cmds.playbackOptions(q=True, min=True)
        end_frame = cmds.playbackOptions(q=True, max=True)

        # generate export name
        prefix_shot = os.path.basename(cmds.file(q=True, sn=True)).split("_")[0]
        export_name = f"{prefix_shot}_{key}_anim.fbx"
        export_fbx_file_path = export_folder + "/" + export_name
        export_fbx_file_path = export_fbx_file_path.replace("\\", "/")

        print("fbx export path : ",export_fbx_file_path)
        # FBX export settings
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


        cmds.select(clear=True)
        cmds.select("*:DeformSet",add=True)
        General.sort_by_type(typ="joint")

        cmds.select(list_mesh_anim,add=True)
        # Export selected
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