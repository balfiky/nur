#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
CLASS_RULE_RE = re.compile(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}")
DECL_RE = re.compile(r"([A-Za-z-]+)\s*:\s*([^;]+)")
PATH_CMD_RE = re.compile(r"([MLC])([^MLC]+)")


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse_style_block(text: str) -> dict[str, dict[str, str]]:
    styles: dict[str, dict[str, str]] = {}
    for cls, body in CLASS_RULE_RE.findall(text or ""):
        styles[cls] = {k.strip(): v.strip() for k, v in DECL_RE.findall(body)}
    return styles


def parse_inline_style(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    return {k.strip(): v.strip() for k, v in DECL_RE.findall(text)}


def parse_color(value: str | None, default=(0, 0, 0, 255)) -> tuple[int, int, int, int]:
    if not value:
        return default
    value = value.strip()
    if value == "none":
        return (0, 0, 0, 0)
    if value.startswith("#") and len(value) == 7:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5)) + (255,)
    return default


def parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    value = value.strip().replace("px", "")
    if value.endswith("%"):
        return default
    return float(value)


def parse_length(value: str | None, basis: float, default: float = 0.0) -> float:
    if value is None:
        return default
    value = value.strip().replace("px", "")
    if value.endswith("%"):
        return basis * float(value[:-1]) / 100.0
    return float(value)


def parse_points(value: str) -> list[tuple[float, float]]:
    pts = []
    for pair in value.split():
        x, y = pair.split(",")
        pts.append((float(x), float(y)))
    return pts


def sample_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = 24,
) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = (
            mt**3 * p0[0]
            + 3 * mt**2 * t * p1[0]
            + 3 * mt * t**2 * p2[0]
            + t**3 * p3[0]
        )
        y = (
            mt**3 * p0[1]
            + 3 * mt**2 * t * p1[1]
            + 3 * mt * t**2 * p2[1]
            + t**3 * p3[1]
        )
        pts.append((x, y))
    return pts


def parse_path(d: str) -> tuple[list[tuple[float, float]], tuple[float, float] | None]:
    points: list[tuple[float, float]] = []
    arrow_dir: tuple[float, float] | None = None
    current = (0.0, 0.0)
    for cmd, raw in PATH_CMD_RE.findall(d):
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", raw)]
        if cmd == "M":
            current = (nums[0], nums[1])
            points.append(current)
        elif cmd == "L":
            next_pt = (nums[0], nums[1])
            points.append(next_pt)
            arrow_dir = (next_pt[0] - current[0], next_pt[1] - current[1])
            current = next_pt
        elif cmd == "C":
            p1 = (nums[0], nums[1])
            p2 = (nums[2], nums[3])
            p3 = (nums[4], nums[5])
            curve = sample_cubic(current, p1, p2, p3)
            points.extend(curve[1:])
            arrow_dir = (p3[0] - p2[0], p3[1] - p2[1])
            current = p3
    return points, arrow_dir


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def element_style(elem: ET.Element, class_styles: dict[str, dict[str, str]]) -> dict[str, str]:
    style: dict[str, str] = {}
    cls = elem.attrib.get("class", "")
    for name in cls.split():
        style.update(class_styles.get(name, {}))
    style.update(parse_inline_style(elem.attrib.get("style")))
    for key in ("fill", "stroke", "stroke-width", "text-anchor", "opacity"):
        if key in elem.attrib:
            style[key] = elem.attrib[key]
    return style


def draw_arrowhead(
    draw: ImageDraw.ImageDraw,
    end: tuple[float, float],
    vec: tuple[float, float] | None,
    color: tuple[int, int, int, int],
    scale: float,
) -> None:
    if not vec:
        return
    vx, vy = vec
    length = math.hypot(vx, vy)
    if length == 0:
        return
    ux, uy = vx / length, vy / length
    size = 10 * scale
    left = (
        end[0] - ux * size - uy * (size * 0.45),
        end[1] - uy * size + ux * (size * 0.45),
    )
    right = (
        end[0] - ux * size + uy * (size * 0.45),
        end[1] - uy * size - ux * (size * 0.45),
    )
    draw.polygon([end, left, right], fill=color)


