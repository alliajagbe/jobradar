"""Building the resume HTML.

Hand-built rather than templated, for two reasons. The document has a small
fixed schema, so a template engine buys nothing. And every measurable unit needs
a `data-slot` and a `data-max-lines` attribute, which a template tends to
obscure and which `measure.py` depends on absolutely.

The structural rules here are ATS rules, not style choices:

- The contact line is one unbroken text node. Wrapping the email in a span lets
  the text shaper split the run, and twenty resumes already sent out extract
  their email as "allia jagbe001@gmail.com" because of exactly that.
- Bullet markers come from CSS `::before`, which DOES put them in the PDF text
  layer, but puts them there uniformly. The original's bug was not that glyphs
  were present, it was that two of five Skills rows extracted without one
  because those rows were drawn from a different font.
- DOM order is reading order. No grid, no columns, no absolute positioning for
  anything whose text has to come out in sequence.
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path

THEME = Path(__file__).parent / "theme"


def tokens(overrides: dict | None = None) -> dict:
    data = json.loads((THEME / "tokens.json").read_text(encoding="utf-8"))
    if overrides:
        data.update(overrides)
    return data


def _font_faces(t: dict) -> str:
    """Embed the base resume's own typeface.

    Computer Modern is not installed on macOS and cannot be reproduced without
    the font files, so CMU Serif (the open Unicode cut of the same design, SIL
    OFL) is kept in ~/.jobradar/resume/fonts and inlined here as data URIs.
    Inlined rather than linked because Chromium loads the page from a string
    with no base URL, so a relative font path has nothing to resolve against.

    TrueType rather than the original OpenType, because Chromium embeds a
    CFF-flavoured face as a Type 3 font. Type 3 is procedural, and it split
    "Alli Ajagbe" across two lines in the extracted text. The outlines are
    converted once by scripts/build_fonts.py.
    """
    import base64
    from .paths import resolve
    faces = []
    for key, filename in (t.get("font_files") or {}).items():
        path = resolve("resume/fonts", filename)
        if not path.exists():
            continue
        weight = key[:3]
        style = "italic" if "italic" in key else "normal"
        b64 = base64.b64encode(path.read_bytes()).decode()
        faces.append(
            "@font-face{font-family:'CMU Serif';"
            f"font-weight:{weight};font-style:{style};font-display:block;"
            f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}")
    return "".join(faces)


def _css_vars(t: dict) -> str:
    return (
        f"--font-stack:{t['font_stack']};"
        f"--body:{t['body_pt']}pt;"
        f"--name:{t['name_pt']}pt;"
        f"--section:{t['section_pt']}pt;"
        f"--margin:{t['margin_in']}in;"
        f"--leading:{t['leading']};"
        f"--indent:{t['indent_in']}in;"
        f"--section-gap:{t['section_gap_em']}em;"
        f"--entry-gap:{t['entry_gap_em']}em;"
    )


def _e(text: str) -> str:
    return _html.escape(str(text), quote=False)


def _bullet(slot: str, text: str, max_lines: int = 2) -> str:
    return (f'<li data-slot="{slot}" data-max-lines="{max_lines}">'
            f'{_e(text)}</li>')


def build(doc: dict, *, overrides: dict | None = None,
          inline_honors: bool = False, drop_honors: bool = False) -> str:
    """Render a resolved document to a complete HTML page."""
    t = tokens(overrides)
    css = (THEME / "resume.css").read_text(encoding="utf-8")
    ident = doc["identity"]

    contact = " | ".join(
        [ident["phone"], ident["email"]] + [l["label"] for l in ident["links"]]
    )

    parts: list[str] = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<style>{_font_faces(t)}\n:root{{{_css_vars(t)}}}\n{css}</style></head><body>",
        f'<div class="name">{_e(ident["name"])}</div>',
        f'<div class="contact" data-slot="contact" data-max-lines="1">{_e(contact)}</div>',
    ]

    parts.append('<section><h2>Professional Summary</h2>')
    parts.append(f'<div data-slot="summary" data-max-lines="3">{_e(doc["summary"])}</div>')
    parts.append('</section>')

    parts.append('<section><h2>Education</h2><div class="edu-table">')
    for edu in doc["education"]:
        honors = edu.get("honors") or ""
        grade = str(edu.get("grade") or "")
        # Score bold over a plain scale: a high GPA should read at a glance.
        if "/" in grade:
            top, _, scale = grade.partition("/")
            grade_html = f'<strong>{_e(top)}</strong>/{_e(scale)}'
        else:
            grade_html = _e(grade)
        degree_cell = _e(edu["degree"])
        if inline_honors and honors and not drop_honors:
            degree_cell += f' <em>{_e(honors)}</em>'
        # Cells are direct children of ONE grid spanning the whole section, so
        # the columns share widths down the rows. A grid per row lets each row
        # size its own columns, and the schools and grades then wander.
        parts.append(
            f'<span class="deg" data-slot="{edu["id"]}" data-max-lines="1">'
            f'{degree_cell}</span>'
            f'<span class="sch"><strong>{_e(edu["school"])}</strong> | '
            f'<strong><em>{_e(edu["location"])}</em></strong></span>'
            f'<span class="gpa">{grade_html}</span>'
            f'<span class="when">{_e(edu["dates"])}</span>')
        if honors and not drop_honors and not inline_honors:
            parts.append(
                f'<div class="honors-row"><ul class="honors">'
                f'<li data-slot="{edu["id"]}.honors" data-max-lines="2">'
                f'<strong><em>Honors:</em></strong> <em>{_e(honors)}</em>'
                f'</li></ul></div>')
    parts.append('</div></section>')

    parts.append('<section><h2>Skills</h2><ul class="skills">')
    for group in doc["skills"]:
        items = ", ".join(group["items"])
        parts.append(f'<li data-slot="{group["id"]}" data-max-lines="1">'
                     f'<strong>{_e(group["name"])}:</strong> {_e(items)}</li>')
    parts.append('</ul></section>')

    parts.append('<section><h2>Experience</h2>')
    for role in doc["experience"]:
        parts.append(
            f'<div class="entry"><div class="entry-head">'
            f'<span class="left"><strong>{_e(role["employer"])}</strong> | '
            f'<strong>{_e(role["title"])}</strong> | '
            f'<strong><em>{_e(role["location"])}</em></strong></span>'
            f'<span class="right"><strong>{_e(role["dates"])}</strong></span>'
            f'</div><ul>')
        for b in role["bullets"]:
            parts.append(_bullet(b["id"], b["text"], 2))
        parts.append('</ul></div>')
    parts.append('</section>')

    parts.append('<section><h2>Projects</h2>')
    for proj in doc["projects"]:
        parts.append(
            f'<div class="entry proj"><div class="entry-head">'
            f'<span class="left"><strong>{_e(proj["name"])}</strong> | '
            f'<strong>{_e(proj["role"])}</strong></span>'
            f'<span class="right"><strong>{_e(proj["dates"])}</strong></span>'
            f'</div><ul>')
        parts.append(_bullet(proj["bullet"]["id"], proj["bullet"]["text"], 2))
        parts.append('</ul></div>')
    parts.append('</section>')

    parts.append('</body></html>')
    return "\n".join(parts)
