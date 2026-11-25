import os

import maya.cmds as cmds
import maya.mel as mel

from tmlib.core import Scene

# ngSkinTools 1 and 2 node types — deleting these only removes the layer
# data; the skinCluster weights stay as they are.
NGSKINTOOLS_NODE_TYPES = (
    "ngSkinLayerData",
    "ngSkinLayerDisplay",
    "ngst2SkinLayerData",
    "ngst2MeshDisplay",
)

# Nodes that must not reach the published rig (checked again right before
# export by verify_clean_scene).
REMOVED_NODE_NAMES = (
    "Delete_Grp",
    "FitSkeleton",
    "FaceFitSkeleton",
    "_dwpicker_data",
)

# AdvancedSkeleton's top rig group — renamed to the character name.
RIG_TOP_GROUP = "Group"


def validate(context):
    print("[RigToAnimPublish/Proxy] running {}".format(__file__))

    result = clean_up_scene(context=context)

    if result == False:
        return False

    return True

def clean_up_scene(context):
    result = Scene.clean_up_scene()

    if result == False:
        return False

    # version_dir = <publish_root>/<script_name>/<block_name>/vNNN, so its
    # parent folder is the scene's block name, i.e. the first "_" block of
    # the scene file name (e.g. "KBA030_rig_v012.ma" -> "KBA030").
    version_dir = context["version_dir"]
    character_name = os.path.basename(os.path.dirname(os.path.normpath(version_dir)))

    Scene.delete_all_delete_grp()
    delete_named_nodes(REMOVED_NODE_NAMES)
    delete_ngskintools_nodes()

    # Unity prep
    remove_unused_influences()
    enable_game_engine_scale_behaviour()

    rename_rig_group(character_name)
    delete_unused_nodes()

    verify_clean_scene()

    version = context["version"]
    ma_path = os.path.join(version_dir, "{}_v{:03d}.ma".format(character_name, version))
    # Export the cleaned scene in memory — Pipeline.export_maya_common()
    # published the saved file as it was before this clean-up.
    cmds.file(
        ma_path.replace("\\", "/"),
        force=True,
        exportAll=True,
        type="mayaAscii",
        preserveReferences=True,
    )

    print("Exported Maya Ascii file to: {}".format(ma_path))

    return True

def _find_named_nodes(names):
    patterns = []
    for name in names:
        patterns += [name, "*:" + name]
    return cmds.ls(*patterns, long=True) or []

def _delete_nodes(nodes):
    for node in nodes:
        if cmds.objExists(node):
            cmds.lockNode(node, lock=False)
            cmds.delete(node)

def delete_named_nodes(names):
    nodes = _find_named_nodes(names)
    described = ["{} ({})".format(n, cmds.nodeType(n)) for n in nodes]
    _delete_nodes(nodes)
    if nodes:
        print("Deleted: {}".format(described))
    else:
        print("Nothing to delete for: {}".format(list(names)))

def _list_ngskintools_nodes():
    nodes = []
    for node_type in NGSKINTOOLS_NODE_TYPES:
        try:
            nodes += cmds.ls(type=node_type) or []
        except RuntimeError:
            # Node type isn't registered (plugin not loaded) — those nodes,
            # if any, show up as "unknown" nodes below instead.
            pass

    for node in cmds.ls(type="unknown") or []:
        if cmds.unknownNode(node, q=True, realClassName=True) in NGSKINTOOLS_NODE_TYPES:
            nodes.append(node)
    return nodes

def delete_ngskintools_nodes():
    # Prefer ngSkinTools2's own "Delete Custom Nodes" (MayaNgSkin plugin's
    # ngSkinTools2.operations.removeLayerData) — it also exits the paint
    # tool so the mesh display node gets released.
    try:
        from ngSkinTools2.operations import removeLayerData
    except ImportError:
        removeLayerData = None
    if removeLayerData is not None and cmds.pluginInfo("ngSkinTools2", q=True, loaded=True):
        removeLayerData.remove_custom_nodes(interactive=False)
    else:
        cmds.setToolTo("Move")

    # Anything ngSkinTools2's API didn't cover (v1 nodes, plugin not
    # loaded -> "unknown" nodes).
    nodes = _list_ngskintools_nodes()
    _delete_nodes(nodes)
    if nodes:
        print("Deleted ngSkinTools nodes: {}".format(nodes))

    # Drop leftover "requires ngSkinTools" entries so the exported .ma
    # doesn't ask for the plugin on open.
    for plugin in cmds.unknownPlugin(q=True, list=True) or []:
        if plugin.lower().startswith("ngskintools"):
            try:
                cmds.unknownPlugin(plugin, remove=True)
            except RuntimeError:
                pass

