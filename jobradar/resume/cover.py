"""Cover letters, set in the same type as the resume.

Deliberately not a second theme. It imports the resume's font faces, tokens and
stylesheet and adds only what a letter needs that a resume does not: a date, an
address block, a salutation and a signature. If the resume's measure or leading
changes, the letter follows, because a candidate who turns up with two documents
in two typefaces looks like two people.

The content rules are the resume's rules. No em-dashes. No sponsorship or
"seeking opportunities" language. No number or tool that is not already on the
master, because a letter is as easy to check against a resume as a resume is to
check against itself, and a figure that appears in one and not the other is
worse than no figure.
"""

from __future__ import annotations

import html as _html
from datetime import date

from .html import THEME, _css_vars, _font_faces, tokens


def _e(text: str) -> str:
    return _html.escape(str(text), quote=False)


COVER_CSS = """
.letter { max-width: 100%; }
.letter .meta { margin: 1.6em 0 1.4em; }
.letter .meta div { margin-bottom: 0.1em; }
.letter p {
  margin: 0 0 0.85em;
  /* Justified like the resume summary, which is the only other real paragraph
     in the pair. Left-aligned here would read as a different document. */
  text-align: justify;
  text-justify: inter-word;
  hyphens: none;
}
.letter .salutation { margin-bottom: 0.9em; }
.letter .signoff { margin-top: 1.4em; }
.letter .signature {
  margin-top: 0.25em; font-weight: 700;
  font-size: var(--body); text-align: left;
}
.rule { border-bottom: 0.5pt solid #000; margin: 0.35em 0 0; }
"""


def build(identity: dict, letter: dict, *, overrides: dict | None = None) -> str:
    """Render a letter. `letter` carries company, role, salutation, paragraphs."""
    t = tokens(overrides)
    css = (THEME / "resume.css").read_text(encoding="utf-8")
    contact = " | ".join(
        [identity["phone"], identity["email"]] + [l["label"] for l in identity["links"]]
    )
    when = letter.get("date") or date.today().strftime("%d %B %Y").lstrip("0")

    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f"<style>{_font_faces(t)}\n:root{{{_css_vars(t)}}}\n{css}\n{COVER_CSS}</style>",
        "</head><body><div class=\"letter\">",
        f'<div class="name">{_e(identity["name"])}</div>',
        f'<div class="contact" data-slot="contact" data-max-lines="1">{_e(contact)}</div>',
        '<div class="rule"></div>',
        '<div class="meta">',
        f'<div>{_e(when)}</div>',
    ]
    for line in letter.get("address") or []:
        parts.append(f"<div>{_e(line)}</div>")
    parts.append("</div>")

    parts.append(f'<p class="salutation">{_e(letter.get("salutation") or "Dear Hiring Team,")}</p>')
    for i, para in enumerate(letter["paragraphs"]):
        text = " ".join(str(para).split())
        parts.append(f'<p data-slot="para{i + 1}" data-max-lines="9">{_e(text)}</p>')

    parts.append('<div class="signoff">')
    parts.append(f'<div>{_e(letter.get("signoff") or "Sincerely,")}</div>')
    # NOT .name: that class is the document header, centred at 18pt, and the
    # signature inherited it as a second giant title halfway down the page.
    parts.append(f'<div class="signature">{_e(identity["name"])}</div>')
    parts.append("</div></div></body></html>")
    return "\n".join(parts)
