import maya.cmds as cmds

import os
from UkoreMaya.core import Logic,Pipeline,AnimationExporter
from UkoreMaya.menu import General

from tmlib.core import Validate,File,Scene
import maya.mel as mel



def validate(context):
    result = clean_up_scene(context=context)

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