def render_svg(svg_path: Path, png_path: Path, scale: float = 2.0) -> None:
    root = ET.parse(svg_path).getroot()
    width = int(parse_float(root.attrib.get("width")) * scale)
    height = int(parse_float(root.attrib.get("height")) * scale)
    image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)

    style_text = "".join(
        elem.text or ""
        for elem in root.findall(".//svg:style", SVG_NS)
        if (elem.text or "").strip()
    )
    class_styles = parse_style_block(style_text)

    for elem in root.iter():
        tag = strip_ns(elem.tag)
        if tag in {"svg", "defs", "style", "title", "desc", "marker", "filter", "feDropShadow"}:
            continue

        style = element_style(elem, class_styles)

        if tag == "rect":
            x = parse_length(elem.attrib.get("x"), width / scale) * scale
            y = parse_length(elem.attrib.get("y"), height / scale) * scale
            w = parse_length(elem.attrib.get("width"), width / scale) * scale
            h = parse_length(elem.attrib.get("height"), height / scale) * scale
            rx = parse_float(elem.attrib.get("rx")) * scale
            fill = parse_color(style.get("fill"), (255, 255, 255, 255))
            stroke = parse_color(style.get("stroke"), (0, 0, 0, 0))
            stroke_width = max(1, int(parse_float(style.get("stroke-width"), 1) * scale))
            if fill[3] > 0:
                draw.rounded_rectangle((x, y, x + w, y + h), radius=rx, fill=fill)
            if stroke[3] > 0 and stroke_width > 0:
                draw.rounded_rectangle(
                    (x, y, x + w, y + h),
                    radius=rx,
                    outline=stroke,
                    width=stroke_width,
                )

        elif tag == "polygon":
            pts = [(x * scale, y * scale) for x, y in parse_points(elem.attrib["points"])]
            fill = parse_color(style.get("fill"), (255, 255, 255, 255))
            stroke = parse_color(style.get("stroke"), (0, 0, 0, 0))
            stroke_width = max(1, int(parse_float(style.get("stroke-width"), 1) * scale))
            if fill[3] > 0:
                draw.polygon(pts, fill=fill)
            if stroke[3] > 0:
                draw.line(pts + [pts[0]], fill=stroke, width=stroke_width)

        elif tag == "path":
            points, arrow_dir = parse_path(elem.attrib.get("d", ""))
            if not points:
                continue
            pts = [(x * scale, y * scale) for x, y in points]
            stroke = parse_color(style.get("stroke"), (0, 0, 0, 255))
            stroke_width = max(1, int(parse_float(style.get("stroke-width"), 1) * scale))
            draw.line(pts, fill=stroke, width=stroke_width)
            if "marker-end" in elem.attrib:
                draw_arrowhead(draw, pts[-1], arrow_dir, stroke, scale)

        elif tag == "text":
            text = "".join(elem.itertext()).strip()
            if not text:
                continue
            x = parse_float(elem.attrib.get("x")) * scale
            y = parse_float(elem.attrib.get("y")) * scale
            font_size = max(10, int(parse_float(style.get("font-size"), 16) * scale))
            weight = style.get("font-weight", "400")
            font = load_font(font_size, bold=weight.isdigit() and int(weight) >= 700)
            fill = parse_color(style.get("fill"), (15, 23, 42, 255))
            anchor = style.get("text-anchor", "start")
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = x
            if anchor == "middle":
                tx = x - tw / 2
            elif anchor == "end":
                tx = x - tw
            ty = y - th
            draw.text((tx, ty), text, fill=fill, font=font)

    image.save(png_path)


def main() -> None:
    diagram_dir = Path("docs/diagrams")
    for svg_path in sorted(diagram_dir.glob("*.svg")):
        png_path = svg_path.with_suffix(".png")
        render_svg(svg_path, png_path)
        print(png_path)


if __name__ == "__main__":
    main()
