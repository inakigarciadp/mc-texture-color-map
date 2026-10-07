# /// script
# dependencies = ["pillow", "numpy"]
# ///
"""Sample the reddit color map into reference_anchors.json: (OKLab color, x, y in tile units) points
that the `reference` layout uses to place each block where blocks of its color sit in that image."""
import json
import os
import sys

import numpy as np
from PIL import Image

from color_map import oklab, srgb_to_linear

TILE = 27  # front-face size in px in the reddit image
STEP, WIN = 9, 9

im = np.asarray(Image.open(sys.argv[1]).convert("RGB")).astype(np.float64)
bg = im[5, 5]
fg = np.abs(im - bg).sum(2) > 12
anchors = []
for y in range(0, im.shape[0] - WIN, STEP):
    for x in range(0, im.shape[1] - WIN, STEP):
        if not fg[y:y + WIN, x:x + WIN].all():
            continue
        lin = srgb_to_linear(im[y:y + WIN, x:x + WIN] / 255).reshape(-1, 3).mean(0)
        L, a, b = oklab(lin)
        cx, cy = (x + WIN / 2) / TILE, (y + WIN / 2) / TILE
        anchors.append([round(L, 4), round(a, 4), round(b, 4), round(cx, 2), round(cy, 2)])
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_anchors.json")
with open(out, "w") as f:
    json.dump(anchors, f, separators=(",", ":"))
print(f"{len(anchors)} anchors -> {out}")
