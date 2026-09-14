"""The pipeline: boards in, scored job records out.

Stages run in a deliberate order, and the ordering is the performance story.
The title and location gates need only a title string and a location string,
both of which arrive in the cheap list payload. Running them before hydration
is what keeps a 4,800-posting SmartRecruiters board from costing 4,800 detail
requests to surface a dozen analyst roles.

Nothing after the title gate is deleted. Every rejection is kept with a
`drop_reason` and shown behind a toggle on the page, because a regex false
positive that deletes a row is invisible, and these regexes will have false
positives. The title gate is the single exception, justified by volume: three
hundred boards times six hundred postings is 180,000 engineering and sales rows
that buy nothing.
"""

from __future__ import annotations

import csv
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import yaml

from . import config, normalize, score as scoring, sponsorship, store, taxonomy
from .matching import Matcher
from .sources import Board, SourceError, build_sources
from .sources import http


@dataclass
class RunReport:
    boards_total: int = 0
    boards_ok: int = 0
    boards_failed: int = 0
    postings_seen: int = 0
    kept: int = 0
    new: int = 0
    closed: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def drop(self, reason: str) -> None:
        key = reason.split(":")[0]
        self.dropped[key] = self.dropped.get(key, 0) + 1


def load_profile() -> dict:
    return yaml.safe_load(config.PROFILE_YAML.read_text(encoding="utf-8")) or {}


def load_boards(only_source: str | None = None, only_token: str | None = None) -> list[Board]:
    boards: list[Board] = []
    for path in (config.BOARDS_CSV, config.SEEDS_DIR / "discovered.csv"):
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not row.get("token"):
                    continue
                board = Board(
                    company=row["company"].strip(),
                    source=row["source"].strip(),
                    token=row["token"].strip(),
                    site=(row.get("site") or "").strip() or None,
                    wd_num=int(row["wd_num"]) if (row.get("wd_num") or "").strip() else None,
                )
                if only_source and board.source != only_source:
                    continue
                if only_token and board.token != only_token:
                    continue
                boards.append(board)
    # One board per identity, first file wins.
    seen: set[str] = set()
    unique: list[Board] = []
    for board in boards:
        if board.key in seen:
            continue
        seen.add(board.key)
        unique.append(board)
    return unique


def run(
    *,
    only_source: str | None = None,
    only_token: str | None = None,
    limit_boards: int | None = None,
    progress=None,
) -> tuple[list[dict], RunReport]:
    profile = load_profile()
    preferred = {c.lower() for c in (profile.get("locations", {}).get("preferred") or [])}
    profile_skills = set(profile.get("skills_i_have") or [])
    boards = load_boards(only_source, only_token)
    if limit_boards:
        boards = boards[:limit_boards]

    sources = build_sources(profile.get("workday_search_terms"))
    report = RunReport(boards_total=len(boards))
    http.reset_circuits()

    employers = sponsorship.employer_table()
    matcher = Matcher(list(employers)) if employers else None
    company_match_cache: dict[str, tuple] = {}

    existing = store.load()
    today = date.today().isoformat()
    fetched: dict[str, dict] = {}
    # Boards that completed cleanly. Only these may close their own jobs.
    healthy: set[str] = set()

    def handle(board: Board) -> tuple[Board, list[dict], str | None]:
        source = sources.get(board.source)
        if source is None:
            return board, [], f"unknown source {board.source!r}"
        try:
            listed = list(source.list_postings(board))
        except SourceError as exc:
            return board, [], str(exc)

        records: list[dict] = []
        gated = 0
        for posting in listed:
            title_n = normalize.title_norm(posting.title)
            match = taxonomy.title_tier(title_n)
            if match.tier in (None, "N"):
                continue                       # stage 1: the only silent discard
            gated += 1
            try:
                record = _build(
                    board, source, posting, title_n, match,
                    preferred=preferred, profile_skills=profile_skills,
                    employers=employers, matcher=matcher,
                    company_match_cache=company_match_cache,
                )
            except SourceError as exc:
                return board, records, str(exc)
            records.append(record)
        return board, records, None

    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
        for board, records, error in pool.map(handle, boards):
            if error:
                report.boards_failed += 1
                report.errors.append(f"{board.source}/{board.token}: {error}")
                if progress:
                    progress(f"  FAIL {board.source}/{board.token}: {error}")
                continue
            report.boards_ok += 1
            healthy.add(board.key)
            for record in records:
                report.postings_seen += 1
                fetched[record["id"]] = record
            if progress:
                # The gate ratio is the single most useful line in the log: when
                # a taxonomy edit breaks the title gate, every board reports 0.
                progress(f"  ok   {board.source}/{board.token}: {len(records)} candidates")

    merged: dict[str, dict] = dict(existing)
    for job_id, record in fetched.items():
        if job_id in existing:
            merged[job_id] = store.merge(existing[job_id], record, today=today)
        else:
            merged[job_id] = store.new_record(record, today=today)
            report.new += 1
        if merged[job_id].get("drop_reason"):
            report.drop(merged[job_id]["drop_reason"])
        else:
            report.kept += 1

    report.closed = close_missing(merged, fetched, healthy, sources, today=today)
    http.close()
    return list(merged.values()), report


