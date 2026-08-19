import maya.cmds as cmds

import os
from UkoreMaya.core import Logic,Pipeline,AnimationExporter
from UkoreMaya.menu import General

from tmlib.core import Validate,File,Scene
import maya.mel as mel



def validate(context):
    result = Scene.validate_scene(context=context)

    if result == False:
        return False

    result = export_skeleton_mesh(context=context)

    if result == False:
        return False
    
    return True

def clean_up_scene(context):
    result = Scene.clean_up_scene()

    if result == False:
        return False
    
    folder_name = context["ticket"]["folder_name"]
    version = context["version"]
    ma_path = os.path.join(context["version_dir"], "{}_v{:03d}.ma".format(folder_name, version))
    Pipeline.export_maya_common(export_file_path=ma_path)

    print("Exported Maya Ascii file to: {}".format(ma_path)
          )
    
    return True


def export_skeleton_mesh(context):
    """ Use to Export Skeleton mesh for unreal engine"""

    print("# Export Skeleton Mesh to UE (Fbx Version) #")

    # get current file path
    current_file_full_path = cmds.file(sn=True, q=1)
    current_file_dir_basename = os.path.basename(os.path.dirname(current_file_full_path))
    shot_name = os.path.basename(current_file_full_path).split("_")[0]
    publish_folder =  Logic.convert_to_publish_path(current_file_full_path)["publish_root_dir"]

    # =======================
    # Generate Export Folder
    # =======================

    export_folder = Logic.generate_publish_version_directory_path(
    current_share_path=current_file_full_path,
    subfolder="SkeletonMesh")
    
    Logic.make_sure_folder_exist(export_folder)

    # display infomation
    # validate face set
    dict_mesh = Pipeline.get_character_meshes(
        pick_character_enable=False, pick_character=False
    )

    for key in dict_mesh.keys():
        geos = dict_mesh[key]
        Validate.fix_material_names(selection=geos)
        Validate.validate_material_face_set(selection=geos)

    # =================
    # export anim fbx
    # =================
    print("# Exporting Animation Fbx #")
    # make sure the export path is directory
    # check is export path is directory
    if not os.path.isdir(export_folder):
        export_folder = os.path.dirname(export_folder)

    # =======================
    # iterate for each character
    # =======================

    for key in dict_mesh.keys():
        list_valid_mesh = dict_mesh[key]
        root_flags = " ".join(f"-root {obj}" for obj in list_valid_mesh)
        start_frame = cmds.playbackOptions(q=True, min=True)
        end_frame = cmds.playbackOptions(q=True, max=True)

        # Disable smooth mesh preview on all meshes before export
        for mesh in list_valid_mesh:
            shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
            for shape in shapes:
                if cmds.attributeQuery('displaySmoothMesh', node=shape, exists=True):
                    cmds.setAttr(f'{shape}.displaySmoothMesh', 0)

        # generate export name
        prefix_shot = os.path.basename(cmds.file(q=True, sn=True)).split("_")[0]
        export_name = f"{key}.fbx"
        export_fbx_file_path = export_folder + "/" + export_name
        export_fbx_file_path = export_fbx_file_path.replace("\\", "/")

        print("fbx export path : ",export_fbx_file_path)
        # FBX export settings
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

        cmds.select(clear=True)
        cmds.select("DeformSet",add=True)
        General.sort_by_type(typ="joint")
        cmds.select(list_valid_mesh,add=True)
        
        # Export selected
        mel.eval(f'FBXExport -f "{export_fbx_file_path}" -s')
        print(f"Exported: {export_fbx_file_path}")

    
    return True