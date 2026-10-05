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


# Legal-form and country words that distinguish one filing ENTITY of a company
# from another, not one company from another. Deliberately conservative:
# "Technologies", "Systems" and "Solutions" are left out because they
# distinguish real companies from each other.
_ENTITY_NOISE = frozenset("""
INC INCORPORATED LLC LLP LP PLC PBC CORP CORPORATION CO COMPANY LTD LIMITED
GMBH NV SA AG KK PTE PVT
US USA U S AMERICA AMERICAS
HOLDINGS HOLDING SERVICES SERVICE NATIONAL ASSOCIATION
""".split())


# A stem of one generic word says nothing about a shared parent company.
_GENERIC_STEMS = frozenset("""
CONSULTING TECHNOLOGY TECHNOLOGIES SOLUTIONS SYSTEMS PARTNERS CAPITAL GLOBAL
INTERNATIONAL ENTERPRISES VENTURES MANAGEMENT ANALYTICS DIGITAL LABS LABORATORY
STAFFING RECRUITING SOFTWARE DATA HEALTH HEALTHCARE MEDICAL FINANCIAL INSURANCE
BANK UNIVERSITY COLLEGE SCHOOL DISTRICT HOSPITAL CLINIC FOUNDATION INSTITUTE
RESEARCH ENGINEERING CONSTRUCTION LOGISTICS TRANSPORT RETAIL ENERGY
""".split())


def entity_stem(norm_name: str) -> str:
    """A company name with its entity designations removed.

    "GLOBALFOUNDRIES U S" and "GLOBALFOUNDRIES U S 2" both reduce to
    "GLOBALFOUNDRIES": one employer that files under two entities. Bare digits
    go too, because the sequence number is exactly what separates them.
    """
    kept = [t for t in norm_name.split(" ")
            if t and t not in _ENTITY_NOISE and not t.isdigit()]
    return " ".join(kept)


@dataclass(frozen=True)
class Match:
    norm_name: str | None
    method: str          # exact | alias | fuzzy | none | ambiguous | too-short
                         # | short-name | sibling-entities
    score: int | None
    # Every entity this resolved to. One name usually; several when one employer
    # files under sibling entities, in which case the caller sums their stats.
    # Refusing those was reporting "no filing record" for companies that
    # sponsor heavily: GlobalFoundries files 125 certifications across two
    # entities and read as unknown.
    names: tuple[str, ...] = ()


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
            return Match(norm, "exact", 100, names=(norm,))

        alias = aliases().get(norm)
        if alias and alias in self._exact:
            return Match(alias, "alias", 100, names=(alias,))

        if len(norm.replace(" ", "")) < config.FUZZY_MIN_NAME_LEN:
            # Short names fuzzy-match nearly everything, so they do not go
            # through the scorer. They are not hopeless though: "Ramp" files as
            # "RAMP BUSINESS CORPORATION" and "Calm" as "CALM.COM INC", and
            # plenty of startups have four-letter names. Accept only an exact
            # first-token hit on a short candidate, and label the method
            # `short-name` so the page can show that this one is a weaker
            # inference than an exact match.
            short = [
                name for name in self._by_first.get(norm, ())
                if len(name.split(" ")) <= 2
            ]
            if len(short) == 1:
                return Match(short[0], "short-name", None, names=(short[0],))
            return Match(None, "too-short", None)

        candidates = set(self._by_prefix.get(norm[:6], ()))
        candidates.update(self._by_first.get(norm.split(" ")[0], ()))
        if not candidates:
            return Match(None, "none", None)

        # More than two, so every tied candidate can be inspected rather than
        # just the runner-up.
        ranked = process.extract(
            norm, list(candidates), scorer=fuzz.token_set_ratio, limit=8
        )
        if not ranked:
            return Match(None, "none", None)

        best_name, best_score = ranked[0][0], int(ranked[0][1])
        if best_score < config.FUZZY_ACCEPT:
            return Match(None, "none", best_score)

        tied = [name for name, score, *_ in ranked
                if best_score - int(score) < config.FUZZY_AMBIGUITY_MARGIN]
        if len(tied) > 1:
            # Sibling entities of one employer, or genuinely different
            # companies? If every tied name reduces to the same stem once
            # entity designations are removed, it is one company and their
            # filings belong together.
            stems = {entity_stem(name) for name in tied}
            stem = next(iter(stems)) if len(stems) == 1 else None
            # A stem that is one generic business word is not evidence of a
            # shared parent: "Consulting Services" and "Consulting Holdings"
            # both reduce to CONSULTING and are unrelated firms. 186 employers
            # in the DOL table file under several entities and the generic ones
            # among them are exactly where this would go wrong.
            if stem and len(stem) >= 4 and stem not in _GENERIC_STEMS:
                return Match(best_name, "sibling-entities", best_score,
                             names=tuple(sorted(tied)))
            # Otherwise an honest "no record" beats a confident number
            # attached to the wrong company.
            return Match(None, "ambiguous", best_score)
        return Match(best_name, "fuzzy", best_score, names=(best_name,))