def _build(board, source, posting, title_n, match, *, preferred, profile_skills,
           employers, matcher, company_match_cache) -> dict:
    """One posting through stages 2 through 7."""
    drop_reason: str | None = None

    senior = taxonomy.seniority_reject(title_n)
    if senior:
        drop_reason = f"seniority:{senior}"

    location = normalize.parse_locations(
        posting.location_raw, list(posting.locations), None, preferred
    )
    if drop_reason is None and not location.is_us and location.confidence == "high":
        drop_reason = "location:non-us"

    # Stage 4: hydrate only what survived the cheap gates.
    if drop_reason is None and source.needs_hydration and not (
        posting.description_html or posting.description_text
    ):
        posting = source.hydrate(board, posting)
        location = normalize.parse_locations(
            posting.location_raw, list(posting.locations), None, preferred
        )
        if not location.is_us and location.confidence == "high":
            drop_reason = "location:non-us"

    text = posting.description_text or normalize.html_to_text(
        posting.description_html, double_unescape=posting.double_escaped
    )
    # Re-run the location parse with the description available, which is what
    # resolves a bare "Remote" into remote-US or a foreign role.
    location = normalize.parse_locations(
        posting.location_raw, list(posting.locations), text, preferred
    )
    if drop_reason is None and not location.is_us and location.confidence == "high":
        drop_reason = "location:non-us"

    verdict = taxonomy.sponsorship_text_verdict(normalize.sentences(text))
    if drop_reason is None and verdict.verdict in (
        "says_no", "requires_citizenship", "requires_clearance"
    ):
        drop_reason = f"text:{verdict.pattern}"

    min_years = taxonomy.min_years_required(text)
    if drop_reason is None and min_years is not None and min_years >= 6:
        drop_reason = f"experience:{min_years}y"

    company = posting.company_name or board.company
    stats, method, mscore = _match_company(
        company, employers, matcher, company_match_cache
    )
    signal = sponsorship.resolve(
        text_verdict=verdict, stats=stats, match_method=method,
        match_score=mscore, is_us=location.is_us,
    )

    penalty, level = normalize.title_level_penalty(posting.title)
    scored = scoring.score_job(
        title_tier=match.tier, title_term=match.term, description_text=text,
        location=location, posted_at=posting.posted_at,
        sponsorship_signal=signal.signal, sponsorship_detail=signal.detail,
        level_penalty=penalty, level_label=level, profile_skills=profile_skills,
    )

    title_dedupe = normalize.title_dedupe_norm(posting.title)
    company_n = normalize.company_norm(company)
    basis = f"{company_n} | {title_dedupe} | {location.primary}"

    return {
        "id": f"{posting.source}:{board.token}:{posting.external_id}",
        "board": board.key,
        "dedupe_key": hashlib.sha256(basis.encode()).hexdigest()[:16],
        "dedupe_basis": basis,
        "company": company,
        "company_norm": company_n,
        "source": posting.source,
        "url": posting.url,
        "title": posting.title,
        "department": posting.department,
        "locations": list(location.locations),
        "location_primary": location.primary,
        "is_remote": location.is_remote,
        "is_us": location.is_us,
        "location_confidence": location.confidence,
        "posted_at": posting.posted_at,
        "posted_at_source": posting.posted_at_source,
        "snippet": text[:config.SNIPPET_CHARS],
        "desc_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        "score": scored.score,
        "explain": scored.components,
        "matched_skills": scored.matched_skills,
        "missing_skills": scored.missing_skills,
        "min_years": scored.min_years,
        "title_tier": match.tier,
        "sponsorship": signal.as_dict(),
        "status": "dropped" if drop_reason else "open",
        "drop_reason": drop_reason,
    }


def _match_company(company, employers, matcher, cache):
    if matcher is None:
        return None, "no-data", None
    if company not in cache:
        result = matcher.match(company)
        stats = employers.get(result.norm_name) if result.norm_name else None
        cache[company] = (stats, result.method, result.score)
    return cache[company]


def close_missing(merged, fetched, healthy, sources, *, today) -> int:
    """Mark jobs that did not come back this run.

    The `healthy` check is the whole point. A board that hit a 429 returns zero
    postings, and closing by absence would mark its entire board dead. The naive
    version of this silently closes six hundred jobs the first time a source
    rate-limits you, and it looks exactly like the market going quiet.

    Workday is a paginated search rather than a board listing, so absence there
    only means "did not match this run's terms". Those age out on a timer.
    """
    closed = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.WORKDAY_STALE_DAYS)).date().isoformat()
    for job_id, record in merged.items():
        if job_id in fetched or record.get("status") == "closed":
            continue
        source = sources.get(record.get("source"))
        board_key = record.get("board")
        if source is not None and source.closes_by_absence:
            if board_key not in healthy:
                continue          # the board failed; absence proves nothing
            record["status"] = "closed"
            record["closed_at"] = today
            closed += 1
        else:
            if record.get("last_seen", today) < cutoff:
                record["status"] = "closed"
                record["closed_at"] = today
                closed += 1
    return closed


def _profile_skill_names() -> list[str]:
    """Skills the profile claims, used only to compute the gap list.

    This must be what the profile actually has, not the whole lexicon. Reading
    the lexicon here makes `missing_skills` the empty set on every job, which
    looks like a working feature and is not one.
    """
    profile = load_profile()
    return list(profile.get("skills_i_have") or [])
