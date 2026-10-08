# /// script
# dependencies = ["pillow", "numpy", "scipy"]
# ///
"""Minecraft block texture color maps: every full-cube block, drawn as a cube, laid out by color in several ways."""
import argparse
import base64
import glob
import io
import json
import math
import os
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.optimize import linear_sum_assignment

PRISM_DIRS = [
    "~/.local/share/PrismLauncher",
    *glob.glob("/mnt/c/Users/*/AppData/Roaming/PrismLauncher"),
]

# Vanilla hardcodes these tints in BlockColors (plains biome for the biome-dependent ones).
GRASS = (0x91, 0xBD, 0x59)
FOLIAGE = (0x77, 0xAB, 0x2F)
TINTS = {"birch_leaves": (0x80, 0xA7, 0x55), "spruce_leaves": (0x61, 0x99, 0x61), "grass_block": GRASS}

# full cubes you can't get in survival
UNOBTAINABLE = {"bedrock", "budding_amethyst", "command_block", "chain_command_block", "repeating_command_block",
                "structure_block", "jigsaw", "test_block", "test_instance_block", "reinforced_deepslate",
                "frosted_ice", "suspicious_sand", "suspicious_gravel", "spawner", "trial_spawner", "vault"}

# biomes where grass doesn't grow naturally (oceans are skipped by name)
NO_GRASS_BIOMES = {"nether_wastes", "crimson_forest", "warped_forest", "soul_sand_valley", "basalt_deltas",
                   "the_end", "end_barrens", "end_highlands", "end_midlands", "small_end_islands", "the_void"}

# what each view shows: the model face whose color it is laid out by
VIEWS = {"side": "north", "top": "up", "bottom": "down"}


def find_jar(version):
    for d in PRISM_DIRS:
        p = os.path.expanduser(f"{d}/libraries/com/mojang/minecraft/{version}/minecraft-{version}-client.jar")
        if os.path.exists(p):
            return p
    raise SystemExit(f"client jar for {version} not found, pass --jar")


class Assets:
    def __init__(self, jar):
        self.z = zipfile.ZipFile(jar)
        self.names = set(self.z.namelist())
        self._tex = {}

    def json(self, path):
        return json.loads(self.z.read(f"assets/minecraft/{path}"))

    def model(self, ref):
        ref = ref.removeprefix("minecraft:")
        return self.json(f"models/{ref}.json")

    def texture(self, ref):
        ref = ref.removeprefix("minecraft:")
        if ref not in self._tex:
            path = f"assets/minecraft/textures/{ref}.png"
            if path not in self.names:
                self._tex[ref] = None
            else:
                im = Image.open(io.BytesIO(self.z.read(path))).convert("RGBA")
                w = im.width
                self._tex[ref] = im.crop((0, 0, w, w))  # first frame of animated strips
        return self._tex[ref]


def resolve_model(assets, ref):
    """Merge the parent chain: textures (child wins) and the nearest elements."""
    textures, elements = {}, None
    while ref:
        m = assets.model(ref)
        for k, v in m.get("textures", {}).items():
            textures.setdefault(k, v)
        if elements is None and "elements" in m:
            elements = m["elements"]
        ref = m.get("parent")
        if ref and ref.removeprefix("minecraft:").startswith("builtin/"):
            break
    return textures, elements


def lookup(textures, ref):
    for _ in range(10):
        if isinstance(ref, dict):
            ref = ref.get("sprite", "")
        if not ref.startswith("#"):
            return ref
        ref = textures.get(ref[1:], "")
    return None


def is_gray(im):
    a = np.asarray(im, dtype=np.int16)
    m = a[..., 3] > 0
    if not m.any():
        return False
    rgb = a[..., :3][m]
    return (rgb.max(1) - rgb.min(1)).mean() < 6


def tinted(im, rgb):
    a = np.asarray(im, dtype=np.float32)
    a[..., :3] *= np.array(rgb, dtype=np.float32) / 255
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def face_image(assets, block, textures, elements, face):
    out = None
    for el in elements:
        f = el["faces"].get(face)
        if not f:
            continue
        ref = lookup(textures, f["texture"])
        im = assets.texture(ref) if ref else None
        if im is None:
            continue
        if "tintindex" in f and is_gray(im):
            im = tinted(im, TINTS.get(block, FOLIAGE))
        if im.size != (16, 16):
            im = im.resize((16, 16), Image.NEAREST)
        out = im if out is None else Image.alpha_composite(out, im)
    return out


