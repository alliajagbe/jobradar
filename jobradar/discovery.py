"""Finding ATS boards by guessing their slug.

A board URL that returns 200 with a non-empty job list is a board that exists.
That makes discovery a matter of generating plausible slugs from a company name
and asking. Feeding it the Department of Labor's top H-1B sponsors is what
points the whole tool at companies that demonstrably sponsor.

Two rules keep it honest:

- A 200 is not confirmation on its own. Where the payload reports a company
  name, it has to look like the company we were hoping for. Without that check,
  discovery cheerfully attaches a well-known company's board to an unrelated
  firm with a similar name, and every job under it inherits the wrong
  sponsorship history.

- Every probe is written to data/probes.csv before anything else, including the
  misses. A miss is as informative as a hit and costs the same request, so
  re-running discovery should cost nothing for slugs already tried.

Workday is absent by design: it needs a tenant, a wd{N} host number and a career
site segment, and guessing three unknowns at once is not a search, it is a
denial of service against somebody's careers page.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from . import config
from .normalize import employer_norm
from .sources import PROBEABLE, build_sources

# A first token this generic resolves to somebody real and entirely unrelated.
# "Global Data Solutions LLC" must not probe the slug `global`.
STOPLIST = {
    "data", "tech", "group", "global", "us", "usa", "inc", "the", "american",
    "national", "international", "first", "general", "united", "new", "one",
    "advanced", "premier", "systems", "solutions", "services", "technologies",
}

NAME_MATCH_MIN = 85


@dataclass(frozen=True)
class Probe:
    company: str
    source: str
    slug: str
    verdict: str
    job_count: int | None
    reported_name: str | None
    probed_at: str


def slug_candidates(company: str, limit: int = 5) -> list[str]:
    """Plausible board slugs for a company name, most likely first."""
    norm = employer_norm(company).lower()
    tokens = [t for t in re.split(r"\s+", norm) if t]
    if not tokens:
        return []
    out: list[str] = []

    def add(value: str) -> None:
        if value and len(value) >= 4 and value not in STOPLIST and value not in out:
            out.append(value)

    add("".join(tokens))
    add("-".join(tokens))
    if len(tokens) >= 2:
        add("".join(tokens[:2]))
        add("-".join(tokens[:2]))
    add(tokens[0])
    return out[:limit]


def load_probes() -> dict[tuple[str, str], Probe]:
    if not config.PROBES_CSV.exists():
        return {}
    out: dict[tuple[str, str], Probe] = {}
    with config.PROBES_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            probe = Probe(
                company=row["company"], source=row["source"], slug=row["slug"],
                verdict=row["verdict"],
                job_count=int(row["job_count"]) if row.get("job_count") else None,
                reported_name=row.get("reported_name") or None,
                probed_at=row.get("probed_at") or "",
            )
            out[(probe.source, probe.slug)] = probe
    return out


def save_probes(probes: dict[tuple[str, str], Probe]) -> None:
    config.PROBES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with config.PROBES_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "slug", "company", "verdict", "job_count",
                         "reported_name", "probed_at"])
        for probe in sorted(probes.values(), key=lambda p: (p.source, p.slug)):
            writer.writerow([probe.source, probe.slug, probe.company, probe.verdict,
                             probe.job_count or "", probe.reported_name or "",
                             probe.probed_at])


def discover(
    companies: list[str],
    *,
    sources: tuple[str, ...] = PROBEABLE,
    max_probes: int | None = None,
    progress=None,
) -> tuple[list[dict], dict[tuple[str, str], Probe]]:
    """Probe slug candidates for each company. Returns (found boards, all probes)."""
    adapters = build_sources()
    probes = load_probes()
    today = date.today().isoformat()
    found: list[dict] = []
    spent = 0

    for company in companies:
        hit = False
        for source_name in sources:
            if hit:
                break
            for slug in slug_candidates(company):
                if max_probes is not None and spent >= max_probes:
                    return found, probes
                cached = probes.get((source_name, slug))
                if cached is None:
                    result = adapters[source_name].probe(slug, company)
                    spent += 1
                    verdict = result.verdict
                    if verdict == "confirmed" and result.reported_name:
                        # A 200 with the wrong company behind it is worse than
                        # a miss: it would attach this board to the wrong
                        # employer's sponsorship record.
                        similarity = fuzz.token_set_ratio(
                            employer_norm(result.reported_name), employer_norm(company)
                        )
                        if similarity < NAME_MATCH_MIN:
                            verdict = "name-mismatch"
                    cached = Probe(company, source_name, slug, verdict,
                                   result.job_count, result.reported_name, today)
                    probes[(source_name, slug)] = cached
                    if progress:
                        progress(f"  {source_name}/{slug}: {verdict}")
                if cached.verdict == "confirmed":
                    found.append({"company": company, "source": source_name,
                                  "token": slug, "site": "", "wd_num": ""})
                    hit = True
                    break
    return found, probes


def write_discovered(rows: list[dict]) -> int:
    path = config.SEEDS_DIR / "discovered.csv"
    existing: list[dict] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    seen = {(r["source"], r["token"]) for r in existing}
    merged = existing + [r for r in rows if (r["source"], r["token"]) not in seen]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["company", "source", "token", "site", "wd_num"])
        writer.writeheader()
        writer.writerows(sorted(merged, key=lambda r: (r["company"], r["source"])))
    return len(merged)
