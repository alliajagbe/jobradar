"""SmartRecruiters job boards.

Paginated, and the list payload carries no description, so this source needs a
detail request per posting. That makes the title-gate-before-hydration rule in
refresh.py load-bearing here: a 4,800-posting enterprise board would otherwise
cost 4,800 detail requests to find a dozen analyst roles.

Every `jobAd.sections.*` lookup is guarded. Sections are optional and a missing
`qualifications` block is common, not exceptional.
"""

from __future__ import annotations

from typing import Iterator

from .base import Board, ProbeResult, RawPosting, SourceError
from . import http
from .greenhouse import normalize_iso

BASE = "https://api.smartrecruiters.com/v1/companies"
PAGE = 100


class SmartRecruiters:
    name = "smartrecruiters"
    closes_by_absence = True
    needs_hydration = True

    def list_postings(self, board: Board) -> Iterator[RawPosting]:
        offset = 0
        while True:
            payload = http.get_json(
                f"{BASE}/{board.token}/postings",
                params={"limit": PAGE, "offset": offset},
            )
            if not isinstance(payload, dict):
                raise SourceError(f"smartrecruiters/{board.token}: unexpected payload")
            content = payload.get("content") or []
            for job in content:
                try:
                    yield self._to_posting(job, board)
                except (KeyError, TypeError) as exc:
                    raise SourceError(
                        f"smartrecruiters/{board.token}: bad job record: {exc}"
                    ) from exc
            offset += len(content)
            total = payload.get("totalFound") or 0
            if not content or offset >= total:
                return

    def _to_posting(self, job: dict, board: Board) -> RawPosting:
        location = job.get("location") or {}
        # Prefer fullLocation: it spells the country out ("..., Colombia")
        # where the country field is a bare ISO code the matcher would miss.
        parts = [location.get("fullLocation")] or []
        if not parts[0]:
            parts = [location.get("city"), location.get("region"), location.get("country")]
        return RawPosting(
            source=self.name,
            external_id=str(job["id"]),
            url=(job.get("ref") or "") or f"https://jobs.smartrecruiters.com/{board.token}/{job['id']}",
            title=job.get("name") or "",
            location_raw=", ".join(p for p in parts if p),
            locations=tuple(
                x for x in [location.get("fullLocation")] if x
            ),
            department=(job.get("department") or {}).get("label"),
            employment_type=(job.get("typeOfEmployment") or {}).get("label"),
            posted_at=normalize_iso(job.get("releasedDate") or job.get("createdOn")),
            posted_at_source="releasedDate",
            company_name=board.company,
            extra={"detail_id": str(job["id"])},
        )

    def hydrate(self, board: Board, posting: RawPosting) -> RawPosting:
        detail = http.get_json(f"{BASE}/{board.token}/postings/{posting.extra['detail_id']}")
        if not isinstance(detail, dict):
            return posting
        sections = ((detail.get("jobAd") or {}).get("sections")) or {}
        chunks = [
            (sections.get(key) or {}).get("text")
            for key in ("companyDescription", "jobDescription", "qualifications",
                        "additionalInformation")
        ]
        html_text = "\n\n".join(c for c in chunks if c)
        url = detail.get("applyUrl") or detail.get("postingUrl") or posting.url
        return RawPosting(**{**posting.__dict__, "description_html": html_text, "url": url})

    def probe(self, slug: str, expect: str) -> ProbeResult:
        try:
            payload = http.get_json(f"{BASE}/{slug}/postings", params={"limit": 1})
        except SourceError as exc:
            return ProbeResult("notfound" if "404" in str(exc) else "error")
        if not isinstance(payload, dict):
            return ProbeResult("error")
        total = payload.get("totalFound") or 0
        if not total:
            return ProbeResult("empty", 200, 0)
        content = payload.get("content") or []
        reported = (content[0].get("company") or {}).get("name") if content else None
        return ProbeResult("confirmed", 200, total, reported)
