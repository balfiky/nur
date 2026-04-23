#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path
from typing import Iterable

import markdown


ROOT = Path(__file__).resolve().parent.parent
DOC_PATHS = [
    path
    for path in sorted(ROOT.glob("*.md"))
    if path.name not in {".pytest_cache/README.md", "PROJECT_NUR_OVERVIEW.md"}
] + sorted((ROOT / "docs").glob("*.md"))


CSS = """
:root {
  --bg: #ffffff;
  --panel: #ffffff;
  --ink: #111827;
  --muted: #4b5563;
  --line: #e5e7eb;
  --soft: #f9fafb;
  --code: #f3f4f6;
  --accent: #1d4ed8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 17px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
}
main {
  max-width: 1080px;
  margin: 0 auto;
  padding: 40px 22px 80px;
}
header {
  border-bottom: 1px solid var(--line);
  padding-bottom: 18px;
  margin-bottom: 30px;
}
h1, h2, h3, h4 {
  color: var(--ink);
  line-height: 1.2;
  margin: 30px 0 14px;
}
h1 { font-size: clamp(34px, 5vw, 54px); margin-top: 0; }
h2 { font-size: 30px; border-top: 1px solid var(--line); padding-top: 24px; }
h3 { font-size: 23px; }
h4 { font-size: 19px; }
p, li, blockquote { color: var(--muted); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
  background: var(--code);
  color: var(--ink);
  padding: 2px 6px;
  border-radius: 5px;
  font: 0.92em ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
pre {
  background: #111827;
  color: #f9fafb;
  padding: 16px;
  border-radius: 14px;
  overflow-x: auto;
}
pre code {
  background: transparent;
  color: inherit;
  padding: 0;
}
blockquote {
  margin: 18px 0;
  padding: 6px 16px;
  border-left: 4px solid #93c5fd;
  background: #f8fbff;
}
img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 18px auto 24px;
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 10px 28px rgba(17, 24, 39, .08);
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 18px 0 24px;
  border: 1px solid var(--line);
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}
th {
  background: var(--soft);
  color: var(--ink);
}
hr {
  border: 0;
  border-top: 1px solid var(--line);
  margin: 34px 0;
}
.notice {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-left: 5px solid #1d4ed8;
  border-radius: 12px;
  padding: 14px 16px;
  margin: 0 0 24px;
  color: #1e3a8a;
}
.meta {
  color: var(--muted);
  font-size: 14px;
}
"""


IMG_RE = re.compile(r'<img([^>]*?)src="([^"]+)"([^>]*?)>')
HREF_RE = re.compile(r'href="([^"]+)"')


def iter_doc_paths() -> Iterable[Path]:
    for path in DOC_PATHS:
        if path.is_file():
            yield path


def embed_local_images(html_text: str, html_path: Path) -> str:
    def repl(match: re.Match[str]) -> str:
        before, src, after = match.groups()
        if re.match(r"^(https?:|data:|mailto:|#)", src):
            return match.group(0)
        source = (html_path.parent / src).resolve()
        if not source.exists():
            return match.group(0)
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        return f'<img{before}src="data:{mime};base64,{encoded}"{after}>'

    return IMG_RE.sub(repl, html_text)


def rewrite_md_links(html_text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        href = match.group(1)
        if href.startswith(("http://", "https://", "mailto:", "#", "data:")):
            return match.group(0)
        base, frag = (href.split("#", 1) + [""])[:2]
        if base.endswith(".md"):
            base = base[:-3] + ".html"
        new_href = f'{base}#{frag}' if frag else base
        return f'href="{html.escape(new_href, quote=True)}"'

    return HREF_RE.sub(repl, html_text)


def convert(md_path: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["extra", "tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )
    out_path = md_path.with_suffix(".html")
    body = rewrite_md_links(body)
    body = embed_local_images(body, out_path)

    title = md_path.stem.replace("_", " ")
    if md_path.name == "README.md":
        title = "Project Nur README"

    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <h1>{html.escape(md_path.name)}</h1>
  <p class="meta">Auto-generated from <code>{html.escape(str(md_path.relative_to(ROOT)))}</code>. Local images are embedded for offline reading.</p>
</header>
<div class="notice">This HTML export is intended for direct reading. It avoids Markdown viewer issues by embedding local diagram images directly.</div>
{body}
</main>
</body>
</html>
"""
    out_path.write_text(html_text, encoding="utf-8")
    return out_path


def main() -> None:
    for md_path in iter_doc_paths():
        out = convert(md_path)
        print(out.relative_to(ROOT))


if __name__ == "__main__":
    main()