# blockstate properties that only turn the block; every value of these is one placement of the same block
ORIENTATION = {"axis", "facing", "orientation", "rotation"}

# where a model face ends up after a blockstate rotation: x turns up -> north, y turns north -> east
XROT = {"up": "north", "north": "down", "down": "south", "south": "up", "east": "east", "west": "west"}
YROT = {"north": "east", "east": "south", "south": "west", "west": "north", "up": "up", "down": "down"}


def placements(assets, name):
    """(model, x, y) for every orientation the block can be placed in, default state first.
    Other properties (lit, open, charges...) stay at the first variant's values."""
    bs = assets.json(f"blockstates/{name}.json")
    if "variants" not in bs:
        # multipart (mushroom blocks, fences...): fall back to the inventory model
        path = f"assets/minecraft/items/{name}.json"
        if path in assets.names:
            m = assets.json(f"items/{name}.json")["model"]
            if m.get("type", "").removeprefix("minecraft:") == "model":
                return [(m["model"], 0, 0)]
        return []

    def props(key):
        return dict(kv.split("=") for kv in key.split(",") if kv)

    keys = list(bs["variants"])
    first = next((k for k in ("", "axis=y") if k in bs["variants"]), keys[0])
    keys.remove(first)
    base = {k: v for k, v in props(first).items() if k not in ORIENTATION}
    out = []
    for key in [first] + keys:
        if {k: v for k, v in props(key).items() if k not in ORIENTATION} != base:
            continue
        v = bs["variants"][key]
        v = v[0] if isinstance(v, list) else v
        p = (v["model"], v.get("x", 0), v.get("y", 0))
        if p not in out:
            out.append(p)
    return out


def collect_blocks(assets):
    """Every obtainable full-cube block with the faces of each of its placements, as seen from
    the north (front), up, east (right side) and down."""
    names = [n.rsplit("/", 1)[1].removesuffix(".json") for n in assets.names
             if n.startswith("assets/minecraft/blockstates/") and n.endswith(".json")]
    # canonical names come first, so they name the tiles of shared textures (copper over waxed_copper)
    names.sort(key=lambda n: (n.startswith("waxed_"), len(n), n))
    blocks = []
    for name in names:
        if name in UNOBTAINABLE or name.startswith("infested_"):
            continue
        variants = []
        for model, xr, yr in placements(assets, name):
            textures, elements = resolve_model(assets, model)
            if not elements or any(e["from"] != [0, 0, 0] or e["to"] != [16, 16, 16] for e in elements):
                continue
            world = {}
            for f in XROT:
                d = f
                for _ in range(xr // 90 % 4):
                    d = XROT[d]
                for _ in range(yr // 90 % 4):
                    d = YROT[d]
                world[d] = f
            faces = {d: face_image(assets, name, textures, elements, world[d]) for d in ("north", "up", "east", "down")}
            if all(v is not None for v in faces.values()):
                variants.append(faces)
        if variants:
            blocks.append({"name": name, "variants": variants})
    return blocks


def biome_grass_colors(assets):
    """Grass color of every overworld biome (the biome's own color, not the blends at its borders),
    grouped by color: [(rgb, [biomes])]. Same rules as the game: a fixed grass_color, else the
    grass colormap at (temperature, downfall), then the dark forest / swamp modifiers."""
    cmap = Image.open(io.BytesIO(assets.z.read("assets/minecraft/textures/colormap/grass.png"))).convert("RGB")
    groups = {}
    for n in sorted(assets.names):
        if not (n.startswith("data/minecraft/worldgen/biome/") and n.endswith(".json")):
            continue
        name = n.rsplit("/", 1)[1].removesuffix(".json")
        if "ocean" in name or name in NO_GRASS_BIOMES:
            continue
        b = json.loads(assets.z.read(n))
        eff = b.get("effects", {})
        if "grass_color" in eff:
            colors = [int(eff["grass_color"].lstrip("#"), 16)]
        else:
            t = min(max(b["temperature"], 0), 1)
            d = min(max(b["downfall"], 0), 1) * t
            r, g, bb = cmap.getpixel((int((1 - t) * 255), int((1 - d) * 255)))
            colors = [r << 16 | g << 8 | bb]
        mod = eff.get("grass_color_modifier")
        if mod == "dark_forest":
            colors = [((c & 0xFEFEFE) + 0x28340A) >> 1 for c in colors]
        elif mod == "swamp":
            colors = [0x4C763C, 0x6A7039]  # two patch colors picked by noise
        for c in colors:
            groups.setdefault(c, []).append(name)
    return [((c >> 16, c >> 8 & 255, c & 255), names) for c, names in groups.items()]


def add_biome_grass(entries, assets):
    """Replace the grass block's top tile with one per biome grass color."""
    i = next(i for i, e in enumerate(entries) if "grass_block" in e["names"])
    grass = entries.pop(i)
    top = assets.texture("block/grass_block_top")
    for rgb, biomes in biome_grass_colors(assets):
        faces = dict(grass["faces"], up=tinted(top, rgb))
        entries.append({"names": [f"grass_block ({', '.join(biomes)})"], "faces": faces})


def view_entries(blocks, face):
    """The tiles of one view: one per distinct texture any placement of any block shows on that face.
    Blocks sharing a texture (oak log and oak wood bark, dirt under grass) share the tile."""
    entries, idx = [], {}
    for b in blocks:
        for faces in b["variants"]:
            key = faces[face].tobytes()
            if key in idx:
                names = entries[idx[key]]["names"]
                if b["name"] not in names and not b["name"].startswith("waxed_"):
                    names.append(b["name"])
                continue
            idx[key] = len(entries)
            entries.append({"names": [b["name"]], "faces": faces})
    return entries


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)


def oklab(lin):
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]])
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]])
    return m2 @ np.cbrt(m1 @ lin)


