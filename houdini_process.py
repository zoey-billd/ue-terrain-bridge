# -*- coding: utf-8 -*-
"""
Houdini terrain processing stage for the Gaea->Houdini->UE bridge.

Run via hython:
  hython.exe houdini_process.py [input] [out_height] [out_grass] [out_rock] [out_dirt]

Reads a Gaea heightmap, derives grass/rock/dirt weight masks from slope +
elevation, and writes a 16-bit height PNG + three 8-bit weight PNGs.
"""
import sys
import os
import json

# ── resolve paths ──────────────────────────────────────────────────────────
if len(sys.argv) >= 6:
    INPUT_H = sys.argv[1]
    OUT_H = sys.argv[2]
    OUT_GRASS = sys.argv[3]
    OUT_ROCK = sys.argv[4]
    OUT_DIRT = sys.argv[5]
else:
    cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        h = cfg["houdini"]
        INPUT_H = h["input_heightmap"]
        OUT_H = h["output_height"]
        OUT_GRASS = h["output_weights"]["grass"]
        OUT_ROCK = h["output_weights"]["rock"]
        OUT_DIRT = h["output_weights"]["dirt"]
    else:
        print("USAGE: hython houdini_process.py <input> <out_height> <out_grass> <out_rock> <out_dirt>")
        sys.exit(1)

print("[houdini] input :", INPUT_H)
print("[houdini] out_h :", OUT_H)
print("[houdini] grass :", OUT_GRASS)
print("[houdini] rock  :", OUT_ROCK)
print("[houdini] dirt  :", OUT_DIRT)

import hou
import numpy as np
from PIL import Image

geo = hou.node("/obj").createNode("geo", "terrain_bridge")

def get_layer_array(node, layer_index=0):
    """Cook node and return the requested layer's volume as a 2D numpy array (y, x)."""
    g = node.geometry()
    prims = g.prims()
    if layer_index >= len(prims):
        return None
    prim = prims[layer_index]
    # allVoxels() returns a flat array; reshape to (voxels_y, voxels_x)
    res = prim.resolution()  # tuple (nx, ny, nz) for volumes
    nx, ny = res[0], res[1]
    flat = np.array(prim.allVoxels())
    return flat.reshape((ny, nx))

# ── 1. import heightmap ────────────────────────────────────────────────────
src = geo.createNode("heightfield_file")
src.parm("filename").set(INPUT_H)
print("[houdini] source:", src.path())

# ── 1b. auto-range the incoming heightfield to real meters ─────────────────
# Gaea's EXR/PNG export writes normalized values relative to a fixed vertical
# span, so one terrain may occupy only a narrow band (e.g. 0.45..0.515).
# A hard-coded 0..1 -> 0..3000 remap then leaves the terrain nearly flat.
# Detect the actual data range and stretch it to 0..HEIGHT_SCALE, so both the
# slope masks and the exported 16-bit height use the full relief.
HEIGHT_SCALE = 3000.0
raw = get_layer_array(src, 0)
raw_min = float(raw.min())
raw_max = float(raw.max())
if raw_max - raw_min < 1e-6:
    raw_min, raw_max = 0.0, 1.0
print("[houdini] source range: %.6f .. %.6f (span %.6f)"
      % (raw_min, raw_max, raw_max - raw_min))

height_scaled = geo.createNode("heightfield_remap")
height_scaled.setInput(0, src)
height_scaled.parm("layer").set("height")
height_scaled.parm("inputmin").set(raw_min)
height_scaled.parm("inputmax").set(raw_max)
height_scaled.parm("outputmin").set(0)
height_scaled.parm("outputmax").set(HEIGHT_SCALE)
print("[houdini] height remapped %.4f..%.4f -> 0..%.0f"
      % (raw_min, raw_max, HEIGHT_SCALE))

# ── helper: create a slope/elevation mask ──────────────────────────────────
def make_mask(parent, input_node, name,
              slope_min=0, slope_max=90,
              height_min=-100000, height_max=100000,
              use_height=False):
    m = parent.createNode("heightfield_maskbyfeature")
    m.setInput(0, input_node)
    # explicitly disable ALL feature masks first
    for toggle in ("maskbyslope", "maskbyheight", "maskbycurvature",
                   "maskbydir", "maskbyocclusion"):
        try:
            m.parm(toggle).set("0")
        except Exception:
            pass
    # enable slope
    m.parm("maskbyslope").set("1")
    m.parm("min_slopeangle").set(slope_min)
    m.parm("max_slopeangle").set(slope_max)
    if use_height:
        m.parm("maskbyheight").set("1")
        m.parm("computerange").set("0")
        m.parm("minheight").set(height_min)
        m.parm("maxheight").set(height_max)
    # replace = combine all enabled feature masks into the output mask
    # (multiply would also multiply with the input's empty mask -> all zeros)
    m.parm("combine").set("replace")
    return m

