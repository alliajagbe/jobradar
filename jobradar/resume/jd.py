"""Getting the full job description back.

jobs.jsonl stores a 400-character snippet, which is enough to rank a posting and
nowhere near enough to tailor against: the real descriptions run 6,000 to 8,000
characters. So the text is refetched on demand from the board it came from.

Refetching needs the Board RECORD, not just the job id. A Workday id carries the
tenant but not the `site` or `wd_num`, and without those the CxS URL cannot be
built at all. Hence the lookup through refresh.load_boards() keyed on the job's
`board` field.

Results are cached because Workday tenants sit behind WAFs that will happily
serve one request and 403 the next, and losing a description you already had
because you re-ran a command is avoidable.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from .. import normalize, store
from ..refresh import load_boards
from ..sources import build_sources
from .paths import ResumeError, resolve

_SLUG = re.compile(r"[^A-Za-z0-9]+")


def slug_for(job: dict) -> str:
    """IntelDataAnalyst, matching the user's existing file naming."""
    def camel(text: str, limit: int = 5) -> str:
        words = [w for w in _SLUG.split(text or "") if w][:limit]
        return "".join(w[:1].upper() + w[1:] for w in words)
    return camel(job["company"], 3) + camel(job["title"], 5)


def find_jobs(query: str, *, limit: int = 8) -> list[dict]:
    """Fuzzy-match jobs.jsonl. The primary way a job is named in practice."""
    from rapidfuzz import fuzz
    jobs = list(store.load().values())
    q = query.lower()
    q_tokens = [t for t in _SLUG.split(q) if t]
    scored = []
    for job in jobs:
        company = job["company"].lower()
        title = job["title"].lower()
        # The company is scored separately and weighted heavily. Matching on the
        # combined string alone ranks any "Data Analyst" above the company you
        # actually named, because the role words dominate the token set.
        # partial_ratio is far too generous on short tokens: it scores "intel"
        # against "analytic services" highly enough to outrank Intel itself.
        # Match whole company tokens instead, allowing a prefix and a little fuzz.
        c_tokens = [t for t in _SLUG.split(company) if t]
        company_hit = 0
        for qt in q_tokens:
            for ct in c_tokens:
                if ct == qt:
                    company_hit = max(company_hit, 100)
                elif len(qt) >= 4 and ct.startswith(qt):
                    company_hit = max(company_hit, 92)
                elif len(qt) >= 4:
                    company_hit = max(company_hit, fuzz.ratio(qt, ct) - 12)
        score = (0.55 * company_hit
                 + 0.30 * fuzz.token_set_ratio(q, title)
                 + 0.15 * fuzz.token_set_ratio(q, f"{company} {title}"))
        if job["status"] == "open":
            score += 6          # prefer a live posting over a closed one
        scored.append((score, job))
    scored.sort(key=lambda s: (-s[0], s[1]["company"]))
    return [j for _, j in scored[:limit]]


def job_by_id(job_id: str) -> dict:
    jobs = store.load()
    if job_id in jobs:
        return jobs[job_id]
    for job in jobs.values():
        if job.get("url") == job_id:
            return job
    raise ResumeError(f"no job with id or url {job_id!r} in data/jobs.jsonl")


def fetch_description(job: dict, *, refetch: bool = False,
                      progress=None) -> tuple[str, str]:
    """Return (text, sha). Cached under ~/.jobradar/jd-cache/."""
    slug = slug_for(job)
    cache = resolve("jd-cache", f"{slug}.txt", create_parent=True)
    if cache.exists() and not refetch:
        body = cache.read_text(encoding="utf-8")
        head, _, text = body.partition("\n---\n")
        if progress:
            progress(f"  using cached description ({len(text)} chars); --refetch to refresh")
        return text, head.split("sha=")[-1].strip()

    boards = {b.key: b for b in load_boards()}
    board = boards.get(job.get("board", ""))
    if board is None:
        raise ResumeError(
            f"board {job.get('board')!r} is no longer in seeds/. The posting cannot be "
            f"refetched; pass --jd-file with the description pasted into a text file."
        )
    source = build_sources(["data analyst"])[board.source]
    external = job["id"].split(":", 2)[2]
    try:
        hit = next((p for p in source.list_postings(board) if p.external_id == external), None)
    except Exception as exc:                                    # noqa: BLE001
        raise ResumeError(f"refetch failed: {exc}") from exc
    if hit is None:
        raise ResumeError(
            f"the posting is no longer listed on {board.key}. It may have closed. "
            f"Pass --jd-file to tailor against a saved copy."
        )
    if source.needs_hydration and not (hit.description_html or hit.description_text):
        hit = source.hydrate(board, hit)
    text = hit.description_text or normalize.html_to_text(
        hit.description_html, double_unescape=hit.double_escaped)
    sha = hashlib.sha256(text.encode()).hexdigest()[:8]
    cache.write_text(f"fetched={date.today().isoformat()} sha={sha}\n---\n{text}",
                     encoding="utf-8")
    if progress:
        progress(f"  fetched {len(text)} chars from {board.key}")
    return text, sha
