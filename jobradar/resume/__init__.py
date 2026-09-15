"""Tailored resume generation.

Three steps, because the rewriting is done by a person (or by Claude Code in
session) rather than by an API call:

  1. `brief`    gathers the full job description and the gap analysis
  2. a human writes a variant naming which master bullet each line came from
  3. `render`   validates, measures, fits to one page and prints the PDF

The measuring is the part that justifies the architecture. Two of the rules the
user has been enforcing by hand are about RENDERED LINES, not characters: the
summary must be exactly three lines and no experience bullet may exceed two. A
character count cannot check those, so the renderer has to be something that can
report real line boxes. See `measure.py`.
"""

from __future__ import annotations

from .paths import ResumeError

__all__ = ["ResumeError"]
