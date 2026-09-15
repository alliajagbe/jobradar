"""Fetch and prepare Computer Modern for the resume renderer.

Usage:
  .venv/bin/python scripts/build_fonts.py [--apply]

The base resume was set in Computer Modern by LaTeX. macOS does not ship it and
no TeX distribution is installed here, so the renderer uses CMU Serif, the open
Unicode cut of the same design (SIL OFL), fetched from CTAN.

The OTF files are then converted to TrueType outlines. That step is not
cosmetic: Chromium embeds a CFF-flavoured OpenType face as a Type 3 font, which
is procedural, and a Type 3 embedding split "Alli Ajagbe" across two lines in
the extracted text. Converting the outlines yields a normal CID TrueType
embedding, which is also what the resumes produced in August used.

The fonts land in ~/.jobradar/resume/fonts, outside the repository, because
they are large binaries derived from a public source rather than project code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CTAN = "https://mirrors.ctan.org/fonts/cm-unicode/fonts/otf"
FACES = {
    "cmunrm": "regular",
    "cmunbx": "bold",
    "cmunti": "italic",
    "cmunbi": "bold italic",
}


def convert(src: Path, dst: Path, tolerance: float = 1.0) -> None:
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont, newTable

    font = TTFont(str(src))
    glyph_set = font.getGlyphSet()
    glyf, hmtx = {}, {}
    for name in font.getGlyphOrder():
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, tolerance, reverse_direction=True))
        glyf[name] = pen.glyph()
        hmtx[name] = font["hmtx"][name]
    for tag in ("CFF ", "VORG"):
        if tag in font:
            del font[tag]
    font["loca"] = newTable("loca")
    font["glyf"] = newTable("glyf")
    font["glyf"].glyphOrder = font.getGlyphOrder()
    font["glyf"].glyphs = glyf
    font["hmtx"].metrics = hmtx
    font["maxp"].numGlyphs = len(glyf)
    font.sfntVersion = "\x00\x01\x00\x00"
    font["head"].indexToLocFormat = 0
    font.save(str(dst))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="download and convert")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jobradar.resume.paths import resolve

    out = resolve("resume/fonts", create_parent=True)
    if not args.apply:
        print(f"would fetch {len(FACES)} CMU Serif faces from CTAN into {out}")
        for stem, label in FACES.items():
            print(f"  {stem}.otf -> {stem}.ttf   ({label})")
        print("\nDry run. Pass --apply.")
        return 0

    import httpx
    out.mkdir(parents=True, exist_ok=True)
    for stem, label in FACES.items():
        otf, ttf = out / f"{stem}.otf", out / f"{stem}.ttf"
        if not otf.exists():
            response = httpx.get(f"{CTAN}/{stem}.otf", timeout=90, follow_redirects=True)
            response.raise_for_status()
            otf.write_bytes(response.content)
        convert(otf, ttf)
        print(f"  {ttf.name:12} {ttf.stat().st_size:>7} bytes  ({label})")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