def average_color(im):
    """Alpha-weighted mean in linear light, of the face shown largest (front)."""
    a = np.asarray(im, dtype=np.float64) / 255
    w = a[..., 3]
    lin = srgb_to_linear(a[..., :3])
    mean = (lin * w[..., None]).sum((0, 1)) / max(w.sum(), 1e-9)
    return mean


def lch(b):
    L, a, bb = b["lab"]
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def snap_greedy(targets):
    """Each block takes the nearest free grid cell, innermost first."""
    taken, cells = set(), [None] * len(targets)
    for i in sorted(range(len(targets)), key=lambda i: math.hypot(*targets[i])):
        tx, ty = targets[i]
        cx, cy = round(tx), round(ty)
        best = None
        for rad in range(0, 100):
            for dx in range(-rad, rad + 1):
                for dy in range(-rad, rad + 1):
                    if max(abs(dx), abs(dy)) != rad or (cx + dx, cy + dy) in taken:
                        continue
                    d = (cx + dx - tx) ** 2 + (cy + dy - ty) ** 2
                    if best is None or d < best[0]:
                        best = (d, (cx + dx, cy + dy))
            if best and best[0] <= (rad - 0.5) ** 2:
                break
        taken.add(best[1])
        cells[i] = best[1]
    return cells


def snap_optimal(targets, margin=3):
    """Assign blocks to distinct grid cells minimizing the total squared distance to their targets."""
    t = np.array(targets)
    cand = sorted({(int(round(x)) + dx, int(round(y)) + dy) for x, y in t
                   for dx in range(-margin, margin + 1) for dy in range(-margin, margin + 1)})
    c = np.array(cand, dtype=float)
    cost = ((t[:, None, :] - c[None, :, :]) ** 2).sum(2)
    rows, cols = linear_sum_assignment(cost)
    cells = [None] * len(targets)
    for r, k in zip(rows, cols):
        cells[r] = cand[k]
    return cells