# ── 2. grass: low slope + low elevation ────────────────────────────────────
grass = make_mask(geo, height_scaled, "grass",
                  slope_min=0, slope_max=30,
                  height_min=-500, height_max=2500,
                  use_height=True)

# ── 3. rock: high slope ────────────────────────────────────────────────────
rock = make_mask(geo, height_scaled, "rock",
                 slope_min=35, slope_max=90)

# ── 4. dirt: mid slope ─────────────────────────────────────────────────────
dirt = make_mask(geo, height_scaled, "dirt",
                 slope_min=15, slope_max=50)

# ── helper: export a mask layer (auto-ranged 0..1 -> 0..255) ───────────────
def make_output(parent, input_node, layer_name, path):
    o = parent.createNode("heightfield_output")
    o.setInput(0, input_node)
    o.parm("output").set(path)
    o.parm("out_format").set("single")
    o.parm("red_channel").set(layer_name)
    try:
        o.parm("red_auto_rangex").set("1")
        o.parm("red_auto_rangey").set("1")
    except Exception:
        pass
    return o

# ── helper: export height as 16-bit (map 0..3000m -> 0..65535) ─────────────
def make_height_output(parent, input_node, path, height_min=0.0, height_max=3000.0):
    o = parent.createNode("heightfield_output")
    o.setInput(0, input_node)
    o.parm("output").set(path)
    o.parm("out_format").set("single")
    o.parm("red_channel").set("height")
    # explicit range: map [height_min, height_max] -> [0, 65535]
    try:
        o.parm("red_auto_rangex").set("0")
        o.parm("red_auto_rangey").set("0")
        o.parm("red_from_rangex").set(height_min)
        o.parm("red_from_rangey").set(height_max)
        o.parm("red_to_rangex").set(0)
        o.parm("red_to_rangey").set(65535)
    except Exception as e:
        print("[houdini] height range set warning:", e)
    return o

# ── 5. cook all nodes then export via numpy (full bit-depth control) ────────
print("[houdini] cooking networks...")

# cook each mask by reading its geometry (forces cook)
height_arr = get_layer_array(height_scaled, 0)
grass_arr = get_layer_array(grass, 1)   # mask is the 2nd prim
rock_arr = get_layer_array(rock, 1)
dirt_arr = get_layer_array(dirt, 1)

print("[houdini] height: min=%.1f max=%.1f" % (height_arr.min(), height_arr.max()))
print("[houdini] grass:  min=%.3f max=%.3f nonzero=%d" % (grass_arr.min(), grass_arr.max(), (grass_arr > 0).sum()))
print("[houdini] rock:   min=%.3f max=%.3f nonzero=%d" % (rock_arr.min(), rock_arr.max(), (rock_arr > 0).sum()))
print("[houdini] dirt:   min=%.3f max=%.3f nonzero=%d" % (dirt_arr.min(), dirt_arr.max(), (dirt_arr > 0).sum()))

# export height as 16-bit PNG (map 0..3000 -> 0..65535)
h16 = np.clip((height_arr / HEIGHT_SCALE) * 65535.0, 0, 65535).astype(np.uint16)
Image.fromarray(h16).save(OUT_H)
print("[houdini] exported height 16-bit: %s" % OUT_H)

# export weights as 8-bit PNG (mask values are 0..1)
for arr, path in [(grass_arr, OUT_GRASS), (rock_arr, OUT_ROCK), (dirt_arr, OUT_DIRT)]:
    a8 = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(a8).save(path)
    print("[houdini] exported weight: %s" % path)

# verify
ok = True
for p in (OUT_H, OUT_GRASS, OUT_ROCK, OUT_DIRT):
    exists = os.path.exists(p)
    size = os.path.getsize(p) if exists else 0
    print("[houdini] %s %s (%d bytes)" % ("OK " if exists else "MISS", p, size))
    if not exists:
        ok = False

try:
    geo.destroy()
except Exception:
    pass

print("[houdini]", "DONE" if ok else "INCOMPLETE")
sys.exit(0 if ok else 1)
