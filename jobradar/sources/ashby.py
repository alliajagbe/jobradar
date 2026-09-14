"""Ashby job boards.

One request per board, real HTML in `descriptionHtml` (not double-escaped,
unlike Greenhouse), and `isListed` marks whether a posting is actually public.

The probe here is weaker than Greenhouse's: the payload carries no reliable
company name, so a confirmed probe rests on the slug alone. Boards discovered
this way are worth a look on their first refresh, since a slug collision shows
up as a board whose postings never match any target title.
"""

from __future__ import annotations

from typing import Iterator

from .base import Board, ProbeResult, RawPosting, SourceError
from . import http
from .greenhouse import normalize_iso

BASE = "https://api.ashbyhq.com/posting-api/job-board"


class Ashby:
    name = "ashby"
    closes_by_absence = True
    needs_hydration = False

    def list_postings(self, board: Board) -> Iterator[RawPosting]:
        payload = http.get_json(f"{BASE}/{board.token}", params={"includeCompensation": "true"})
        if not isinstance(payload, dict):
            raise SourceError(f"ashby/{board.token}: unexpected payload type")
        for job in payload.get("jobs") or []:
            if job.get("isListed") is False:
                continue
            try:
                yield self._to_posting(job, board)
            except (KeyError, TypeError) as exc:
                raise SourceError(f"ashby/{board.token}: bad job record: {exc}") from exc

    def _to_posting(self, job: dict, board: Board) -> RawPosting:
        secondary = [
            s.get("location") for s in (job.get("secondaryLocations") or [])
            if isinstance(s, dict) and s.get("location")
        ]
        return RawPosting(
            source=self.name,
            external_id=str(job["id"]),
            url=job.get("jobUrl") or f"https://jobs.ashbyhq.com/{board.token}/{job['id']}",
            title=job.get("title") or "",
            location_raw=job.get("location") or "",
            locations=tuple(secondary),
            department=job.get("department") or job.get("team"),
            employment_type=job.get("employmentType"),
            posted_at=normalize_iso(job.get("publishedAt")),
            posted_at_source="publishedAt",
            description_html=job.get("descriptionHtml"),
            company_name=board.company,
        )

    def hydrate(self, board: Board, posting: RawPosting) -> RawPosting:
        return posting

    def probe(self, slug: str, expect: str) -> ProbeResult:
        try:
            payload = http.get_json(f"{BASE}/{slug}")
        except SourceError as exc:
            return ProbeResult("notfound" if "404" in str(exc) else "error")
        jobs = (payload or {}).get("jobs") or [] if isinstance(payload, dict) else []
        if not jobs:
            return ProbeResult("empty", 200, 0)
        return ProbeResult("confirmed", 200, len(jobs), None)
