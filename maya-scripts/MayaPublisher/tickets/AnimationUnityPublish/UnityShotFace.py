"""Ticket script for MayaPublisher: bake every character's facial texture
(any animated material color) to image sequences for Unity.

Companion to UnityShot (which exports the skeleton/camera headless): this one
needs a viewport — it playblasts — so its .json is batch_mode "gui". Output is
flat "{Material}.{Frame}.png" files in context["version_dir"]
(<animation_publish>/UnityShotFace/<shot>/vNNN/), which Unity's Shot Importer
picks up from its "Published Textures" folder and turns into one
"{Shot}_{Material}" Texture2DArray per material.

    context = {
        "version_dir": str,   # already-created destination folder for this publish
        "version": int,       # version number just created, e.g. 3 for v003
        "category": str,      # this ticket script's category, e.g. "Rig"
        "script_name": str,   # this ticket script's own file name (no .py)
        "tool_id": str,       # "maya_publisher"
    }
"""

import glob
import importlib.util
import os

import maya.cmds as cmds
import numpy

# Bake resolution (square). Matches UnityShot.bake_texture_to_image_sequence's default.
RESOLUTION = 512

# LED face cleanup, run on every baked LED frame (same size out). The capture of the eye
# rig leaves hairline seams inside the shapes (Jacob's FaceShield: a grey line along each
# eye's inner edge, from a piece behind the eye that doesn't match its outline) and
# stair-stepped edges at 512. CLEAN_SEAM_PX: dark gaps up to this wide inside a shape get
# filled, so a real gap that narrow (eyes nearly touching, a thin dark line drawn in the
# eye) is lost too. CLEAN_SCALE: edges are redrawn at this many times the size, then
# averaged back down, which gives them smooth anti-aliasing. Output is the shapes' brightest
# color on black: fine for a one-color LED face, not for a multi-color one.
CLEAN_LED_FACES = True
CLEAN_SEAM_PX = 5
CLEAN_SCALE = 4

# LED faces (MayaToolkit LedFaceCapture, e.g. Jacob's FaceShield since rig Hi_v004): the
# material's color comes from a ledFaceMaterial node, a VP2-only shading override fed by an
# offscreen capture of the eye rig. That capture only runs while the panel's Renderer is the
# "LED Face Capture" override, so the bake switches every model panel to it.
LED_NODE_TYPE = "ledFaceMaterial"
LED_OVERRIDE = "ledFaceCaptureOverride"


