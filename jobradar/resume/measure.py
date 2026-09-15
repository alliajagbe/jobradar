"""Rendering, and measuring what was rendered.

The only module that knows Playwright exists.

Measurement is the reason this project renders in a browser at all. Two of the
rules are expressed in RENDERED LINES, not characters: the summary must be
exactly three lines, and no experience bullet may exceed two. Character counts
cannot check either, because wrapping depends on the font, the measure and the
exact words. A browser reports real line boxes, so the rule the user calls "the
single most repeated correction" becomes a mechanical check.

Line counting uses Range.getClientRects rather than height/lineHeight, because
an inline <strong> of a different size breaks the division, and because the
rects give the width of the final line for free. That width is what powers both
the stub-line check and the "cut roughly N characters" hint.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import ResumeError

# One page at 96 CSS pixels per inch. The guard absorbs sub-pixel rounding;
# without it a document that prints on one page fails by a third of a pixel.
PAGE_PX = 1056
PAGE_GUARD = 4

_MEASURE_JS = """() => {
  const out = {slots: {}, lefts: [], page: {}};
  const merge = (tops) => {
    const m = [];
    for (const t of tops) if (!m.length || t - m[m.length - 1] > 1.5) m.push(t);
    return m;
  };
  for (const el of document.querySelectorAll('[data-slot]')) {
    const r = document.createRange();
    r.selectNodeContents(el);
    const rects = [...r.getClientRects()].filter(x => x.width > 0.5 && x.height > 0.5);
    if (!rects.length) { out.slots[el.dataset.slot] = {lines: 0, last_frac: 0, max: +el.dataset.maxLines}; continue; }
    const tops = merge([...new Set(rects.map(x => Math.round(x.top * 2) / 2))].sort((a, b) => a - b));
    const lastTop = tops[tops.length - 1];
    const lastW = rects.filter(x => Math.abs(x.top - lastTop) <= 1.5)
                       .reduce((s, x) => s + x.width, 0);
    const measure = el.getBoundingClientRect().width || 1;
    out.slots[el.dataset.slot] = {
      lines: tops.length,
      max: +el.dataset.maxLines,
      last_frac: +(lastW / measure).toFixed(3),
      width_px: +measure.toFixed(1)
    };
  }
  // Distinct left edges of bullet text, which is the mechanical form of the
  // "five indent levels" complaint.
  const lefts = new Set();
  for (const li of document.querySelectorAll('li')) {
    const r = document.createRange(); r.selectNodeContents(li);
    for (const rect of r.getClientRects()) if (rect.width > 0.5) { lefts.add(Math.round(rect.left)); break; }
  }
  out.lefts = [...lefts].sort((a, b) => a - b);
  out.page.height = document.documentElement.scrollHeight;
  out.page.font_ok = document.fonts.check(FONT_PROBE);
  return out;
}"""


@dataclass(frozen=True)
class Metrics:
    slots: dict
    lefts: list
    height_px: int
    font_ok: bool
    pages: int | None = None

    @property
    def fits_one_page(self) -> bool:
        return self.height_px <= PAGE_PX + PAGE_GUARD

    def overflow_px(self) -> int:
        return max(0, self.height_px - PAGE_PX)


def render(html: str, pdf_path: Path | None, *, font_probe: str) -> Metrics:
    """Lay out the HTML, measure it, and optionally print it to PDF."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                                  # pragma: no cover
        raise ResumeError(
            "playwright is not installed. Run: .venv/bin/pip install -r "
            "requirements-resume.txt"
        ) from exc

    js = _MEASURE_JS.replace("FONT_PROBE", repr(font_probe))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # A fixed viewport and scale factor: the measurements are only
            # comparable between runs if the layout viewport is identical.
            page = browser.new_page(viewport={"width": 816, "height": PAGE_PX},
                                    device_scale_factor=1)
            page.set_content(html, wait_until="load")
            page.evaluate("async () => { await document.fonts.ready; }")
            raw = page.evaluate(js)
            if pdf_path is not None:
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                page.pdf(path=str(pdf_path), format="Letter", print_background=True,
                         margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            browser.close()
    except Exception as exc:                                    # noqa: BLE001
        raise ResumeError(f"render failed: {exc}") from exc

    pages = _page_count(pdf_path) if pdf_path else None
    return Metrics(slots=raw["slots"], lefts=raw["lefts"],
                   height_px=int(raw["page"]["height"]),
                   font_ok=bool(raw["page"]["font_ok"]), pages=pages)


def _page_count(path: Path) -> int | None:
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    return None


def extract_text(path: Path, *, layout: bool = False) -> str:
    """pdftotext the result. Used by the ATS round-trip checks, which exist
    because twenty already-submitted resumes have an email that no regex
    matches, and nothing in the build caught it."""
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + [str(path), "-"]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResumeError(f"pdftotext failed: {exc}") from exc
