#!/usr/bin/env python3
"""Regenerate the architecture diagrams as SVG + PNG under docs/diagrams/.

SVG sources are built from ``tools/build_diagrams.py``; PNGs are rasterized
with cairosvg at 2x device pixels.  Both are tracked in git — SVGs are the
source of truth that GitHub and local HTML export embed, and PNGs are the
pre-rendered preview that the README and docs use so contributors don't
need cairosvg installed just to read the docs.

Usage::

    python3 tools/render_diagram_pngs.py

Requires ``cairosvg`` (listed in the dev extras).
"""

from __future__ import annotations

import sys
from pathlib import Path

import cairosvg

# Make tools/ importable when running the script directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nur_tools.build_diagrams import BUILDERS  # noqa: E402


OUT_DIR = ROOT / "docs" / "diagrams"
PNG_SCALE = 2  # retina-style preview for README/docs


def render_one(name: str) -> None:
    canvas = BUILDERS[name]()
    svg_text = canvas.to_svg()
    svg_path = OUT_DIR / f"{name}.svg"
    png_path = OUT_DIR / f"{name}.png"
    svg_path.write_text(svg_text, encoding="utf-8")
    cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        write_to=str(png_path),
        output_width=canvas.width * PNG_SCALE,
        output_height=canvas.height * PNG_SCALE,
    )
    print(f"wrote {svg_path.name} and {png_path.name}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in BUILDERS:
        render_one(name)


if __name__ == "__main__":
    main()
