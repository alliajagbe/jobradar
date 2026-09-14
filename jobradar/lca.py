"""Department of Labor LCA disclosure data, compacted to an employer table.

The source file is a 252 MB xlsx that expands to roughly 2 GB of XML. Three
rules make that tractable:

1. Stream it. `load_workbook(read_only=True)` plus `iter_rows(values_only=True)`
   never materializes the sheet. pandas would need several gigabytes and offers
   nothing we want here.

2. Index columns by HEADER NAME, never by position. DOL reorders and renames
   columns between fiscal years, and a positional reader silently files worksite
   states under employer name, producing a sponsorship table that looks fine and
   is entirely wrong.

3. Take ONE FILE PER FISCAL YEAR. The quarterly files are cumulative
   year-to-date, so ingesting Q1, Q2 and Q3 separately triples every count and
   makes every employer look three times the sponsor it is. The verification
   step at the end prints the decision-date range so this is checkable rather
   than assumed.

Known risk: openpyxl loads the whole of `sharedStrings.xml` into memory even in
read-only mode. On a file with this many distinct employer and worksite strings
that can be several hundred megabytes resident. A GitHub Actions runner has 7 GB
and absorbs it. If that ever stops being true, the fallback is a hand-rolled
reader over `zipfile` + `xml.etree.ElementTree.iterparse` with shared strings
resolved from a disk-backed offset index, which is constant memory and about
eighty lines.
"""

from __future__ import annotations

import csv
import gzip
import re
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from . import config
from .normalize import employer_norm

PERFORMANCE_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"

WANTED = {
    "EMPLOYER_NAME", "CASE_STATUS", "VISA_CLASS", "SOC_CODE",
    "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "DECISION_DATE",
}


def find_lca_files() -> list[tuple[str, str]]:
    """Scrape the performance page for LCA disclosure links.

    Scraped rather than hardcoded because DOL moves these between /media/ and
    /sites/dolgov/files/ETA/oflc/pdfs/ without warning, and a hardcoded URL
    turns into a silent 404 that looks like "no sponsorship data this quarter".
    """
    response = httpx.get(PERFORMANCE_PAGE, timeout=60, follow_redirects=True)
    response.raise_for_status()
    found: dict[str, str] = {}
    for href in re.findall(r'href="([^"]*LCA_Disclosure_Data[^"]*\.xlsx)"', response.text):
        url = href if href.startswith("http") else "https://www.dol.gov" + href
        url = url.replace("dol.gov//", "dol.gov/")
        name = url.rsplit("/", 1)[-1]
        found[name] = url
    return sorted(found.items())


def latest_per_year(files: list[tuple[str, str]], years: int = 2) -> list[tuple[str, str]]:
    """One file per fiscal year, the highest quarter available. See rule 3."""
    best: dict[int, tuple[int, str, str]] = {}
    for name, url in files:
        m = re.search(r"FY(\d{4})(?:_Q(\d))?", name)
        if not m:
            continue
        year, quarter = int(m.group(1)), int(m.group(2) or 4)
        if year not in best or quarter > best[year][0]:
            best[year] = (quarter, name, url)
    chosen = sorted(best.items(), reverse=True)[:years]
    return [(name, url) for _, (_, name, url) in chosen]