def _unity_shot():
    """UnityShot.py sits next to this file; tickets are loaded by path, not as
    a package, so load it the same way to reuse its bake function."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "UnityShot.py")
    spec = importlib.util.spec_from_file_location("_maya_publisher_unity_shot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_animated(plug):
    """True when anything upstream of plug changes over time: keyframes
    (any animCurve, driven keys included), an expression, or a file texture
    playing an image sequence (useFrameExtension)."""
    sources = cmds.listConnections(plug, source=True, destination=False) or []
    for source in sources:
        for node in cmds.listHistory(source) or []:
            node_type = cmds.nodeType(node)
            if node_type.startswith("animCurve") or node_type == "expression":
                return True
            if node_type == "file" and cmds.getAttr(f"{node}.useFrameExtension"):
                return True
    return False


def _led_source(plug):
    """The ledFaceMaterial node feeding plug (directly, or furthest-downstream
    in its history), or None. VP2 can only draw an LED face from that node's
    own outColor, not from a material input plug it drives."""
    direct = cmds.listConnections(plug, source=True, destination=False, type=LED_NODE_TYPE) or []
    if direct:
        return direct[0]
    history = cmds.listHistory(plug) or []
    led_nodes = [node for node in history if cmds.nodeType(node) == LED_NODE_TYPE]
    return led_nodes[0] if led_nodes else None


def find_facial_materials(unity_shot):
    """Every material assigned to a character mesh (*_Geo) whose color input
    is animated — the "facial texture" (eyes/mouth swaps, blinking, ...) —
    as (material, bake_node) pairs. bake_node is what gets baked: the
    material itself, or for an LED face the ledFaceMaterial node driving it
    (always counted as facial: it's a live capture of the animated eye rig).
    A static material is skipped: there's nothing to bake that the regular
    material setup in Unity doesn't already have."""
    shapes = cmds.ls("*_Geo", dag=True, shapes=True, noIntermediate=True, long=True) or []
    found = {}
    for shape in shapes:
        for shading_group in set(cmds.listConnections(shape, type="shadingEngine") or []):
            for material in cmds.ls(cmds.listConnections(f"{shading_group}.surfaceShader") or [], materials=True):
                if material in found:
                    continue
                try:
                    plug = unity_shot._find_material_color_plug(material)
                except RuntimeError:
                    continue
                led = _led_source(plug)
                if led:
                    found[material] = led
                elif _is_animated(plug):
                    found[material] = material
    return sorted(found.items())


def _window_extreme(values, size, axis, fn):
    """Max or min (fn) over a centered window of `size` along `axis`, edge-padded."""
    half = size // 2
    pad = [(0, 0)] * values.ndim
    pad[axis] = (half, half)
    padded = numpy.pad(values, pad, mode="edge")
    windows = numpy.lib.stride_tricks.sliding_window_view(padded, size, axis=axis)
    return fn(windows, axis=-1)


def _box_blur(values, radius, axis):
    """Mean over a centered window of 2*radius+1 along `axis` (cumulative sums: fast at any size)."""
    pad = [(0, 0)] * values.ndim
    pad[axis] = (radius + 1, radius)
    total = numpy.cumsum(numpy.pad(values, pad, mode="edge"), axis=axis)
    upper = numpy.take(total, range(2 * radius + 1, total.shape[axis]), axis=axis)
    lower = numpy.take(total, range(0, total.shape[axis] - 2 * radius - 1), axis=axis)
    return (upper - lower) / (2 * radius + 1)


def clean_led_mask(lum, seam=CLEAN_SEAM_PX, scale=CLEAN_SCALE):
    """lum: 2D float array 0-1 of one LED frame. Returns shape coverage 0-1, same size,
    with dark seams up to `seam` px inside shapes filled and edges smoothed."""
    size = seam // 2 * 2 + 1
    # closing (max then min): fills dark gaps narrower than `size`, keeps the outline
    closed = lum
    for fn in (numpy.max, numpy.min):
        closed = _window_extreme(_window_extreme(closed, size, 0, fn), size, 1, fn)
    # redraw at `scale`x: upsample, soften (three box passes ~ a Gaussian), threshold, average down
    big = numpy.repeat(numpy.repeat(closed, scale, axis=0), scale, axis=1)
    for _ in range(3):
        for axis in (0, 1):
            big = _box_blur(big, scale // 2 + 1, axis)
    big = (big > 0.5).astype(numpy.float32)
    height, width = lum.shape
    return big.reshape(height, scale, width, scale).mean(axis=(1, 3))


def _clean_led_frame(path):
    """Rewrite one baked LED frame (PNG) in place with clean_led_mask."""
    from PySide6.QtGui import QImage

    image = QImage(path).convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    rgba = numpy.frombuffer(image.constBits(), numpy.uint8, image.sizeInBytes())
    rgba = rgba.reshape(height, image.bytesPerLine())[:, : width * 4].reshape(height, width, 4)
    rgb = rgba[:, :, :3].astype(numpy.float32)
    lum = rgb.max(axis=2) / 255.0
    if lum.max() <= 0:
        return  # an all-black frame (eyes closed): nothing to clean
    color = rgb.reshape(-1, 3)[lum.argmax()]
    coverage = clean_led_mask(lum)
    out = numpy.empty((height, width, 4), numpy.uint8)
    out[:, :, :3] = numpy.round(coverage[:, :, None] * color).astype(numpy.uint8)
    out[:, :, 3] = 255
    data = out.tobytes()
    QImage(data, width, height, width * 4, QImage.Format_RGBA8888).save(path, "PNG")


def clean_led_frames(version_dir, prefix):
    frames = sorted(glob.glob(os.path.join(version_dir, f"{prefix}.*.png")))
    for path in frames:
        _clean_led_frame(path)
    print(f"- Cleaned {len(frames)} LED frame(s) for {prefix} (seam {CLEAN_SEAM_PX}px, scale {CLEAN_SCALE}x)")


def _check_led_plugin():
    """An LED face whose plug-in wasn't loaded when the scene opened is an
    "unknown" node: nothing would draw, and loading the plug-in now can't
    turn it back into a real one. Fail with the fix instead of baking black."""
    for node in cmds.ls(type="unknown") or []:
        if cmds.unknownNode(node, query=True, realClassName=True) == LED_NODE_TYPE:
            raise RuntimeError(
                f"'{node}' is an unknown {LED_NODE_TYPE} — the led_face_capture plug-in "
                "(MayaToolkit > LedFaceCapture) wasn't loaded when the scene opened. "
                "Enable it for this repo in UkoreHub and publish again."
            )


def _use_led_renderer():
    """Point every model panel at the LED Face Capture override; returns
    {panel: previous override name} for _restore_renderer()."""
    previous = {}
    for panel in cmds.getPanel(type="modelPanel") or []:
        previous[panel] = cmds.modelEditor(panel, query=True, rendererOverrideName=True) or ""
        cmds.modelEditor(panel, edit=True, rendererOverrideName=LED_OVERRIDE)
    return previous


def _use_black_background():
    """The LED capture clears to the viewport background color; an LED face is
    lit shapes on a black screen. Returns the previous state for
    _restore_background()."""
    previous = (cmds.displayRGBColor("background", query=True),
                cmds.displayPref(query=True, displayGradient=True))
    cmds.displayPref(displayGradient=False)
    cmds.displayRGBColor("background", 0.0, 0.0, 0.0)
    return previous


def _restore_background(previous):
    color, gradient = previous
    cmds.displayRGBColor("background", *color[:3])
    cmds.displayPref(displayGradient=gradient)


def _restore_renderer(previous):
    for panel, name in previous.items():
        if cmds.modelPanel(panel, exists=True):
            cmds.modelEditor(panel, edit=True, rendererOverrideName=name)


def validate(context):
    if cmds.about(batch=True):
        raise RuntimeError("UnityShotFace needs a viewport (it playblasts) — run it in GUI Maya.")

    unity_shot = _unity_shot()
    unity_shot.clean_up_scene()  # import references + drop namespaces, as UnityShot does
    _check_led_plugin()

    materials = find_facial_materials(unity_shot)
    print("# Bake Facial Textures #")
    print("- Export Path : ", context["version_dir"])
    print("- Materials : ", [f"{m} (LED: {n})" if n != m else m for m, n in materials])
    if not materials:
        raise RuntimeError(
            "No animated material found on any *_Geo mesh — nothing to bake. "
            "(A facial material counts when its color is keyed, driven by an "
            "expression, a file texture with Use Image Sequence on, or a "
            "ledFaceMaterial LED face.)"
        )

    previous_renderers = None
    previous_background = None
    try:
        for material, node in materials:
            led = node != material
            if led:
                # re-applied per bake: the bake restores the panel's own state afterward
                switched = _use_led_renderer()
                if previous_renderers is None:
                    previous_renderers = switched
                if previous_background is None:
                    previous_background = _use_black_background()
            # prefix keeps the frames named after the material ("MTL_Capture.1001.png"),
            # which is what Unity's "{Shot}_{Material}" array and the actor's driver expect.
            unity_shot.bake_texture_to_image_sequence(
                context["version_dir"],
                material_name=node,
                resolution=RESOLUTION,
                prefix=material,
                use_subfolder=False,
                # the LED capture of the eye rig inherits the panel's lighting: "none" = black
                display_lights="flat" if led else "none",
            )
            if led and CLEAN_LED_FACES:
                clean_led_frames(context["version_dir"], material)
    finally:
        _restore_renderer(previous_renderers or {})
        if previous_background is not None:
            _restore_background(previous_background)

    baked = sorted(glob.glob(os.path.join(context["version_dir"], "*.png")))
    print(f"Baked {len(baked)} frame(s) for {len(materials)} material(s).")
    if not baked:
        raise RuntimeError("Playblast produced no frames — check the viewport isn't minimized.")
    return True
