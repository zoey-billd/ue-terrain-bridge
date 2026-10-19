# -*- coding: utf-8 -*-
"""
UE-side terrain import + PCG regenerate stage for the Gaea->Houdini->UE bridge.

Sent to UE via the TCP bridge (port 9877).  Reads the processed heightmap
and weight PNGs produced by Houdini, applies them to the existing Landscape,
then regenerates the configured PCG volumes.

Expected inputs (paths configurable via the JSON injected by the orchestrator):
  height_png   : 16-bit heightmap PNG
  weight_grass : 8-bit grass weight PNG  -> Layer_1
  weight_rock  : 8-bit rock weight PNG   -> Layer_2
  weight_dirt  : 8-bit dirt weight PNG   -> Layer_3
"""
import unreal
import json
import os
import time

# ── configuration injected by the orchestrator ──────────────────────────────
CFG = json.loads(r'''__CFG_JSON__''')

HEIGHT_PNG = CFG["height_png"]
WGRASS = CFG["weight_grass"]
WROCK = CFG["weight_rock"]
WDIRT = CFG["weight_dirt"]
LAYER_MAP = CFG["layer_mapping"]          # {"Layer_1":"grass", ...}
LS_LABEL = CFG["landscape_label"]
PCG_LABELS = CFG["regen_pcg_labels"]
LEVEL_PATH = CFG.get("level_path", "/Game/ThirdPerson/Lvl_ThirdPerson1")
SAVE_AFTER = CFG.get("save_after", True)

result = {"steps": [], "errors": []}

def log(msg):
    result["steps"].append(msg)
    print(msg)

def fail(msg):
    result["errors"].append(msg)
    print("ERROR:", msg)

# ── 0. load the correct level (editor may be on a demo map) ────────────────
try:
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world.get_name() != LEVEL_PATH.rsplit("/", 1)[-1]:
        log("Loading level: %s" % LEVEL_PATH)
        unreal.EditorLevelLibrary.load_level(LEVEL_PATH)
        log("Level loaded")
except Exception as e:
    fail("load_level: %s" % e)

# ── 1. find the landscape ──────────────────────────────────────────────────
landscape = None
for a in unreal.EditorLevelLibrary.get_all_level_actors():
    if isinstance(a, unreal.Landscape):
        landscape = a
        break

if landscape is None:
    fail("No landscape actor found in level %s" % LEVEL_PATH)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0)

log("Landscape found: %s" % landscape.get_name())
try:
    layer_names = [str(n) for n in landscape.get_target_layer_names()]
    log("Landscape layers: %s" % layer_names)
except Exception as e:
    fail("get_target_layer_names: %s" % e)

# ── ensure texture import folder exists ────────────────────────────────────
TB_FOLDER = "/Game/TerrainBridge"
if not unreal.EditorAssetLibrary.does_directory_exist(TB_FOLDER):
    unreal.EditorAssetLibrary.make_directory(TB_FOLDER)
    log("Created folder: %s" % TB_FOLDER)

# ── helper: render a Texture2D into a RenderTarget2D via Canvas ────────────
def tex_to_rt(world, tex, w, h, fmt=None):
    """Draw a Texture2D into a new TextureRenderTarget2D and return it."""
    if fmt is None:
        fmt = unreal.TextureRenderTargetFormat.RTF_RGBA8
    rt = unreal.RenderingLibrary.create_render_target2d(
        world, width=w, height=h, format=fmt)
    canvas, size, ctx = unreal.RenderingLibrary.begin_draw_canvas_to_render_target(world, rt)
    canvas.draw_texture(
        tex,
        unreal.Vector2D(0, 0),
        unreal.Vector2D(w, h),
        unreal.Vector2D(0, 0),
        unreal.Vector2D(1, 1),
    )
    unreal.RenderingLibrary.end_draw_canvas_to_render_target(world, ctx)
    return rt

# ── 2. import heightmap ────────────────────────────────────────────────────
from PIL import Image
import numpy as np

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()

if not os.path.exists(HEIGHT_PNG):
    fail("Heightmap not found: %s" % HEIGHT_PNG)
