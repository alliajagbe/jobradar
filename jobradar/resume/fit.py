"""Fitting one page, and refusing to cheat while doing it.

The ladder never changes the font size. Shrinking type until it fits is exactly
the failure this project exists to undo, and it is invisible in a PDF: the
document looks fine and reads worse.

Order matters and encodes a judgement. Spacing first, because whitespace is the
cheapest thing to spend. Then the education honors, because "Beta Gamma Sigma,
Wiseman Scholar, Outstanding Graduate Ambassador" costs the same two lines as a
real employment entry and the user's own base resume omits honors entirely.
Only then SBSF Agritech, which the user named as the single droppable role.

Past that the tool stops and reports rather than choosing. Everything remaining
is protected by an explicit instruction, so a fitter that silently dropped a
project to make room would be answering a question nobody asked.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from . import html as H
from .measure import Metrics, render


@dataclass
class FitResult:
    document: dict
    overrides: dict
    metrics: Metrics
    applied: list[str]
    fits: bool
    inline_honors: bool = False
    drop_honors: bool = False


def _strip_bullet(doc: dict, bullet_id: str) -> dict:
    out = copy.deepcopy(doc)
    for role in out["experience"]:
        role["bullets"] = [b for b in role["bullets"] if b["id"] != bullet_id]
    return out


def _strip_role(doc: dict, role_id: str) -> dict:
    out = copy.deepcopy(doc)
    out["experience"] = [r for r in out["experience"] if r["id"] != role_id]
    return out


def fit(doc: dict, *, font_probe: str, progress=None) -> FitResult:
    base = H.tokens()
    steps = base["fit_steps"]
    overrides: dict = {}
    applied: list[str] = []
    inline_honors = drop_honors = False
    current = doc

    metrics = render(H.build(current, overrides=overrides), None, font_probe=font_probe)
    if metrics.fits_one_page:
        return FitResult(current, overrides, metrics, applied, True)

    for step in steps:
        kind = step["kind"]
        if kind == "tighten":
            overrides.update({k: v for k, v in step.items()
                              if k not in ("kind", "label", "target")})
        elif kind == "inline_honors":
            inline_honors = True
        elif kind == "drop_honors":
            drop_honors = True
        elif kind == "drop_bullet":
            current = _strip_bullet(current, step["target"])
        elif kind == "drop_role":
            current = _strip_role(current, step["target"])
        applied.append(step["label"])
        if progress:
            progress(f"  fit: {step['label']}")
        metrics = render(
            H.build(current, overrides=overrides, inline_honors=inline_honors,
                    drop_honors=drop_honors),
            None, font_probe=font_probe)
        if metrics.fits_one_page:
            return FitResult(current, overrides, metrics, applied, True,
                             inline_honors, drop_honors)

    return FitResult(current, overrides, metrics, applied, False,
                     inline_honors, drop_honors)


def longest_slots(doc: dict, metrics: Metrics, n: int = 5) -> list[tuple[str, int, int]]:
    """Where to cut, when the ladder runs out. (slot, chars, approx chars to cut)."""
    texts: dict[str, str] = {"summary": doc["summary"]}
    for role in doc["experience"]:
        for b in role["bullets"]:
            texts[b["id"]] = b["text"]
    for proj in doc["projects"]:
        texts[proj["bullet"]["id"]] = proj["bullet"]["text"]
    rows = []
    for slot, m in metrics.slots.items():
        text = texts.get(slot)
        if not text or m["lines"] <= 1:
            continue
        per_line = len(text) / m["lines"]
        rows.append((slot, len(text), int(per_line * m["last_frac"]) + 6))
    rows.sort(key=lambda r: -r[1])
    return rows[:n]
