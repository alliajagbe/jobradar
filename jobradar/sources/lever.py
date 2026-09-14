"""Lever job boards.

One request per board. Descriptions arrive as PLAIN TEXT in `descriptionPlain`
and `additionalPlain`, so they bypass html_to_text entirely; running them
through an HTML parser mangles the line structure that the sponsorship matcher
depends on.

`createdAt` is epoch MILLISECONDS. Reading it as seconds dates every Lever
posting to 1970 and zeroes its recency points, which is a quiet way to lose a
whole source from the top of your rankings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from .base import Board, ProbeResult, RawPosting, SourceError
from . import http

BASE = "https://api.lever.co/v0/postings"


class Lever:
    name = "lever"
    closes_by_absence = True
    needs_hydration = False

    def list_postings(self, board: Board) -> Iterator[RawPosting]:
        payload = http.get_json(f"{BASE}/{board.token}", params={"mode": "json"})
        if not isinstance(payload, list):
            raise SourceError(f"lever/{board.token}: unexpected payload type")
        for job in payload:
            try:
                yield self._to_posting(job, board)
            except (KeyError, TypeError) as exc:
                raise SourceError(f"lever/{board.token}: bad job record: {exc}") from exc

    def _to_posting(self, job: dict, board: Board) -> RawPosting:
        categories = job.get("categories") or {}
        text = "\n\n".join(
            part for part in (job.get("descriptionPlain"), job.get("additionalPlain")) if part
        )
        return RawPosting(
            source=self.name,
            external_id=str(job["id"]),
            url=job.get("hostedUrl") or job.get("applyUrl") or "",
            title=job.get("text") or "",
            location_raw=categories.get("location") or "",
            locations=tuple(
                x for x in (categories.get("allLocations") or []) if isinstance(x, str)
            ),
            department=categories.get("team") or categories.get("department"),
            employment_type=categories.get("commitment"),
            posted_at=_from_millis(job.get("createdAt")),
            posted_at_source="createdAt",
            description_text=text,
            company_name=board.company,
        )

    def hydrate(self, board: Board, posting: RawPosting) -> RawPosting:
        return posting

    def probe(self, slug: str, expect: str) -> ProbeResult:
        try:
            payload = http.get_json(f"{BASE}/{slug}", params={"mode": "json"})
        except SourceError as exc:
            return ProbeResult("notfound" if "404" in str(exc) else "error")
        if not isinstance(payload, list) or not payload:
            return ProbeResult("empty", 200, 0)
        return ProbeResult("confirmed", 200, len(payload), None)


def _from_millis(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, OSError, OverflowError):
        return None
