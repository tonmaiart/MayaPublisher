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

import glob
import json
import os

import maya.cmds as cmds
import maya.mel as mel

from UkoreMaya.core import Pipeline

from tmlib.core import Validate, Utility, Connection, Scene


# TEMP: hardcoded while the texture-bake pipeline is being built out — will
# become a per-ticket/per-character argument later
BAKE_TEXTURE_TARGET_MATERIAL = "M_eyeShader"


def clean_up_scene():
    Scene.import_all_references()
    Scene.remove_all_namespaces()


def validate(context):
    clean_up_scene()
    result = export_shot_to_unity(
        export_path=context["version_dir"],
        prefix_seperate=True,
        version=context["version"],
        validate_material=True,
        export_anim=True,
        export_camera=True,
        export_head_locator=True,
        disable_segment_scale_compensate=True,
    )

    # TEMP: wired in here only to exercise bake_texture_to_image_sequence()
    # during a real publish while it's being built — pull this call out once
    # the feature has its own ticket flow
    bake_texture_to_image_sequence(
        export_path=context["version_dir"],
        material_name=BAKE_TEXTURE_TARGET_MATERIAL,
    )

    return result


def export_shot_to_unity(
    export_path,
    prefix_seperate=True,
    version="",
    validate_material=True,
    export_anim=True,
    export_camera=True,
    export_head_locator=True,
    disable_segment_scale_compensate=False,
):

    print("# Export Shot to Unity #")
    print("- Export Path : ", export_path)
    print("- Prefix Seperate : ", prefix_seperate)
    print("- Validate Material : ", validate_material)
    print("- Export Anim : ", export_anim)
    print("- Export Camera : ", export_camera)
    print("- Export Head Locator : ", export_head_locator)

    # shot name prefix, taken from the current scene's parent folder name
    current_file_path = cmds.file(sn=True, q=1)
    current_file_dir_name = os.path.basename(os.path.dirname(current_file_path))

    # validate face set
    if validate_material:
        dict_mesh = Pipeline.get_character_meshes(pick_character_enable=False, pick_character=[])

        for key in dict_mesh.keys():
            geos = dict_mesh[key]
            Validate.fix_material_names(selection=geos)
            Validate.validate_material_face_set(selection=geos)

    # export anim fbx and camera
    if export_anim:
        export_anim_fbx(
            export_path,
            prefix_seperate=prefix_seperate,
            version=version,
            prefix_shot=current_file_dir_name,
            disable_segment_scale_compensate=disable_segment_scale_compensate,
        )

    if export_camera:
        export_anim_camera(
            export_fbx_path=os.path.join(
                export_path, "{}_camera.fbx".format(current_file_dir_name)
            )
        )

    return True


def _move_to_world(list_node):
    """Reparent each given transform to world, skipping ones already there.

    Returns each node's resulting full path, in order. Must be used instead
    of guessing "|" + short_name afterward — if a world-level sibling
    already has the same short name (another character's root, a leftover
    reference node, anything), Maya silently renames the newly-parented
    node on collision (e.g. "Root_M" -> "Root_M1"), and the guessed path
    would point at nothing."""

    result = []
    for node in list_node:
        if cmds.listRelatives(node, parent=True, fullPath=True):
            result.append(cmds.parent(node, world=True)[0])
        else:
            result.append(node)
    return result


def _undo_scale_compensation_dummy(node_path):
    """If parenting node_path to world (via _move_to_world) caused Maya to
    insert an automatic compensation transform above it, collapse that
    dummy back to scale 1, reparent node_path directly to world in its
    place, and delete the now-empty dummy.

    This happens when a joint's jointOrient combines with a scaled-away
    parent to produce shear — a joint has no shear attribute of its own to
    absorb it, so Maya creates an extra transform (named e.g. "Root_M1" or
    "group1") to hold the compensating scale/shear instead, leaving
    node_path as that dummy's child rather than sitting directly at world.

    Returns (final_path, min_scale) — min_scale is the smallest of the
    dummy's scaleX/Y/Z, or 1.0 when no dummy was created.
    """

    parent = cmds.listRelatives(node_path, parent=True, fullPath=True)
    if not parent:
        print(f"[scale-metadata] {node_path}: no dummy, parent is already world")
        return node_path, 1.0

    dummy = parent[0]
    scale_xyz = cmds.getAttr(f"{dummy}.scale")[0]
    min_scale = min(scale_xyz)
    print(f"[scale-metadata] {node_path}: dummy={dummy} scale={scale_xyz} min={min_scale}")

    cmds.setAttr(f"{dummy}.scale", 1, 1, 1, type="double3")
    final_path = cmds.parent(node_path, world=True)[0]
    cmds.delete(dummy)

    return final_path, min_scale


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