def download(url: str, name: str, *, progress=None) -> "object":
    """Resumable ranged download. A truncated xlsx fails deep inside openpyxl
    with an unhelpful zip error, so the file is only renamed into place once its
    length matches what the server promised."""
    config.LCA_DIR.mkdir(parents=True, exist_ok=True)
    final = config.LCA_DIR / name
    if final.exists():
        if progress:
            progress(f"  {name}: already downloaded")
        return final
    part = final.with_suffix(".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}

    with httpx.stream("GET", url, headers=headers, timeout=120, follow_redirects=True) as r:
        if r.status_code not in (200, 206):
            raise RuntimeError(f"{url} -> {r.status_code}")
        total = int(r.headers.get("Content-Length", 0)) + (have if r.status_code == 206 else 0)
        mode = "ab" if r.status_code == 206 and have else "wb"
        written = have if mode == "ab" else 0
        mark = 0
        with part.open(mode) as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                written += len(chunk)
                if progress and written - mark >= 20 << 20:
                    mark = written
                    pct = f" ({100*written//total}%)" if total else ""
                    progress(f"  {name}: {written >> 20} MB{pct}")
    part.replace(final)
    if progress:
        progress(f"  {name}: done, {final.stat().st_size >> 20} MB")
    return final


@dataclass
class Agg:
    display_name: str = ""
    certified: int = 0
    denied: int = 0
    analyst_certified: int = 0
    last_decision: str = ""
    states: set = field(default_factory=set)


def ingest(paths: list, *, progress=None, row_limit: int | None = None) -> dict[str, Agg]:
    from openpyxl import load_workbook

    totals: dict[str, Agg] = defaultdict(Agg)
    for path in paths:
        if progress:
            progress(f"  reading {path.name}")
        book = load_workbook(path, read_only=True, data_only=True)
        sheet = book[book.sheetnames[0]]
        rows = sheet.iter_rows(values_only=True)
        header = next(rows)
        index = {str(name).strip().upper(): i for i, name in enumerate(header) if name}
        missing = WANTED - set(index)
        if "EMPLOYER_NAME" in missing or "CASE_STATUS" in missing:
            raise RuntimeError(f"{path.name}: missing required columns {sorted(missing)}")
        if progress and missing:
            progress(f"    note: columns absent in this file: {sorted(missing)}")

        for count, row in enumerate(rows, 1):
            try:
                _fold(totals, row, index)
            except (IndexError, TypeError, ValueError):
                continue        # one malformed row must not end a 700k-row file
            if progress and count % 100_000 == 0:
                progress(f"    {count:,} rows, {len(totals):,} employers")
            if row_limit and count >= row_limit:
                break
        book.close()
    return totals


def _fold(totals: dict[str, Agg], row, index: dict[str, int]) -> None:
    def cell(name):
        i = index.get(name)
        return row[i] if i is not None and i < len(row) else None

    raw_name = cell("EMPLOYER_NAME")
    if not raw_name:
        return
    key = employer_norm(str(raw_name))
    if not key:
        return

    agg = totals[key]
    if not agg.display_name:
        agg.display_name = str(raw_name).strip()

    status = str(cell("CASE_STATUS") or "").upper()
    certified = status.startswith("CERTIFIED")
    if certified:
        agg.certified += 1
        soc = str(cell("SOC_CODE") or "").strip()
        if soc in config.ANALYST_SOC_CODES:
            agg.analyst_certified += 1
    elif status.startswith("DENIED"):
        agg.denied += 1

    decided = cell("DECISION_DATE")
    if decided:
        text = str(decided)[:10]
        if text > agg.last_decision:
            agg.last_decision = text

    state = cell("WORKSITE_STATE")
    if state:
        agg.states.add(str(state).strip().upper())


def write_table(totals: dict[str, Agg], *, top: int = 100_000) -> int:
    """Write the compacted table the pipeline reads.

    Capped at the top employers by certified count: the long tail is single
    filings from firms nobody is applying to, and shipping 600k rows into the
    repository for them is not worth the diff noise.
    """
    ranked = sorted(totals.items(), key=lambda kv: -kv[1].certified)[:top]
    config.SPONSORS_CSV_GZ.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(config.SPONSORS_CSV_GZ, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["norm_name", "display_name", "certified", "analyst_certified",
                         "denied", "last_decision", "states"])
        for key, agg in ranked:
            writer.writerow([key, agg.display_name, agg.certified, agg.analyst_certified,
                             agg.denied, agg.last_decision, ",".join(sorted(agg.states))])
    return len(ranked)


def verify(totals: dict[str, Agg], *, progress=print) -> None:
    """Print what a human needs to see to believe the ingest.

    If a six-person LLC tops this list, the aggregation is wrong. The expected
    names are the large IT services firms and the big tech employers.
    """
    ranked = sorted(totals.items(), key=lambda kv: -kv[1].certified)[:20]
    dates = [a.last_decision for a in totals.values() if a.last_decision]
    progress(f"  employers: {len(totals):,}")
    if dates:
        progress(f"  decision dates: {min(dates)} to {max(dates)}")
    progress("  top employers by certified filings:")
    for key, agg in ranked:
        progress(f"    {agg.certified:7,}  {agg.analyst_certified:6,} analyst  {agg.display_name[:48]}")