def layout_wheel(blocks, gray_chroma, per_sector=8, hue_start=250):
    """A disc: hue goes around (grays get their own wedge at the top, between cyan and blue),
    lightness goes outward (dark center, light rim), so every ray is a gradient.
    Blocks are ranked by hue and cut into sectors with the same count, each sector filled
    from the center out, so the disc comes out round and gapless."""
    keys = []
    for b in blocks:
        L, c, h = lch(b)
        keys.append((360 + L if c < gray_chroma else (h - hue_start) % 360, L))  # neutrals after every hue
    order = sorted(range(len(blocks)), key=lambda i: keys[i][0])
    n = len(order)
    sectors = max(8, round(n / per_sector))
    # rotate so the gray wedge (the last sectors) is centered at the top
    gray = sum(1 for k in keys if k[0] >= 360) / n
    offset = -math.pi / 2 - 2 * math.pi * (1 - gray / 2)
    targets = [None] * n
    for s in range(sectors):
        idx = order[s * n // sectors:(s + 1) * n // sectors]
        width = 2 * math.pi / sectors
        mid = offset + width * (s + 0.5)
        idx.sort(key=lambda i: keys[i][1])
        for k, i in enumerate(idx):
            r = math.sqrt(2 * (k + 0.5) / width)  # ring area up to r = width/2 * r^2 = k+0.5 cells
            targets[i] = (r * math.cos(mid), r * math.sin(mid))
    return snap_greedy(targets), []


def layout_reference(blocks, gray_chroma, k=24):
    """Same color distribution as the reddit image: each block goes to the mean position of the
    k reference samples closest in color (reference_anchors.json, see extract_reference.py).
    Both color sets are standardized per OKLab channel first, which evens out the screenshot's lighting."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_anchors.json")
    with open(path) as f:
        ref = np.array(json.load(f))
    rc, rp = ref[:, :3], ref[:, 3:]
    bc = np.array([b["lab"] for b in blocks])
    rc = (rc - rc.mean(0)) / rc.std(0)
    bc = (bc - bc.mean(0)) / bc.std(0)
    d = ((bc[:, None, :] - rc[None, :, :]) ** 2).sum(2)
    near = np.argsort(d, 1)[:, :k]
    w = 1 / (np.take_along_axis(d, near, 1) + 0.02)
    pos = (rp[near] * w[..., None]).sum(1) / w.sum(1)[:, None]
    pos -= pos.mean(0)
    # the reddit map holds ~500 blocks; scale its area to our block count
    pos *= math.sqrt(len(blocks) / 500)
    return snap_optimal([tuple(p) for p in pos]), []


def layout_spectrum(blocks, gray_chroma, hue_start=15):
    """A rectangle: hue goes left to right (grays in the leftmost columns), lightness bottom to top.
    Every column holds the same number of blocks, sorted dark to light, so each one is a gradient."""
    n = len(blocks)
    rows = max(4, round(math.sqrt(n / 2)))
    keys = []
    for b in blocks:
        L, c, h = lch(b)
        keys.append((-1 if c < gray_chroma else (h - hue_start) % 360, L))
    order = sorted(range(n), key=lambda i: keys[i])
    cells = [None] * n
    for col in range(math.ceil(n / rows)):
        idx = sorted(order[col * rows:(col + 1) * rows], key=lambda i: keys[i][1])
        for r, i in enumerate(idx):
            cells[i] = (col, rows - 1 - r)
    return cells, []


# OKLab hue ranges (degrees) of each family, checked after the neutral/brown/pink rules in family()
FAMILIES = [("red", 0, 45), ("orange", 45, 75), ("yellow", 75, 115), ("green", 115, 170),
            ("cyan", 170, 230), ("blue", 230, 290), ("purple", 290, 325), ("magenta", 325, 360)]


def family(b, gray_chroma):
    L, c, h = lch(b)
    if c < gray_chroma or (L < 0.3 and c < 0.06):
        return "white" if L > 0.85 else "gray" if L > 0.38 else "black"
    if 35 <= h < 100 and L < 0.62 and c < 0.12:
        return "brown"
    if (h >= 330 or h < 40) and L > 0.68:
        return "pink"
    for name, lo, hi in FAMILIES:
        if lo <= h < hi:
            return name


def layout_rows(blocks, gray_chroma, width=22):
    """One band per color family. Inside a band, lightness goes left to right (dark to light)
    and, within each column, chroma goes top to bottom (most saturated first)."""
    order = ["white", "gray", "black", "brown", "red", "orange", "yellow", "green", "cyan", "blue", "purple",
             "magenta", "pink"]
    groups = {f: [] for f in order}
    for i, b in enumerate(blocks):
        groups[family(b, gray_chroma)].append(i)
    cells, labels, y = [None] * len(blocks), [], 0
    for f in order:
        idx = sorted(groups[f], key=lambda i: lch(blocks[i])[0])
        if not idx:
            continue
        h = math.ceil(len(idx) / width)
        cols = math.ceil(len(idx) / h)
        for col in range(cols):
            chunk = sorted(idx[col * h:(col + 1) * h], key=lambda i: -lch(blocks[i])[1])
            for r, i in enumerate(chunk):
                cells[i] = (col, y + r)
        labels.append((y, h, f))
        y += h + 1
    return cells, labels


def layout_som(blocks, gray_chroma, epochs=80, seed=0):
    """A self-organizing map: a square grid where every block sits next to the blocks most similar
    to it in all of lightness, hue and chroma, so any straight walk across it is a smooth gradient."""
    n = len(blocks)
    gw = math.ceil(math.sqrt(n))
    gh = math.ceil(n / gw)
    data = np.array([b["lab"] for b in blocks]) * np.array([1.0, 2.0, 2.0])  # weight hue/chroma like lightness
    gy, gx = np.mgrid[0:gh, 0:gw]
    grid = np.stack([gx.ravel(), gy.ravel()], 1).astype(float)
    # init on the data's two main axes so the result is deterministic
    mean = data.mean(0)
    u, sv, vt = np.linalg.svd(data - mean, full_matrices=False)
    sd = sv[:2] / math.sqrt(n)
    w = mean + ((grid[:, :1] / (gw - 1) - 0.5) * 3 * sd[0]) * vt[0] + ((grid[:, 1:] / (gh - 1) - 0.5) * 3 * sd[1]) * vt[1]
    for e in range(epochs):
        sigma = max(gw / 2 * (0.5 / (gw / 2)) ** (e / (epochs - 1)), 0.5)
        bmu = ((data[:, None, :] - w[None]) ** 2).sum(2).argmin(1)
        g2 = ((grid[:, None, :] - grid[None, :, :]) ** 2).sum(2)
        h = np.exp(-g2 / (2 * sigma ** 2))[bmu]  # (n, nodes)
        w = (h[:, :, None] * data[:, None, :]).sum(0) / h.sum(0)[:, None]
    cost = ((data[:, None, :] - w[None]) ** 2).sum(2)
    rows, cols = linear_sum_assignment(cost)
    cells = [None] * n
    for r, c in zip(rows, cols):
        cells[r] = (int(grid[c, 0]), int(grid[c, 1]))
    return cells, []


LAYOUTS = {"wheel": layout_wheel, "reference": layout_reference, "spectrum": layout_spectrum,
           "rows": layout_rows, "som": layout_som}


def shade(im, k):
    a = np.asarray(im, dtype=np.float32)
    a[..., :3] *= k
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def cube_back(faces, s, d):
    """Top and right faces of an oblique cube (viewed from above-right): front face s×s at (0, d),
    top and right faces d deep. Returned as one (s+d)×(s+d) sprite without the front."""
    sp = Image.new("RGBA", (s + d, s + d))
    top = faces["up"].resize((s, s), Image.NEAREST)
    right = shade(faces["east"], 0.62).resize((s, s), Image.NEAREST)
    # top: parallelogram from (0,d)-(s,d) up to (d,0)-(s+d,0); inverse affine maps out->in
    sp.alpha_composite(top.transform((s + d, d), Image.AFFINE, (1, -1, d, 0, s / d, 0), Image.NEAREST), (0, 0))
    sp.alpha_composite(right.transform((d, s + d), Image.AFFINE, (s / d, 0, 0, -1, 1, d), Image.NEAREST), (s, 0))
    return sp


def render(blocks, cells, s, d, bg, pad, labels=(), face="north"):
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    x0, y0 = min(xs), min(ys)
    font = ImageFont.load_default(size=max(12, s // 3))
    lw = max((int(font.getlength(t)) for _, _, t in labels), default=-pad) + pad
    W = (max(xs) - x0 + 1) * s + d + 2 * pad + lw
    H = (max(ys) - y0 + 1) * s + d + 2 * pad
    img = Image.new("RGBA", (W, H), bg)
    pos = [(pad + lw + (cx - x0) * s, pad + (cy - y0) * s) for cx, cy in cells]
    # every front face lies in the nearest plane, so draw all top/right faces first and the fronts over them;
    # a top/right face then only shows where no neighbour's front covers it
    for i, b in enumerate(blocks):
        img.alpha_composite(cube_back(b["faces"], s, d), pos[i])
    for i, b in enumerate(blocks):
        img.alpha_composite(shade(b["faces"][face].resize((s, s), Image.NEAREST), 0.86), (pos[i][0], pos[i][1] + d))
    draw = ImageDraw.Draw(img)
    for y, h, text in labels:
        draw.text((pad, pad + d + (y - y0 + h / 2) * s), text, font=font, fill=(235, 235, 230), anchor="lm")
    return img


def png_b64(im):
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def write_html(views, path, gray_chroma):
    """One self-contained page: textures inlined as base64 PNGs, every view's tiles and layouts."""
    tex, tex_idx = [], {}
    data = {"tex": tex, "gray": gray_chroma, "views": {}}
    for view, (entries, layouts) in views.items():
        t = []
        for e in entries:
            im = e["faces"][VIEWS[view]]
            key = im.tobytes()
            if key not in tex_idx:
                tex_idx[key] = len(tex)
                tex.append(png_b64(im))
            t.append(tex_idx[key])
        data["views"][view] = {"n": [", ".join(n.replace("_", " ") for n in e["names"]) for e in entries],
                               "t": t, "layouts": layouts,
                               # OKLab ×1000 per tile, for match mode
                               "c": [round(v * 1000) for e in entries for v in e["lab"]]}
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer_template.html")).read()
    with open(path, "w") as f:
        f.write(tpl.replace("/*DATA*/null", json.dumps(data, separators=(",", ":"))))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="26.3")
    ap.add_argument("--jar")
    ap.add_argument("--size", type=int, default=64, help="cube face size in px (multiple of 16 is crispest)")
    ap.add_argument("--gray-chroma", type=float, default=0.03, help="OKLab chroma under which a block counts as gray")
    ap.add_argument("--layouts", default=",".join(LAYOUTS), help=f"comma-separated, from: {', '.join(LAYOUTS)}")
    ap.add_argument("--png-views", default="side", help=f"views to render as png, comma-separated, from: {', '.join(VIEWS)}")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    args = ap.parse_args()

    assets = Assets(args.jar or find_jar(args.version))
    blocks = collect_blocks(assets)
    os.makedirs(args.out, exist_ok=True)
    s = args.size
    data, html_views = {}, {}
    for view, face in VIEWS.items():
        entries = view_entries(blocks, face)
        if view == "top":
            add_biome_grass(entries, assets)
        for e in entries:
            lin = average_color(e["faces"][face])
            e["lab"] = oklab(lin)
            e["hex"] = "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in np.clip(linear_to_srgb(lin), 0, 1))
        data[view] = [{"blocks": e["names"], "hex": e["hex"], "oklab": [round(x, 4) for x in e["lab"]], "cells": {}}
                      for e in entries]
        layouts = {}
        for name in args.layouts.split(","):
            cells, labels = LAYOUTS[name](entries, args.gray_chroma)
            layouts[name] = [v for c in cells for v in c]
            for row, c in zip(data[view], cells):
                row["cells"][name] = list(c)
            if view in args.png_views.split(","):
                img = render(entries, cells, s, s // 4, (128, 128, 124, 255), s, labels, face)
                suffix = "" if view == "side" else f"-{view}"
                png = os.path.join(args.out, f"color-map-{args.version}-{name}{suffix}.png")
                img.save(png)
                print(f"{view}: {len(entries)} tiles -> {png} ({img.width}x{img.height})")
        html_views[view] = (entries, layouts)
    with open(os.path.join(args.out, f"colors-{args.version}.json"), "w") as f:
        json.dump(data, f, indent=1)
    html = os.path.join(args.out, f"color-map-{args.version}.html")
    write_html(html_views, html, args.gray_chroma)
    print(f"-> {html} ({os.path.getsize(html) // 1024} KB)")

if __name__ == "__main__":
    main()
