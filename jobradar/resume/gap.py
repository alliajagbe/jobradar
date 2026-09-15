"""ATS gap analysis: what the posting asks for, against what is true.

Four buckets, and the fourth is the one that matters most. A term the posting
asks for that sits on the never-add list must be surfaced to the writer as
explicitly forbidden, not quietly omitted, because the natural instinct on
seeing "Airflow" five times in a JD is to add Airflow.

The skills lexicon here is the MASTER's pools, not seeds/skills.csv. That file
is a job-matching lexicon and it contains Spark, SAS, Redshift and Airflow, all
of which are on the never-add list. Using it here would recommend fabrications.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Master
from .rules import NEVER_ADD

# Posting boilerplate and sentence-initial words. Without this the "missing"
# bucket fills with Governance, This, Job, Range, Position, Posting and the
# company's own name, and the one genuinely missing tool is lost in it.
_STOP = frozenset("""
the this that these those job jobs position posting positions role roles range
additional information description requirements requirement qualifications
qualification preferred minimum required experience experiences work working
team teams company employer employment benefits salary compensation equal
opportunity applicants applicant candidates candidate please apply application
you your our their they will can may must should who what when where why how
other others including include includes included such well also able ability
new full time part join build help make take work day days year years
united states us usa remote hybrid onsite office location locations
bachelor bachelors master masters degree university college
""".split())


@dataclass
class Gap:
    strong: list[tuple[str, int, str]] = field(default_factory=list)
    underrepresented: list[tuple[str, int, str]] = field(default_factory=list)
    missing: list[tuple[str, int]] = field(default_factory=list)
    forbidden: list[tuple[str, int]] = field(default_factory=list)


def _count(term: str, text: str) -> int:
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text))


def analyse(master: Master, jd_text: str, company: str = "") -> Gap:
    low = jd_text.lower()
    gap = Gap()

    rendered = " ".join(
        [master.raw["summary"]["text"]]
        + [b.text for b in master.bullets.values()]
        + [", ".join(g.get("default") or g["items"]) for g in master.raw["skills"]]
    ).lower()
    pool = {i for g in master.raw["skills"] for i in g["items"]}

    for term in sorted(pool):
        hits = _count(term, low)
        if not hits:
            continue
        where = [g["id"] for g in master.raw["skills"] if term in (g.get("default") or [])]
        if where:
            gap.strong.append((term, hits, ",".join(where)))
        else:
            # Confirmed and in a pool, but not currently shown. This is the most
            # actionable bucket: it is a free keyword win with no fabrication.
            owner = next(g["id"] for g in master.raw["skills"] if term in g["items"])
            gap.underrepresented.append((term, hits, owner))

    for term in NEVER_ADD:
        hits = _count(term, low)
        if hits:
            gap.forbidden.append((term, hits))

    # Capitalised multiword tokens in the JD that appear nowhere in the master.
    company_tokens = {w.lower() for w in re.split(r"[^A-Za-z]+", company or "") if w}
    for token in sorted(set(re.findall(r"\b[A-Z][A-Za-z0-9+.#-]{2,}\b", jd_text))):
        tl = token.lower()
        if tl in _STOP or tl in company_tokens:
            continue
        if tl in rendered or any(tl == t.lower() for t in pool):
            continue
        if any(tl == t.lower() for t in NEVER_ADD):
            continue
        # A term that is only ever capitalised mid-sentence is prose, not a tool.
        if token.istitle() and _count(token, low) == _count(token, jd_text.lower()):
            pass
        hits = _count(token, low)
        if hits >= 3:
            gap.missing.append((token, hits))

    gap.strong.sort(key=lambda r: -r[1])
    gap.underrepresented.sort(key=lambda r: -r[1])
    gap.missing.sort(key=lambda r: -r[1])
    return gap
