"""Turns the script typeface into outlines so the logo needs no font at runtime (README images, favicons, the web UI).
usage: python docs/assets/brand/src/make_logo.py   (needs fontTools; prints JSON with the path data)
The typeface is Dancing Script (SIL Open Font License, see DancingScript-OFL.txt)."""
import json
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).parent
WEIGHT = 700


def outlines(text, size=100.0, tracking=0.0, origin=(0.0, 0.0)):
    font = instantiateVariableFont(TTFont(HERE / "DancingScript-Variable.ttf"), {"wght": WEIGHT})
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    upm = font["head"].unitsPerEm
    k = size / upm
    x, pieces, bounds = origin[0], [], [1e9, 1e9, -1e9, -1e9]
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
        gs[name].draw(TransformPen(pen, (k, 0, 0, -k, x, origin[1])))        # flip y: SVG grows downwards, fonts upwards
        pieces.append(pen.getCommands())
        bp = BoundsPen(gs); gs[name].draw(TransformPen(bp, (k, 0, 0, -k, x, origin[1])))
        if bp.bounds:
            b = bp.bounds; bounds = [min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3])]
        x += hmtx[name][0] * k + tracking
    return "".join(pieces), bounds


if __name__ == "__main__":
    word, wb = outlines("Markbook")
    m, mb = outlines("M")
    print(json.dumps({"word": word, "word_bounds": wb, "m": m, "m_bounds": mb}))
