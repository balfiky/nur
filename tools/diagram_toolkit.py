"""SVG diagram toolkit for Project Nūr architecture diagrams.

Primitives: Canvas, box, diamond, cylinder, group frame, arrow (orthogonal),
legend, note.

Design language
---------------
- Flat, grid-based, no drop shadows.
- Small palette with semantic meaning (process / storage / client / danger /
  attention / neutral).
- Orthogonal arrow routing (L / Z shapes); diagonals only used for explicit
  fan-out relationships.
- Typography hierarchy: title 28 / section 16 / box title 15 / body 12 /
  edge label 11.

The toolkit is only used by ``tools/render_diagram_pngs.py`` to regenerate
the SVG + PNG pairs under ``docs/diagrams/``.  SVGs are the source of truth
that ships with the repo; PNGs are derived artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

FONT_STACK = "'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif"

THEME = {
    "bg": "#fafbfc",
    "ink": "#1f2328",
    "ink_soft": "#57606a",
    "muted": "#8c959f",
    "line": "#d0d7de",
    "line_soft": "#eaeef2",
    "arrow": "#57606a",
}

# Semantic colors.  Each role has three shades: fill (pale), stroke, and ink
# for titles inside that fill.  Body text uses THEME["ink_soft"] regardless.
PALETTE = {
    "process":   {"fill": "#ddf4ff", "stroke": "#218bff", "ink": "#0550ae"},
    "storage":   {"fill": "#eae6ff", "stroke": "#8250df", "ink": "#5931ad"},
    "client":    {"fill": "#dafbe1", "stroke": "#2da44e", "ink": "#116329"},
    "external":  {"fill": "#fff1e5", "stroke": "#fb8f44", "ink": "#9a5700"},
    "danger":    {"fill": "#ffebe9", "stroke": "#cf222e", "ink": "#a40e26"},
    "attention": {"fill": "#fff8c5", "stroke": "#bf8700", "ink": "#633c01"},
    "neutral":   {"fill": "#f6f8fa", "stroke": "#8c959f", "ink": "#1f2328"},
    "blank":     {"fill": "#ffffff", "stroke": "#d0d7de", "ink": "#1f2328"},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _text(x: float, y: float, s: str, *, size: int, weight: int = 400,
          fill: str = THEME["ink"], anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="{FONT_STACK}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{_esc(s)}</text>'
    )


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------


@dataclass
class Canvas:
    width: int
    height: int
    title: str = ""
    subtitle: str = ""
    pieces: list[str] = field(default_factory=list)

    # ---- helpers -------------------------------------------------------

    def add(self, svg: str) -> None:
        self.pieces.append(svg)

    # ---- primitives ----------------------------------------------------

    def group(self, x: float, y: float, w: float, h: float, label: str,
              color: str = "neutral") -> None:
        """Dashed rounded frame with a label in the top-left."""
        c = PALETTE[color]
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" '
            f'fill="none" stroke="{c["stroke"]}" stroke-width="1.5" '
            f'stroke-dasharray="6 5" opacity="0.85"/>'
        )
        self.add(_text(x + 16, y + 22, label, size=13, weight=700,
                       fill=c["ink"]))

    def tile(self, x: float, y: float, w: float, h: float, title: str,
             body: Iterable[str] = (), *, color: str = "process",
             title_size: int = 15, body_size: int = 12) -> None:
        """Solid rounded rectangle with a bold title and optional body lines."""
        c = PALETTE[color]
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
            f'fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="1.5"/>'
        )
        cx = x + w / 2
        self.add(_text(cx, y + 23, title, size=title_size, weight=700,
                       fill=c["ink"], anchor="middle"))
        for i, line in enumerate(body):
            self.add(_text(cx, y + 45 + i * (body_size + 5), line,
                           size=body_size, fill=THEME["ink_soft"],
                           anchor="middle"))

    def pill(self, x: float, y: float, w: float, h: float, label: str, *,
             color: str = "neutral") -> None:
        c = PALETTE[color]
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}" '
            f'fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="1.5"/>'
        )
        self.add(_text(x + w / 2, y + h / 2 + 4, label, size=12, weight=600,
                       fill=c["ink"], anchor="middle"))

    def diamond(self, cx: float, cy: float, w: float, h: float, label: str,
                *, color: str = "attention") -> None:
        c = PALETTE[color]
        hw, hh = w / 2, h / 2
        pts = f"{cx},{cy - hh} {cx + hw},{cy} {cx},{cy + hh} {cx - hw},{cy}"
        self.add(
            f'<polygon points="{pts}" fill="{c["fill"]}" '
            f'stroke="{c["stroke"]}" stroke-width="1.5"/>'
        )
        self.add(_text(cx, cy + 4, label, size=13, weight=700,
                       fill=c["ink"], anchor="middle"))

    def cylinder(self, x: float, y: float, w: float, h: float, title: str,
                 body: Iterable[str] = (), *, color: str = "storage") -> None:
        c = PALETTE[color]
        ry = 10
        path = (
            f"M{x},{y + ry} "
            f"C{x},{y - ry * 0.6} {x + w},{y - ry * 0.6} {x + w},{y + ry} "
            f"L{x + w},{y + h - ry} "
            f"C{x + w},{y + h + ry * 0.6} {x},{y + h + ry * 0.6} {x},{y + h - ry} "
            f"Z"
        )
        self.add(
            f'<path d="{path}" fill="{c["fill"]}" stroke="{c["stroke"]}" '
            f'stroke-width="1.5"/>'
        )
        # Top ellipse outline for the 3D look.
        self.add(
            f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" '
            f'fill="none" stroke="{c["stroke"]}" stroke-width="1.5" '
            f'opacity="0.6"/>'
        )
        cx = x + w / 2
        self.add(_text(cx, y + 30, title, size=14, weight=700,
                       fill=c["ink"], anchor="middle"))
        for i, line in enumerate(body):
            self.add(_text(cx, y + 50 + i * 16, line, size=12,
                           fill=THEME["ink_soft"], anchor="middle"))

    # ---- arrows --------------------------------------------------------

    def arrow(self, start: tuple[float, float], end: tuple[float, float], *,
              label: str = "", dashed: bool = False,
              route: Literal["direct", "h-then-v", "v-then-h"] = "direct",
              color: str | None = None) -> None:
        stroke = color or THEME["arrow"]
        dasharray = ' stroke-dasharray="6 4"' if dashed else ""
        x1, y1 = start
        x2, y2 = end
        if route == "direct":
            d = f"M{x1},{y1} L{x2},{y2}"
            label_pos = ((x1 + x2) / 2, (y1 + y2) / 2)
        elif route == "h-then-v":
            d = f"M{x1},{y1} L{x2},{y1} L{x2},{y2}"
            label_pos = ((x1 + x2) / 2, y1)
        else:  # v-then-h
            d = f"M{x1},{y1} L{x1},{y2} L{x2},{y2}"
            label_pos = (x1, (y1 + y2) / 2)
        self.add(
            f'<path d="{d}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.75" marker-end="url(#arrowhead)"{dasharray}/>'
        )
        if label:
            lx, ly = label_pos
            # White chip so the label doesn't smear over the arrow.
            w = 8 + 7 * len(label)
            self.add(
                f'<rect x="{lx - w / 2}" y="{ly - 10}" width="{w}" '
                f'height="16" rx="4" fill="{THEME["bg"]}" opacity="0.95"/>'
            )
            self.add(_text(lx, ly + 2, label, size=11, weight=600,
                           fill=THEME["ink_soft"], anchor="middle"))

    def connector(self, points: list[tuple[float, float]], *,
                  label: str = "", dashed: bool = False,
                  color: str | None = None) -> None:
        """Explicit polyline for when none of the routes fit."""
        stroke = color or THEME["arrow"]
        dasharray = ' stroke-dasharray="6 4"' if dashed else ""
        d = "M" + " L".join(f"{x},{y}" for x, y in points)
        self.add(
            f'<path d="{d}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.75" marker-end="url(#arrowhead)"{dasharray}/>'
        )
        if label:
            # Label on the middle segment.
            mid = points[len(points) // 2]
            prev = points[len(points) // 2 - 1]
            lx = (mid[0] + prev[0]) / 2
            ly = (mid[1] + prev[1]) / 2
            w = 8 + 7 * len(label)
            self.add(
                f'<rect x="{lx - w / 2}" y="{ly - 10}" width="{w}" '
                f'height="16" rx="4" fill="{THEME["bg"]}" opacity="0.95"/>'
            )
            self.add(_text(lx, ly + 2, label, size=11, weight=600,
                           fill=THEME["ink_soft"], anchor="middle"))

    # ---- text ----------------------------------------------------------

    def note(self, x: float, y: float, text: str, *, size: int = 13,
             weight: int = 400, fill: str | None = None,
             anchor: str = "start") -> None:
        self.add(_text(x, y, text, size=size, weight=weight,
                       fill=fill or THEME["ink"], anchor=anchor))

    def section(self, x: float, y: float, text: str) -> None:
        self.add(_text(x, y, text, size=15, weight=800,
                       fill=THEME["ink"]))

    def legend(self, x: float, y: float, items: list[tuple[str, str]], *,
               title: str = "") -> None:
        """items: list of (color_name, label)."""
        cursor_y = y
        if title:
            self.add(_text(x, cursor_y, title, size=12, weight=700,
                           fill=THEME["ink_soft"]))
            cursor_y += 18
        for color, label in items:
            c = PALETTE[color]
            self.add(
                f'<rect x="{x}" y="{cursor_y - 10}" width="16" height="12" '
                f'rx="3" fill="{c["fill"]}" stroke="{c["stroke"]}" '
                f'stroke-width="1.25"/>'
            )
            self.add(_text(x + 24, cursor_y, label, size=12,
                           fill=THEME["ink_soft"]))
            cursor_y += 18

    # ---- finalize ------------------------------------------------------

    def to_svg(self) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img" '
            f'aria-labelledby="title desc">'
        )
        defs = (
            '<defs>'
            '<marker id="arrowhead" markerWidth="10" markerHeight="10" '
            'refX="9" refY="5" orient="auto" markerUnits="strokeWidth">'
            f'<path d="M1,1 L9,5 L1,9 Z" fill="{THEME["arrow"]}"/>'
            '</marker>'
            '</defs>'
        )
        title_el = f'<title id="title">{_esc(self.title)}</title>'
        desc_el = f'<desc id="desc">{_esc(self.subtitle or self.title)}</desc>'
        bg = f'<rect width="100%" height="100%" fill="{THEME["bg"]}"/>'
        title_line = (
            _text(48, 52, self.title, size=28, weight=800,
                  fill=THEME["ink"])
            if self.title else ""
        )
        subtitle_line = (
            _text(48, 78, self.subtitle, size=14,
                  fill=THEME["ink_soft"])
            if self.subtitle else ""
        )
        body = "\n".join(self.pieces)
        return (
            head + title_el + desc_el + defs + bg + title_line
            + subtitle_line + body + "</svg>"
        )