def _find_character_deformation_roots():
    """Find every character's own "DeformationSystem" group, keyed by the
    top-level group in its Full Hierarchy Path (e.g.
    "|Jacob|Group|DeformationSystem" -> character "Jacob"). Auto-detects
    every character found in the scene — no picked-character list needed.

    Returns {character_name: deformation_system_full_path}.
    """

    nodes = cmds.ls("DeformationSystem", "*:DeformationSystem", type="transform", long=True) or []

    return {node.split("|")[1]: node for node in nodes}


def _joints_under(deformation_system_root):
    return (
        cmds.listRelatives(deformation_system_root, allDescendents=True, fullPath=True, type="joint")
        or []
    )


def bake_and_detach_skeleton(disable_segment_scale_compensate=False):
    """
    Bake every character's skeleton animation onto its joints, break every
    remaining incoming connection driving them (constraints, rig controls),
    clear any visibility keyframes and force every joint's visibility on,
    and strip any non-joint node left under each root joint — same
    treatment RigUnityPublish/UnityRigSetup.py's bake_and_detach_skeleton()
    applies, just with no mesh involved (this ticket exports skeleton
    only).

    Each character's joints are auto-detected under its own
    "DeformationSystem" group (see _find_character_deformation_roots) — not
    a single shared "DeformSet".

    Deliberately does NOT move any root joint to world here — the caller
    must move each character's root(s) to world one at a time, export, then
    reparent back before moving the next character's root. If two
    characters happen to share a root joint's short name (a common rig
    convention, e.g. both named "Root_M") and both were at world at once,
    Maya would auto-rename the second on collision, breaking the
    "|" + short_name lookup used to find each character's moved root.

    disable_segment_scale_compensate: optional, off by default — when True,
    also disables segmentScaleCompensate on every joint (Unity ignores it
    and misreads scaled joint chains without this off).

    Returns {character_name: [root_joint, ...]}, each root joint's
    ORIGINAL full path (not yet moved to world).
    """

    character_roots = _find_character_deformation_roots()

    if not character_roots:
        cmds.warning("Not found any DeformationSystem group, skip baking skeleton")
        return {}

    character_joints = {
        character_name: _joints_under(root) for character_name, root in character_roots.items()
    }
    list_joint = [joint for joints in character_joints.values() for joint in joints]

    if not list_joint:
        cmds.warning("Not found any joint under DeformationSystem")
        return {}

    start_frame = cmds.playbackOptions(q=True, min=True)
    end_frame = cmds.playbackOptions(q=True, max=True)

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

    if disable_segment_scale_compensate:
        for joint in list_joint:
            if cmds.attributeQuery("segmentScaleCompensate", node=joint, exists=True):
                cmds.setAttr(f"{joint}.segmentScaleCompensate", False)

    # each character's root joint(s) — no joint parent within its own
    # DeformationSystem
    character_root_joints = {}
    for character_name, joints in character_joints.items():
        character_root_joints[character_name] = [
            joint
            for joint in joints
            if (cmds.listRelatives(joint, parent=True, fullPath=True) or [None])[0] not in joints
        ]

    for root_joints in character_root_joints.values():
        for root_joint in root_joints:
            _remove_non_joint_descendants(root_joint)

    return character_root_joints


