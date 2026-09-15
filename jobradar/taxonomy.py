"""The gates: which titles count, which seniorities are out, which postings a
visa holder should not waste an application on.

This module decides what the whole tool is worth. Everything downstream is
presentation. Two patterns here are load-bearing enough to have their own tests:

- The `lead` seniority pattern. A naive `\\blead\\b` drops "Lead Generation
  Analyst", which is an entry-level marketing analytics role, and you would not
  notice for weeks because the job simply never appears.

- The no-sponsorship pattern. The single most common sentence on any US job
  posting is "Applicants must be authorized to work in the United States."
  That is NOT a disqualifier: someone on OPT is authorized to work. Only the
  longer form disqualifies, the one that adds "without sponsorship" or "now or
  in the future". If a future edit ever simplifies this regex to
  `authorized to work`, the pipeline silently drops most of its input and the
  page just looks empty. Measured against 1,355 real postings from four boards,
  the affirmative and negative pattern sets matched 18 and 15 postings
  respectively with zero overlap.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config

# ---- Titles ---- #


@dataclass(frozen=True)
class TitleMatch:
    tier: str | None       # A | A-partial | B | C | N (negative) | None
    term: str | None


_ANALYTICS_WORD = re.compile(
    r"\b(analyst|analytics|analysis|data|business intelligence|bi|insights|"
    r"reporting|quantitative|statistic)\w*\b", re.I
)


@lru_cache(maxsize=1)
def _title_terms() -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"A": [], "B": [], "C": [], "N": []}
    with config.TITLES_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            buckets[row["tier"].strip()].append(row["term"].strip().lower())
    return buckets


@lru_cache(maxsize=1)
def _negative_patterns() -> tuple[re.Pattern[str], ...]:
    """Negative terms, matched with words allowed in between.

    A plain substring test misses the way these titles are actually written:
    "security analyst" does not appear in "Security Operations Analyst", and
    eight infosec roles reached the results because of it. Any negative term
    ending in a role noun becomes `<domain> ...up to three words... <noun>`.

    This is only safe because the negative list holds different PROFESSIONS
    rather than different industries. Alli is industry agnostic, so credit,
    claims and fraud analytics are analytics jobs and are not in here; infosec,
    software QA and wet-lab work are not analytics jobs and are.
    """
    nouns = ("analyst", "analysts", "administrator", "engineer", "specialist")
    out: list[re.Pattern[str]] = []
    for term in _title_terms()["N"]:
        words = term.split()
        if len(words) >= 2 and words[-1] in nouns:
            head = r"\s+".join(re.escape(w) for w in words[:-1])
            out.append(re.compile(
                rf"(?<![a-z]){head}\b[\w\s/&,-]{{0,26}}?\b{re.escape(words[-1])}(?![a-z])"
            ))
        else:
            out.append(re.compile(rf"(?<![a-z]){re.escape(term)}(?![a-z])"))
    return tuple(out)


def title_tier(title_norm_value: str) -> TitleMatch:
    """Classify a normalized title.

    Negative terms are checked first and win outright: "Security Analyst"
    contains "analyst" but is not a role this tool is for.
    """
    terms = _title_terms()
    for pattern in _negative_patterns():
        hit = pattern.search(title_norm_value)
        if hit:
            return TitleMatch("N", hit.group(0))

    for term in terms["A"]:
        if term in title_norm_value:
            # Exact, or exact plus a department qualifier after a separator.
            stripped = re.sub(r"[,\-–|/(].*$", "", title_norm_value).strip()
            tier = "A" if stripped == term or title_norm_value == term else "A-partial"
            return TitleMatch(tier, term)

    for term in terms["C"]:
        if term in title_norm_value and _ANALYTICS_WORD.search(title_norm_value):
            return TitleMatch("C", term)

    for term in terms["B"]:
        if term in title_norm_value:
            return TitleMatch("B", term)

    return TitleMatch(None, None)


# ---- Seniority ---- #

_SENIORITY = re.compile(
    r"\b(senior|sr\.?|staff|principal|director|head\s+of|vp|vice\s+president|"
    r"chief|distinguished|fellow|manager|mgr|architect|president|executive)\b",
    re.I,
)

# `lead` gets its own pattern because the generic one is wrong. Reject a title
# that LEADS with "lead", or "lead <role noun>". Never reject "lead generation"
# (a marketing function), "leadership", or "team lead" buried in a longer title.
_LEAD = re.compile(
    r"^lead\b(?!\s+generation)|"
    r"\blead\s+(analyst|data|business|scientist|engineer|developer)\b|"
    # Lead as a trailing noun: "Analytics Lead", "Reporting Lead". These are
    # leadership roles wearing an analytics title. Kept separate from the
    # leading-"lead" branch so "Lead Generation Analyst" stays in scope.
    r"\b(analytics|analysis|data|bi|reporting|insights|science)\s+lead\b",
    re.I,
)

_INTERN = re.compile(r"\b(intern|internship|co-?op|apprentice|trainee)\b", re.I)


def seniority_reject(title_norm_value: str) -> str | None:
    """Return the offending term, or None. Title only, never the description:
    "you will work with senior analysts" does not make it a senior role."""
    m = _SENIORITY.search(title_norm_value)
    if m:
        return m.group(1).lower()
    m = _LEAD.search(title_norm_value)
    if m:
        return "lead"
    m = _INTERN.search(title_norm_value)
    if m:
        return m.group(1).lower()
    return None


# ---- Sponsorship language ---- #

_CITIZENSHIP = [
    ("citizenship", re.compile(
        r"must be a (?:u\.?s\.?|united states) citizen|"
        r"(?:u\.?s\.?|united states) citizenship (?:is )?(?:required|mandatory)|"
        r"citizenship (?:is )?(?:a )?requirement|"
        r"requires? (?:u\.?s\.?|united states|american )?\s*citizenship|"
        r"must (?:be|hold).{0,30}(?:u\.?s\.?|american) citizen|"
        r"u\.?s\.? persons? only|"
        r"restricted to (?:u\.?s\.? )?citizens|"
        r"(?:itar|export control).{0,60}(?:citizen|u\.?s\.? person)", re.I)),
]

# Only a REQUIRED clearance disqualifies. Real Databricks postings say
# "candidates with an active Secret clearance are strongly encouraged to apply",
# which is a preference, and dropping those loses good roles.
_CLEARANCE = [
    ("clearance", re.compile(
        r"(?:active |current )?(?:security|government) clearance.{0,40}"
        r"(?:is )?(?:required|mandatory|must)|"
        r"(?:must|required to) (?:possess|hold|have).{0,40}clearance|"
        r"\b(?:ts/sci|top secret|secret clearance)\b.{0,40}(?:required|mandatory)",
        re.I)),
]

_NO_SPONSOR = [
    ("no_sponsorship", re.compile(
        r"(?:will |can )?not (?:be able to |be willing to )?sponsor|"
        r"unable to (?:provide |offer )?sponsor|"
        r"do(?:es)? not (?:provide|offer|support)\s+(?:visa|immigration|work)?\s*sponsor|"
        r"no (?:visa |work |immigration )?sponsorship|"
        r"sponsorship (?:is )?(?:not |un)available|"
        r"not eligible for (?:visa |immigration )?sponsorship|"
        r"does not participate in (?:the )?h-?1b", re.I)),
    # The careful one. See the module docstring. The "without sponsorship" or
    # "now or in the future" clause is REQUIRED; without it this matches the
    # ordinary "must be authorized to work in the United States" that appears
    # on nearly every US posting and is not a disqualifier for an OPT holder.
    ("authorized_without_sponsorship", re.compile(
        r"authoriz\w+ to work.{0,80}?"
        r"(?:without (?:the need for |requiring )?sponsorship|"
        r"now or in the future|"
        r"currently or in the future)", re.I)),
]

_AFFIRMATIVE = [
    ("sponsors", re.compile(
        r"(?:we|will|do) (?:are able to |can )?sponsor|"
        r"(?:visa |work )?sponsorship (?:is )?(?:available|offered|provided)|"
        r"(?:able|happy|willing) to (?:offer|provide|support).{0,40}sponsorship|"
        r"h-?1b (?:sponsorship|transfer)s?|"
        r"cap-?exempt|"
        r"open to (?:opt|cpt|stem opt)|"
        r"we support (?:visa|work) sponsorship", re.I)),
]


@dataclass(frozen=True)
class TextVerdict:
    verdict: str            # says_yes | says_no | requires_citizenship | requires_clearance | silent
    pattern: str | None
    evidence: str | None    # the matched sentence, verbatim, for the UI
    positive_evidence: str | None = None   # set when signals are mixed


def sponsorship_text_verdict(sentence_list: list[str]) -> TextVerdict:
    """Read a description for what it says about sponsorship.

    Affirmative and negative are both collected. When both appear, negative
    wins but both sentences are kept so the page can show "Mixed signals" and
    let a human decide. Real postings do carry a boilerplate no-sponsorship
    footer under a role-specific yes.
    """
    positive: tuple[str, str] | None = None
    negative: tuple[str, str, str] | None = None

    for sentence in sentence_list:
        if positive is None:
            for name, rx in _AFFIRMATIVE:
                if rx.search(sentence):
                    positive = (name, sentence)
                    break
        if negative is None:
            for verdict, group in (
                ("requires_citizenship", _CITIZENSHIP),
                ("requires_clearance", _CLEARANCE),
                ("says_no", _NO_SPONSOR),
            ):
                for name, rx in group:
                    if rx.search(sentence):
                        negative = (verdict, name, sentence)
                        break
                if negative:
                    break

    if negative:
        return TextVerdict(negative[0], negative[1], negative[2],
                           positive_evidence=positive[1] if positive else None)
    if positive:
        return TextVerdict("says_yes", positive[0], positive[1])
    return TextVerdict("silent", None, None)


# ---- Years of experience ---- #

_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*(\d{1,2})\s*)?(?:years?|yrs?)", re.I)


def min_years_required(text: str) -> int | None:
    """Lowest "N years" mentioned near the word experience.

    The minimum, not the maximum: a posting saying "3+ years of SQL, 5+ years
    preferred" is a three-year role. Matches far from "experience" are ignored,
    which keeps "the last 10 years of transaction data" out of the result.
    """
    best: int | None = None
    low = text.lower()
    for m in _YEARS.finditer(low):
        window = low[max(0, m.start() - 80): m.end() + 80]
        if "experience" not in window:
            continue
        if re.search(r"years? of (education|schooling)", window):
            continue
        value = int(m.group(1))
        if value > 20:
            continue
        best = value if best is None else min(best, value)
    return best


# ---- Skills ---- #


@dataclass(frozen=True)
class Skill:
    canonical: str
    pattern: re.Pattern[str]
    weight: int


@lru_cache(maxsize=1)
def skills() -> tuple[Skill, ...]:
    out: list[Skill] = []
    with config.SKILLS_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            aliases = [a.strip() for a in row["aliases"].split("|") if a.strip()]
            # Word boundaries fail on tokens ending in punctuation like "c++"
            # or containing "/" like "a/b test", so escape and bound manually.
            parts = [rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])" for a in aliases]
            out.append(Skill(
                canonical=row["canonical"].strip(),
                pattern=re.compile("|".join(parts), re.I),
                weight=int(row["weight"]),
            ))
    return tuple(out)


def match_skills(text: str) -> tuple[list[str], int]:
    """Return (matched canonical names, summed weight)."""
    matched: list[str] = []
    total = 0
    for skill in skills():
        if skill.pattern.search(text):
            matched.append(skill.canonical)
            total += skill.weight
    return matched, total
