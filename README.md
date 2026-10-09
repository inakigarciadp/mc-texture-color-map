# mc-texture-color-map

A color map of every full-cube block's texture, like [this reddit post](https://www.reddit.com/r/Minecraft/comments/14jrc92/created_a_full_color_palette_for_all_the_blocks/), for picking palettes and gradients.

Open `out/color-map-26.3.html` in a browser (it works offline and on a phone). To rebuild it, you need [uv](https://docs.astral.sh/uv/) and a Minecraft client jar for the version:

```
uv run color_map.py [--layouts wheel,wheel-gaps,reference,reference-gaps,spectrum,rows,som] [--png-views side,top,bottom] [--version 26.3] [--jar PATH] [--size 64] [--gray-chroma 0.03] [--out out/]
```

- Reads textures, block models and blockstates straight from the client jar. It finds Prism Launcher's (`libraries/com/mojang/minecraft/<version>/`); for other launchers pass `--jar`, e.g. the vanilla launcher's `.minecraft/versions/<version>/<version>.jar`.
- Keeps blocks whose model is one or more full 0–16 cubes (multipart blocks use their item model). Blocks you can't get in survival are left out (`UNOBTAINABLE`: command blocks, bedrock, suspicious sand…). Animated textures use their first frame. Tinted textures (grass, leaves) get their plains color. In the top view, the grass block gets one tile per biome grass color: the biome's own color, not the blends at its borders. Swamps get both of their patch colors, and biomes with the same color share a tile.
- Color = alpha-weighted mean of the front face, in linear light, compared in OKLab.
- Three views, each laid out by the color of its own face: `side` (walls), `top` (floors), `bottom` (ceilings).
- Blocks that can be turned (logs, pillars, barrels, observers, furnaces…) show every texture their placements put on that face. A log, for example, has a bark tile and a rings tile in every view. Only orientation properties count (`ORIENTATION`), and the rest stay at the default state. Blocks sharing a texture share one tile, and its tooltip lists all of them (oak log and oak wood bark).
- Output:
  - `out/color-map-<version>-<layout>[-<view>].png`: side view by default, other views with `--png-views`.
  - `out/color-map-<version>.html`: a single self-contained page (textures inlined, about 400 KB) to open on a phone. A sidebar has the face, layout and gaps controls, and shows the selected block's texture, name and hex color. The block name also shows on hover. **Match**: select a tile and press `Find matching blocks`. The matching panel keeps that block on screen in every view, and the map dims everything except its zones: similar hue (same hue ±30°, any lightness), neutrals (grays) and accent (opposite hue ±30°). Each zone can be toggled from the panel. Built from `viewer_template.html`.
  - `out/colors-<version>.json`: per view, a list of tiles with their blocks, hex, OKLab and grid cell per layout.

Layouts:
- `wheel`: a disc. Hue goes around it, and grays get a wedge at the top. Lightness goes from a dark center to a light rim, so every ray is a gradient. Each hue slice holds the same number of blocks.
- `reference`: the reddit image's color distribution, packed into a solid shape with no holes. Each block goes to the mean position of the reference samples nearest in color. The samples are stored in `reference_anchors.json`, made with `uv run extract_reference.py <reddit-image.png>`.
- `spectrum`: a rectangle. Hue runs left to right (grays first), and each column runs dark to light, bottom to top.
- `rows`: one labeled band per color family (white, gray, black, brown, red… pink). Within a band, dark is on the left and light on the right.
- `som`: a self-organizing map. A square where every block sits next to the most similar blocks, so any straight line across it is a smooth gradient.

Gapped variants (`<layout>-gaps`, the `gaps` toggle in the viewer, off for layouts without one) leave empty space where no block has the colors, so touching tiles are close in color and a gap means a jump:
- `wheel-gaps`: angle follows the real hue (half true hue, half rank, so the crowded reds to yellows still fit), and radius follows the real lightness.
- `reference-gaps`: keeps the reddit image's gaps. Each block goes where the reference samples nearest in color bunch up, so it never lands in the gap between two clusters.

## Credits and license

- Inspired by [this color palette posted on r/Minecraft](https://www.reddit.com/r/Minecraft/comments/14jrc92/created_a_full_color_palette_for_all_the_blocks/); `reference_anchors.json` is sampled from that image.
- Code under the [MIT License](LICENSE).
- Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft. Block textures © Mojang, shown in the generated maps.
