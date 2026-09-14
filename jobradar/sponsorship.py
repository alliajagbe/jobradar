"""Turning DOL filing history plus posting text into one signal.

Five values, not four. Collapsing "this employer filed zero LCAs" into "we could
not find this employer" loses the distinction that matters most for a forty
person startup on Ashby, where the two mean opposite things. The page renders
them as "Never filed" and "No filing record" and always shows how the employer
was matched, so a fuzzy match can be discounted by the person reading it.

The honest caveat, which belongs in the UI and not only here: LCA filings are an
employer-level historical signal. They are not a promise about any given role.
"""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from functools import lru_cache

from . import config
from .normalize import employer_norm

SIGNALS = ("strong", "some", "never_filed", "unknown", "explicit_no")


@dataclass(frozen=True)
class EmployerStats:
    norm_name: str
    display_name: str
    certified: int
    analyst_certified: int
    denied: int
    last_decision: str


@lru_cache(maxsize=1)
def employer_table() -> dict[str, EmployerStats]:
    """Load data/sponsors.csv.gz. Missing is fine: every company then reads as
    `unknown` and the tool still works, it just scores sponsorship at 5.

    Names are re-normalized from `display_name` at LOAD time rather than trusting
    the `norm_name` written at ingest. Employer matching only works when both
    sides run through the same normalizer, and the file is built by a workflow
    that may have run weeks and several edits ago. Re-normalizing here means a
    change to normalize.employer_norm takes effect immediately on both sides
    instead of waiting for the next quarterly ingest, during which the two would
    silently disagree.

    Re-keying merges corporate variants that now collapse to one name, which is
    the intended outcome: "ANTHROPIC PBC" and "ANTHROPIC INC" are one employer.
    """
    if not config.SPONSORS_CSV_GZ.exists():
        return {}
    merged: dict[str, EmployerStats] = {}
    with gzip.open(config.SPONSORS_CSV_GZ, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = employer_norm(row["display_name"]) or row["norm_name"]
            incoming = EmployerStats(
                norm_name=key,
                display_name=row["display_name"],
                certified=int(row["certified"] or 0),
                analyst_certified=int(row["analyst_certified"] or 0),
                denied=int(row["denied"] or 0),
                last_decision=row.get("last_decision") or "",
            )
            current = merged.get(key)
            if current is None:
                merged[key] = incoming
                continue
            merged[key] = EmployerStats(
                norm_name=key,
                # Keep the spelling of whichever variant files the most.
                display_name=(current.display_name if current.certified >= incoming.certified
                              else incoming.display_name),
                certified=current.certified + incoming.certified,
                analyst_certified=current.analyst_certified + incoming.analyst_certified,
                denied=current.denied + incoming.denied,
                last_decision=max(current.last_decision, incoming.last_decision),
            )
    return merged


@dataclass(frozen=True)
class Signal:
    signal: str
    detail: str
    text_verdict: str
    evidence: str | None
    positive_evidence: str | None
    certified: int | None
    analyst_certified: int | None
    match_method: str
    match_score: int | None

    def as_dict(self) -> dict:
        return {
            "signal": self.signal, "detail": self.detail,
            "text_verdict": self.text_verdict, "evidence": self.evidence,
            "positive_evidence": self.positive_evidence,
            "certified": self.certified, "analyst_certified": self.analyst_certified,
            "match_method": self.match_method, "match_score": self.match_score,
        }


def resolve(
    *,
    text_verdict,
    stats: EmployerStats | None,
    match_method: str,
    match_score: int | None,
    is_us: bool,
) -> Signal:
    """Combine what the posting says with what the employer has filed.

    Posting text outranks filing history in both directions: a company with
    4,000 certified LCAs that says "we cannot sponsor this role" cannot sponsor
    this role.

    An affirmative statement only counts for a US posting. The one real example
    found while building this was Affirm saying "We are able to offer visa
    sponsorship for this role" on a role based in Spain.
    """
    verdict = text_verdict.verdict

    if verdict in ("says_no", "requires_citizenship", "requires_clearance"):
        detail = {
            "says_no": "the posting says no sponsorship",
            "requires_citizenship": "the posting requires US citizenship",
            "requires_clearance": "the posting requires a security clearance",
        }[verdict]
        return Signal("explicit_no", detail, verdict, text_verdict.evidence,
                      text_verdict.positive_evidence, _c(stats), _a(stats),
                      match_method, match_score)

    if verdict == "says_yes" and is_us:
        return Signal("says_yes", "the posting offers sponsorship", verdict,
                      text_verdict.evidence, None, _c(stats), _a(stats),
                      match_method, match_score)

    if stats is None:
        return Signal("unknown", "no filing record found", verdict, None, None,
                      None, None, match_method, match_score)

    if (stats.certified >= config.SPONSOR_STRONG_CERTIFIED
            and stats.analyst_certified >= config.SPONSOR_STRONG_ANALYST_SOC):
        detail = (f"{stats.certified} certified filings, "
                  f"{stats.analyst_certified} in analyst roles")
        return Signal("strong", detail, verdict, None, None, stats.certified,
                      stats.analyst_certified, match_method, match_score)

    if stats.certified >= 1:
        detail = f"{stats.certified} certified filing" + ("s" if stats.certified != 1 else "")
        return Signal("some", detail, verdict, None, None, stats.certified,
                      stats.analyst_certified, match_method, match_score)

    return Signal("never_filed", "no certified filings on record", verdict, None,
                  None, 0, 0, match_method, match_score)


def _c(stats: EmployerStats | None) -> int | None:
    return stats.certified if stats else None


def _a(stats: EmployerStats | None) -> int | None:
    return stats.analyst_certified if stats else None