def remove_unused_influences():
    """Same result as AdvancedSkeleton's "Remove Unused Influences" button
    (Maya's removeUnusedInfluences on every skinned mesh): drops every
    influence with zero weight from each skinCluster."""
    for skin_cluster in cmds.ls(type="skinCluster") or []:
        influences = cmds.skinCluster(skin_cluster, q=True, influence=True) or []
        weighted = set(cmds.skinCluster(skin_cluster, q=True, weightedInfluence=True) or [])
        unused = [inf for inf in influences if inf not in weighted]
        # Never strip a skinCluster down to nothing.
        if not unused or len(unused) == len(influences):
            continue
        for influence in unused:
            cmds.skinCluster(skin_cluster, e=True, removeInfluence=influence)
        print("Removed {} unused influence(s) from {}".format(len(unused), skin_cluster))

def _source_advanced_skeleton():
    """Sources AdvancedSkeleton.mel from ADVANCEDSKELETON_ROOT (same env var
    UkoreAdvancedSkeleton uses) so its global procs can be called."""
    if mel.eval('exists "asGameEngineScaling"'):
        return
    root = os.environ.get("ADVANCEDSKELETON_ROOT")
    if not root:
        raise RuntimeError("ไม่พบ ADVANCEDSKELETON_ROOT — ต้องใช้ AdvancedSkeleton เพื่อ Enable Scale Behaviour")
    mel_file = os.path.join(root, "AdvancedSkeleton.mel").replace("\\", "/")
    if not os.path.exists(mel_file):
        raise RuntimeError("ไม่พบ AdvancedSkeleton.mel: {}".format(mel_file))
    mel.eval('source "{}";'.format(mel_file))

def enable_game_engine_scale_behaviour():
    """AdvancedSkeleton > Game Engine > Scale Behaviour: Enable — turns off
    segmentScaleCompensate on the deform joints and rewires scale so it
    matches how Unity handles joint scale."""
    if not cmds.objExists("DeformationSystem"):
        cmds.warning("DeformationSystem not found — skipping Scale Behaviour Enable.")
        return
    _source_advanced_skeleton()
    mel.eval("asGameEngineScaling 1;")

def rename_rig_group(character_name):
    """Renames the rig's top "Group" to the character name taken from the
    scene file name, e.g. "|Group" -> "|KBA030"."""
    groups = cmds.ls(RIG_TOP_GROUP, long=True) or []
    if not groups and cmds.objExists("|" + character_name):
        # tmlib's Scene clean-up already renamed it.
        print('"{}" already renamed to "|{}"'.format(RIG_TOP_GROUP, character_name))
        return
    if not groups:
        top_nodes = ["{} ({})".format(n, cmds.nodeType(n)) for n in cmds.ls(assemblies=True) or []]
        raise RuntimeError('ไม่พบ group ชื่อ "{}" ใน scene — top nodes: {}'.format(RIG_TOP_GROUP, top_nodes))
    if len(groups) > 1:
        raise RuntimeError('พบ group ชื่อ "{}" มากกว่า 1 ตัว: {}'.format(RIG_TOP_GROUP, groups))

    existing = [n for n in cmds.ls(character_name, long=True) or [] if n != groups[0]]
    if existing:
        raise RuntimeError('มี node ชื่อ "{}" อยู่แล้ว ไม่สามารถ rename Group ได้: {}'.format(character_name, existing))

    cmds.lockNode(groups[0], lock=False)
    new_name = cmds.rename(groups[0], character_name)
    print("Renamed {} -> {}".format(groups[0], new_name))

def delete_unused_nodes():
    """Hypershade > Edit > Delete Unused Nodes."""
    mel.eval("MLdeleteUnused;")
    print("Deleted unused nodes.")

def verify_clean_scene():
    """Blocks the publish if anything that should have been removed is
    still in the scene right before export."""
    leftovers = _find_named_nodes(REMOVED_NODE_NAMES)
    leftovers += [n for n in _list_ngskintools_nodes() if cmds.objExists(n)]
    if leftovers:
        raise RuntimeError("ยังลบไม่หมดก่อน export: {}".format(leftovers))