def export_anim_fbx(
    export_path,
    prefix_seperate=True,
    version="",
    prefix_shot="",
    disable_segment_scale_compensate=False,
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
        dict_mesh = Pipeline.get_character_meshes(pick_character_enable=False, pick_character=[])
    else:
        dict_mesh = {"anim": Pipeline.list_meshes_with_suffix_geo()}

    for key in dict_mesh.keys():
        print("Anim Fbx Character : ", key)

        for mesh in dict_mesh[key]:
            print("- ", Utility.cut(mesh))

    character_root_joints = bake_and_detach_skeleton(
        disable_segment_scale_compensate=disable_segment_scale_compensate
    )

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

    # per-character world-scale metadata, keyed the same as dict_mesh —
    # 1.0 unless _undo_scale_compensation_dummy finds a compensation dummy
    shot_metadata = {key: {"scale_min": 1.0} for key in dict_mesh.keys()}

    for key in dict_mesh.keys():
        if version:
            export_name = f"{key}_anim_{version:03}.fbx"
        else:
            export_name = f"{key}_anim.fbx"

        if prefix_shot:
            export_name = prefix_shot + "_" + export_name

        export_fbx_file_path = os.path.join(export_path, export_name).replace("\\", "/")

        root_joints = character_root_joints.get(key)
        if not root_joints:
            cmds.warning(f"Not found DeformationSystem skeleton for {key}, skip exporting")
            continue

        # move this character's root(s) to world one at a time, export,
        # then reparent back before the next character — if two characters
        # share a root joint short name (e.g. both "Root_M") and were both
        # at world at once, Maya would auto-rename the second on collision
        original_parents = [
            (cmds.listRelatives(root_joint, parent=True, fullPath=True) or [None])[0]
            for root_joint in root_joints
        ]

        moved_root_joints = _move_to_world(root_joints)

        new_root_paths = []
        root_scales = []
        for moved_root_joint in moved_root_joints:
            final_path, min_scale = _undo_scale_compensation_dummy(moved_root_joint)
            new_root_paths.append(final_path)
            root_scales.append(min_scale)
        shot_metadata[key] = {"scale_min": min(root_scales)}

        joints = []
        for new_root_path in new_root_paths:
            joints.append(new_root_path)
            joints.extend(
                cmds.listRelatives(new_root_path, allDescendents=True, fullPath=True, type="joint")
                or []
            )

        cmds.select(clear=True)
        cmds.select(joints, add=True)

        print("fbx export path : ", export_fbx_file_path)
        mel.eval(f'FBXExport -f "{export_fbx_file_path}" -s')
        print(f"Exported: {export_fbx_file_path}")

        for new_root_path, original_parent in zip(new_root_paths, original_parents):
            if original_parent:
                cmds.parent(new_root_path, original_parent)

    metadata_name = f"{prefix_shot}_metadata.json" if prefix_shot else "metadata.json"
    metadata_path = os.path.join(export_path, metadata_name).replace("\\", "/")
    with open(metadata_path, "w") as f:
        json.dump(shot_metadata, f, indent=4)
    print("metadata export path : ", metadata_path)


def _unlock_all_attributes(node):
    """Unlock every locked attribute on node (transform or shape channels
    alike) so a later bakeResults/rename/parent on it can't silently skip
    a channel or error out on a locked one."""

    for attr in cmds.listAttr(node, locked=True) or []:
        cmds.setAttr(f"{node}.{attr}", lock=False)


def export_anim_camera(export_fbx_path):
    """Designed for camera named RenderCam, this will export camera fbx file.

    Bakes camshot's own transform + shape keyframes (so anything driven by
    constraints/expressions/connections becomes plain keys), deletes every
    constraint left on it, moves it to world, then exports it directly —
    replaces the old approach of building a separate RenderCam duplicate
    constrained/connected to camshot."""

    cams = cmds.ls("camshot", "*:camshot", "*:*:camshot", transforms=True)

    if not cams:
        cmds.warning("ไม่พบกล้อง camshot")
        return

    cam_shot = cams[0]
    cam_shot_shape = cmds.listRelatives(cam_shot, shapes=True, fullPath=True)[0]

    # unlock before touching anything else below (bake/parent/rename all
    # need write access to these channels)
    _unlock_all_attributes(cam_shot)
    _unlock_all_attributes(cam_shot_shape)

    start_frame = cmds.playbackOptions(q=True, min=True)
    end_frame = cmds.playbackOptions(q=True, max=True)

    cmds.bakeResults(
        [cam_shot, cam_shot_shape],
        simulation=True,
        t=(start_frame, end_frame),
        sampleBy=1,
        disableImplicitControl=True,
        preserveOutsideKeys=True,
    )

    constraints = cmds.listRelatives(cam_shot, children=True, type="constraint", fullPath=True) or []
    if constraints:
        cmds.delete(constraints)

    child_transforms = (
        cmds.listRelatives(cam_shot, children=True, type="transform", fullPath=True) or []
    )
    if child_transforms:
        cmds.delete(child_transforms)

    _move_to_world([cam_shot])

    cam_render_name = "RenderCam"
    cam_shot = cmds.rename(cam_shot, cam_render_name)
    cmds.select(cam_shot, replace=True)

    print(f"Exporting Camera to Path : {export_fbx_path}")
    mel.eval("FBXExportBakeComplexAnimation -v true")
    # export_anim_fbx() above turns this off (FBXExport* options are global,
    # not scoped per export) — must turn it back on or RenderCam's shape
    # data is silently dropped from the fbx even though it's selected
    mel.eval("FBXExportCameras -v true")
    cmds.file(
        export_fbx_path,
        force=True,  # -force (บังคับเขียนทับไฟล์เดิม)
        options="fbx",  # -options "fbx" (กำหนดตัวเลือกการ Export)
        type="FBX export",  # -typ "FBX export" (ประเภทไฟล์)
        preserveReferences=True,  # -pr (รักษา Reference files ไว้)
        exportSelected=True,  # -es (Export เฉพาะสิ่งที่ถูกเลือก)
    )


def _find_material_color_plug(material_name):
    """Return "material_name.<attr>" for whichever color-like INPUT
    material_name has (color for Lambert/Blinn/etc., baseColor for
    aiStandardSurface-style shaders), so it can be fed into a flat shader's
    outColor. Works whether that attribute is a static value or driven by
    an animated network (ramp, keyframes, expressions, ...) — connectAttr
    just makes the flat shader re-read it every frame.

    Deliberately prefers these plain color INPUTS over material_name's own
    "outColor": on a lit shader (Lambert/Blinn/aiStandardSurface/...),
    outColor is the fully-shaded result, computed from a lightData array
    the renderer feeds it during an actual render pass — pulled outside of
    that (e.g. via connectAttr into another node, with viewport lights
    switched off for the flat/no-overlay capture) it has no lights to
    shade with and evaluates to black. "color"/"baseColor" is the raw input
    value instead, unaffected by lighting either way — which is also
    exactly the "flat" behavior asked for. outColor is only used as a last
    resort, for a material that's already a flat/output-only shader (e.g.
    surfaceShader itself) with no color/baseColor input to read instead.
    """

    for attr in ("color", "baseColor", "outColor"):
        if cmds.attributeQuery(attr, node=material_name, exists=True):
            return f"{material_name}.{attr}"

    raise RuntimeError(
        f"[bake_texture] material '{material_name}' has no color/baseColor/outColor "
        "attribute to bake from"
    )


def bake_texture_to_image_sequence(
    export_path,
    material_name=BAKE_TEXTURE_TARGET_MATERIAL,
    resolution=512,
    prefix="",
    debug_skip_playblast=False,
    debug_keep_temp_nodes=False,
):
    """Bake material_name's color output to a flipbook image sequence, one
    frame per frame of the current timeline.

    Builds a temp 1x1 plane named "RenderPlane" (its default UVs already
    cover 0-1, so it fills the frame exactly) and a temp orthographic
    camera framed square-on to it, feeds material_name's color plug into a
    temp surfaceShader assigned to the plane, then playblasts the timeline
    range to export_path.

    surfaceShader is what makes this "flat"/unlit — it has no diffuse/
    specular response to scene lights at all, so whatever lighting sits in
    the shot can't leak into the baked colors; only material_name's own
    (possibly animated) color network reaches the render. The resolution
    gate is switched on on the temp camera so its frame in the viewport
    visibly matches the render's edges, with no extra margin around the
    plane, while this is being dialed in.

    debug_skip_playblast: when True, stop right after the camera/plane are
    set up and the panel is switched to look through the temp camera —
    skips the playblast entirely (implies debug_keep_temp_nodes), so
    RenderPlane / tempBakeTextureCam stay in the scene for eyeballing the
    framing/alignment interactively. Returns None in this mode.

    debug_keep_temp_nodes: when True, still runs the playblast as normal,
    but skips deleting RenderPlane / tempBakeTextureCam / the flat shader
    afterward — for inspecting exactly what was in front of the camera
    when a playblast comes out wrong (e.g. blank/transparent).

    Every node this creates (plane, camera, flat shader + its shading
    group) is temporary and normally always deleted again before returning
    (whether or not the bake succeeds) — except when debug_skip_playblast
    or debug_keep_temp_nodes left them in place, see above. The panel this
    borrows to render through is also restored to whatever camera and
    grid/hud/manipulator/object-type display state it had before this ran
    (same exception for the two debug flags — the whole point there is
    leaving the panel looking through tempBakeTextureCam to inspect it).

    Returns the playblast's exported file path pattern, or None if
    material_name doesn't exist in the scene (a warning is printed instead
    of raising, since this is a best-effort extra bake, not the shot's
    actual publish output) or debug_skip_playblast was used.
    """

    if not cmds.objExists(material_name):
        cmds.warning(
            f"[bake_texture] material '{material_name}' not found in scene, "
            "skip baking texture"
        )
        return None

    print("# Bake Texture to Image Sequence #")
    print("- Material : ", material_name)
    print("- Export Path : ", export_path)
    print("- Resolution : ", resolution)

    temp_plane = None
    temp_camera = None
    flat_shader = None
    flat_sg = None
    # not temp nodes, the viewer's own panel/preference state — restored
    # below (unless a debug_* flag is deliberately leaving it for
    # inspection, see the finally block)
    cm_was_enabled = cmds.colorManagementPrefs(query=True, cmEnabled=True)
    original_panel = None
    original_camera = None
    original_state_string = None

    try:
        # color management applies the view/display transform (sRGB, ACES,
        # whatever the scene's using) to the viewport before playblast
        # captures it, so a raw (1,1,1) white plug reads back slightly grey
        # in the exported frame — this is a flat data bake, not a beauty
        # pass, so turn it off for the capture and put it back after
        if cm_was_enabled:
            cmds.colorManagementPrefs(edit=True, cmEnabled=False)

        color_plug = _find_material_color_plug(material_name)

        temp_plane, _ = cmds.polyPlane(
            name="RenderPlane",
            width=1,
            height=1,
            subdivisionsX=1,
            subdivisionsY=1,
            axis=(0, 0, 1),
        )
        cmds.setAttr(f"{temp_plane}.translate", 0, 0, 0, type="double3")
        cmds.setAttr(f"{temp_plane}.rotate", 0, 0, 0, type="double3")

        flat_shader = cmds.shadingNode(
            "surfaceShader", asShader=True, name="tempBakeFlatShader"
        )
        flat_sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{flat_shader}SG"
        )
        cmds.connectAttr(f"{flat_shader}.outColor", f"{flat_sg}.surfaceShader", force=True)
        cmds.connectAttr(color_plug, f"{flat_shader}.outColor", force=True)
        cmds.sets(temp_plane, edit=True, forceElement=flat_sg)

        # Created via mel.eval(camera + objectMoveCommand + cameraMakeNode)
        # instead of cmds.camera() — this is the literal sequence Maya's own
        # Create > Cameras > Camera menu item runs. cameraMakeNode is what
        # actually finishes registering the camera as a normal interactive
        # one; a plain cmds.camera() node was missing whatever that step
        # does, and couldn't be selected from the Panels menu's camera list
        # (dragging it into a panel directly still worked, since that
        # doesn't go through the same registration path).
        mel.eval(
            "camera -centerOfInterest 5 -focalLength 35 -lensSqueezeRatio 1 "
            "-cameraScale 1 -horizontalFilmAperture 1.0 -horizontalFilmOffset 0 "
            "-verticalFilmAperture 1.0 -verticalFilmOffset 0 -filmFit Fill "
            "-overscan 1 -motionBlur 0 -shutterAngle 144 -nearClipPlane 0.1 "
            "-farClipPlane 10000 -orthographic 1 -orthographicWidth 1 "
            "-panZoomEnabled 0 -horizontalPan 0 -verticalPan 0 -zoom 1; "
            "objectMoveCommand; "
            'cameraMakeNode 1 "";'
        )
        # cameraMakeNode leaves the new camera transform selected
        new_cameras = [
            node
            for node in (cmds.ls(selection=True, long=True) or [])
            if cmds.listRelatives(node, shapes=True, type="camera")
        ]
        if not new_cameras:
            raise RuntimeError(
                "[bake_texture] camera creation via cameraMakeNode left nothing selected"
            )

        temp_camera = cmds.rename(new_cameras[0], "tempBakeTextureCam")
        temp_camera_shape = cmds.listRelatives(temp_camera, shapes=True, type="camera")[0]

        cmds.setAttr(f"{temp_camera}.translate", 0, 0, 5, type="double3")
        cmds.setAttr(f"{temp_camera}.rotate", 0, 0, 0, type="double3")
        # resolution gate on, so the camera's frame in the viewport can be
        # checked by eye against the plane's edges while this is dialed in
        cmds.setAttr(f"{temp_camera_shape}.displayResolution", True)
        cmds.setAttr(f"{temp_camera_shape}.displayGateMask", True)

        # own subfolder per material, so multiple baked materials don't dump
        # their sequences into the same folder and collide/mix frames
        sequence_dir = os.path.join(export_path, material_name)
        os.makedirs(sequence_dir, exist_ok=True)

        start_frame = cmds.playbackOptions(q=True, min=True)
        end_frame = cmds.playbackOptions(q=True, max=True)

        image_name = prefix or material_name
        output_path = os.path.join(sequence_dir, image_name).replace("\\", "/")

        panel = cmds.getPanel(withFocus=True)
        if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
            panels = cmds.getPanel(type="modelPanel")
            panel = panels[0]

        # snapshot this panel's camera + every display toggle before
        # touching either, so both can be put back afterward — stateString
        # returns a modelEditor -e command capturing the panel's full
        # current display state (grid/hud/manipulators/every object-type
        # toggle/etc.) in one shot, restorable with a single mel.eval
        original_panel = panel
        original_camera = cmds.modelPanel(panel, query=True, camera=True)
        original_state_string = cmds.modelEditor(panel, query=True, stateString=True)

        # lookThru (not just modelEditor's camera flag) is what actually
        # aligns the panel to the new camera and flips its display to
        # orthographic — modelEditor(camera=...) alone left the panel
        # showing the old perspective camera in testing
        cmds.lookThru(panel, temp_camera)

        # strip every overlay down to the shaded mesh itself: grid, HUD,
        # manipulators, and every other object type (lights, joints,
        # locators, other cameras, curves, image planes, ...) off, with
        # only polygon meshes left on so RenderPlane is all that shows.
        # -allObjects has to be its own call, before -polymeshes: passed in
        # the same modelEditor call, -allObjects re-applies its boolean to
        # every per-type toggle (including polymeshes) regardless of the
        # order the flags are written in Python, silently turning the
        # plane back off too — which is what was producing a fully blank/
        # transparent playblast (nothing at all was being drawn, not even
        # RenderPlane).
        cmds.modelEditor(
            panel,
            edit=True,
            displayLights="none",
            displayAppearance="smoothShaded",
            grid=False,
            hud=False,
            manipulators=False,
            selectionHiliteDisplay=False,
            allObjects=False,
        )
        cmds.modelEditor(panel, edit=True, polymeshes=True)

        if debug_skip_playblast:
            print(
                f"[bake_texture] debug_skip_playblast=True — stopping before playblast. "
                f"{temp_plane} / {temp_camera} left in the scene, panel '{panel}' is "
                f"looking through {temp_camera}: check alignment/framing by eye."
            )
            return None

        result = cmds.playblast(
            format="image",
            filename=output_path,
            startTime=start_frame,
            endTime=end_frame,
            viewer=False,
            showOrnaments=False,
            compression="png",
            widthHeight=(resolution, resolution),
            percent=100,
            clearCache=True,
            offScreen=True,
            forceOverwrite=True,
            # playblast has no "camera" flag — it always renders through
            # whichever panel it's pointed at, so editorPanelName pins it to
            # the exact panel lookThru()/modelEditor() were just set up on
            editorPanelName=panel,
        )

        exported_files = sorted(glob.glob(result.replace("####", "*")))
        print(f"Bake texture sequence exported: {len(exported_files)} frame(s) to {sequence_dir}")

        return result

    finally:
        if cm_was_enabled:
            cmds.colorManagementPrefs(edit=True, cmEnabled=True)

        keep_for_debug = debug_skip_playblast or debug_keep_temp_nodes

        # put the panel back the way it looked before — its original camera
        # and every grid/hud/manipulator/object-type display toggle — so
        # the artist's own viewport isn't left stuck on the temp camera
        # with everything but polymeshes switched off. Skipped in debug
        # mode on purpose: that's the point of leaving it looking through
        # tempBakeTextureCam, for eyeballing the setup.
        if not keep_for_debug and original_panel and cmds.getPanel(typeOf=original_panel) == "modelPanel":
            if original_camera and cmds.objExists(original_camera):
                cmds.lookThru(original_panel, original_camera)
            if original_state_string:
                mel.eval(original_state_string)

        if not keep_for_debug:
            for node in (temp_plane, temp_camera, flat_sg, flat_shader):
                if node and cmds.objExists(node):
                    cmds.delete(node)