else:
    log("Importing heightmap: %s" % HEIGHT_PNG)
    try:
        # 0) disable edit layers — landscape_import_heightmap_from_render_target
        #    refuses to run when bCanHaveLayersContent is True.  Set it
        #    unconditionally: gating on a property read can skip the disable
        #    while layers content still exists, silently failing the import.
        try:
            landscape.set_editor_property("bCanHaveLayersContent", False)
            log("Ensured landscape edit layers disabled")
        except Exception as e:
            fail("disable edit layers: %s" % e)
        # 1) import height PNG as Texture2D
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        task = unreal.AssetImportTask()
        task.filename = HEIGHT_PNG
        task.destination_path = TB_FOLDER
        task.destination_name = "T_TBHeight"
        task.automated = True
        task.replace_existing = True
        task.save = True
        asset_tools.import_asset_tasks([task])
        tex = unreal.EditorAssetLibrary.load_asset("%s/T_TBHeight" % TB_FOLDER)
        if not tex:
            fail("Failed to load height texture")
        else:
            # 2) read size from source PNG
            with Image.open(HEIGHT_PNG) as im:
                tw, th = im.size
            # 3) render texture into a RenderTarget (RGBA16F preserves 16-bit height)
            rt = tex_to_rt(world, tex, tw, th,
                           fmt=unreal.TextureRenderTargetFormat.RTF_RGBA16F)
            # 4) import from render target into landscape
            landscape.landscape_import_heightmap_from_render_target(rt)
            log("Heightmap imported via render target (%dx%d)" % (tw, th))
    except Exception as e:
        fail("heightmap import: %s" % e)

# ── 3. apply weight maps to landscape layers ───────────────────────────────
weight_files = {}
for layer, key in LAYER_MAP.items():
    if key == "grass":
        weight_files[layer] = WGRASS
    elif key == "rock":
        weight_files[layer] = WROCK
    elif key == "dirt":
        weight_files[layer] = WDIRT

# Import each weight PNG as a Texture2D, then paint onto the landscape layer
asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
imported_textures = {}

for layer_name, png_path in weight_files.items():
    if not os.path.exists(png_path):
        fail("Weight PNG missing for layer %s: %s" % (layer_name, png_path))
        continue
    if layer_name not in layer_names:
        fail("Layer %s not on landscape (have %s)" % (layer_name, layer_names))
        continue

    tex_name = "T_TBWeight_%s" % layer_name
    tex_path = "/Game/TerrainBridge/%s" % tex_name

    task = unreal.AssetImportTask()
    task.filename = png_path
    task.destination_path = "/Game/TerrainBridge"
    task.destination_name = tex_name
    task.automated = True
    task.replace_existing = True
    task.save = True
    try:
        asset_tools.import_asset_tasks([task])
        log("Imported weight texture: %s -> %s" % (os.path.basename(png_path), tex_path))
        imported_textures[layer_name] = tex_path
    except Exception as e:
        fail("Weight import failed for %s: %s" % (layer_name, e))

# Paint each weight layer from its texture via render target
for layer_name, tex_path in imported_textures.items():
    try:
        tex = unreal.EditorAssetLibrary.load_asset(tex_path)
        if not tex:
            fail("Could not load weight texture: %s" % tex_path)
            continue
        png_path = weight_files[layer_name]
        with Image.open(png_path) as im:
            tw, th = im.size

        rt = tex_to_rt(world, tex, tw, th)
        landscape.landscape_import_weightmap_from_render_target(rt, layer_name)
        log("Applied weight layer: %s (%dx%d)" % (layer_name, tw, th))
    except Exception as e:
        fail("Weight paint failed for %s: %s" % (layer_name, e))

# ── 4. regenerate PCG volumes ──────────────────────────────────────────────
pcg_components = []
for a in unreal.EditorLevelLibrary.get_all_level_actors():
    if a.get_actor_label() in PCG_LABELS:
        comp = a.get_component_by_class(unreal.PCGComponent)
        if comp:
            pcg_components.append((a.get_actor_label(), comp))

log("PCG volumes to regenerate: %d" % len(pcg_components))
for label, comp in pcg_components:
    try:
        comp.generate(force=True)
        log("PCG regen requested: %s" % label)
    except Exception as e:
        try:
            comp.set_editor_property("regenerate_in_editor", True)
            log("PCG regen (fallback) requested: %s" % label)
        except Exception as e2:
            fail("PCG regen failed for %s: %s / %s" % (label, e, e2))

# ── 5. save ────────────────────────────────────────────────────────────────
if SAVE_AFTER:
    try:
        unreal.EditorLoadingAndSavingUtils.save_current_level()
        log("Level saved")
    except Exception as e:
        fail("save_current_level: %s" % e)
    try:
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, False)
        log("Dirty packages saved")
    except Exception as e:
        fail("save_dirty_packages: %s" % e)

print(json.dumps(result, ensure_ascii=False, indent=2))
import gc; gc.collect()
