"""Greenhouse job boards.

One request returns the entire board with descriptions included, which makes
this the cheapest of the five sources and the right one to develop against.

Two things about this API cost real debugging time:

1. `content` is HTML-entity-encoded TWICE. It arrives as `&amp;lt;p&amp;gt;`. One
   unescape pass leaves literal `&lt;` through the text and every skill and
   sponsorship regex then matches nothing. The symptom is a 600-job board where
   every posting scores the same.

2. Recency must come from `first_published`, not `updated_at`. `updated_at`
   moves whenever a recruiter fixes a typo, so a nine-month-old requisition
   reads as posted today and wins the recency points over genuinely fresh work.
"""

from __future__ import annotations

from typing import Iterator

from .. import normalize
from .base import Board, ProbeResult, RawPosting, SourceError
from . import http

BASE = "https://boards-api.greenhouse.io/v1/boards"


class Greenhouse:
    name = "greenhouse"
    closes_by_absence = True
    needs_hydration = False

    def list_postings(self, board: Board) -> Iterator[RawPosting]:
        payload = http.get_json(f"{BASE}/{board.token}/jobs", params={"content": "true"})
        if not isinstance(payload, dict):
            raise SourceError(f"greenhouse/{board.token}: unexpected payload type")
        for job in payload.get("jobs") or []:
            try:
                yield self._to_posting(job)
            except (KeyError, TypeError) as exc:
                raise SourceError(f"greenhouse/{board.token}: bad job record: {exc}") from exc

    def _to_posting(self, job: dict) -> RawPosting:
        offices = [o.get("name") for o in (job.get("offices") or []) if o.get("name")]
        location = (job.get("location") or {}).get("name") or ""
        departments = [d.get("name") for d in (job.get("departments") or []) if d.get("name")]
        posted = job.get("first_published") or job.get("updated_at")
        return RawPosting(
            source=self.name,
            external_id=str(job["id"]),
            url=job.get("absolute_url") or "",
            title=job.get("title") or "",
            location_raw=location,
            locations=tuple(offices),
            department=departments[0] if departments else None,
            posted_at=normalize_iso(posted),
            posted_at_source="first_published" if job.get("first_published") else "updated_at",
            description_html=job.get("content"),
            company_name=job.get("company_name"),
            double_escaped=True,
        )

    def hydrate(self, board: Board, posting: RawPosting) -> RawPosting:
        return posting

    def probe(self, slug: str, expect: str) -> ProbeResult:
        try:
            payload = http.get_json(f"{BASE}/{slug}/jobs")
        except SourceError as exc:
            return ProbeResult("notfound" if "404" in str(exc) else "error")
        jobs = (payload or {}).get("jobs") or [] if isinstance(payload, dict) else []
        if not jobs:
            return ProbeResult("empty", 200, 0)
        reported = jobs[0].get("company_name")
        return ProbeResult("confirmed", 200, len(jobs), reported)


def normalize_iso(value: str | None) -> str | None:
    """Greenhouse timestamps carry an offset (`2026-09-09T10:50:29-04:00`).
    Everything downstream compares UTC strings, so convert once here."""
    if not value:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
