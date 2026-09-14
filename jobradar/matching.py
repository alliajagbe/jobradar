"""Matching an ATS company name to a Department of Labor employer name.

The hardest correctness problem in the project, and the one where being wrong is
worse than being silent: a confident "412 certified filings" beside a company
that has never sponsored anyone will cost real applications.

Four passes, first hit wins, and every result records HOW it was reached so the
page can show "matched by fuzzy, 89" and let the reader discount it.

The two refusal rules matter more than the acceptance rule:

- If the top two candidates are within a few points of each other, refuse. A
  company scoring 94 against "ACME SOLUTIONS LLC" and 93 against "ACME SOLUTIONS
  GROUP" is a company we cannot place.
- Refuse very short normalized names outright. They fuzzy-match nearly
  everything in a file of 600,000 employers.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from . import config
from .normalize import employer_norm


@dataclass(frozen=True)
class Match:
    norm_name: str | None
    method: str          # exact | alias | fuzzy | none | ambiguous | too-short
    score: int | None


@lru_cache(maxsize=1)
def aliases() -> dict[str, str]:
    """Hand-written corrections, checked before fuzzy so a human decision is
    never overridden by a later scoring change."""
    if not config.ALIASES_CSV.exists():
        return {}
    out: dict[str, str] = {}
    with config.ALIASES_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[employer_norm(row["company"])] = employer_norm(row["employer"])
    return out


class Matcher:
    """Built once per run over the employer table, then queried per company.

    Direction matters: there are a few hundred companies and hundreds of
    thousands of employers, so we iterate companies and pull candidates. The
    reverse is minutes per company even with rapidfuzz.
    """

    def __init__(self, employer_norm_names: list[str]) -> None:
        self._names = employer_norm_names
        self._exact = set(employer_norm_names)
        # Blocking keys: a six-character prefix and the full first token. The
        # union of the two candidate sets is typically tens to hundreds of
        # names instead of the whole file.
        self._by_prefix: dict[str, list[str]] = {}
        self._by_first: dict[str, list[str]] = {}
        for name in employer_norm_names:
            self._by_prefix.setdefault(name[:6], []).append(name)
            self._by_first.setdefault(name.split(" ")[0], []).append(name)

    def match(self, company_name: str) -> Match:
        norm = employer_norm(company_name)
        if not norm:
            return Match(None, "none", None)

        if norm in self._exact:
            return Match(norm, "exact", 100)

        alias = aliases().get(norm)
        if alias and alias in self._exact:
            return Match(alias, "alias", 100)

        if len(norm.replace(" ", "")) < config.FUZZY_MIN_NAME_LEN:
            return Match(None, "too-short", None)

        candidates = set(self._by_prefix.get(norm[:6], ()))
        candidates.update(self._by_first.get(norm.split(" ")[0], ()))
        if not candidates:
            return Match(None, "none", None)

        ranked = process.extract(
            norm, list(candidates), scorer=fuzz.token_set_ratio, limit=2
        )
        if not ranked:
            return Match(None, "none", None)

        best_name, best_score = ranked[0][0], int(ranked[0][1])
        if best_score < config.FUZZY_ACCEPT:
            return Match(None, "none", best_score)
        if len(ranked) > 1:
            runner_up = int(ranked[1][1])
            if best_score - runner_up < config.FUZZY_AMBIGUITY_MARGIN:
                # Two plausible employers. An honest "no record" beats a
                # confident number attached to the wrong company.
                return Match(None, "ambiguous", best_score)
        return Match(best_name, "fuzzy", best_score)
