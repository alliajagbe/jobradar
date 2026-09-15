"""Workday tenants, via the CxS endpoint their own career pages call.

This is the awkward source, and the comments below are the reasons why.

- `limit` is capped at 20 no matter what you send. Sending 100 returns 20, and
  a board that looks suspiciously small is usually this.
- Several tenants 403 a library User-Agent. They serve the same public JSON to
  a browser-shaped one.
- It is a SEARCH, not a board listing. You pass keywords and get matches, so a
  posting missing from a run means "did not match this run's terms", never
  "closed". `closes_by_absence = False` is what stops the expiry pass from
  marking an entire tenant dead the first time a search term changes.
- A board needs three unknowns: tenant, the wd{N} host number, and the career
  site segment. None of them are guessable in bulk, which is why Workday
  tenants are curated by hand in seeds/boards.csv rather than discovered.
"""

from __future__ import annotations

from typing import Iterator

from .. import config
from .base import Board, ProbeResult, RawPosting, SourceError
from . import http

# Workday's own cap. Sending more is silently ignored.
PAGE = 20

_HEADERS = {"User-Agent": config.WORKDAY_USER_AGENT, "Accept": "application/json"}


def _host(board: Board) -> str:
    return f"https://{board.token}.wd{board.wd_num or 1}.myworkdayjobs.com"


def _cxs(board: Board) -> str:
    return f"{_host(board)}/wday/cxs/{board.token}/{board.site}"


class Workday:
    name = "workday"
    closes_by_absence = False
    needs_hydration = True

    def __init__(self, search_terms: list[str] | None = None) -> None:
        self.search_terms = search_terms or ["data analyst"]

    def list_postings(self, board: Board) -> Iterator[RawPosting]:
        if not board.site:
            raise SourceError(f"workday/{board.token}: no career site configured")
        seen: set[str] = set()
        for term in self.search_terms:
            for posting in self._search(board, term):
                if posting.external_id in seen:
                    continue
                seen.add(posting.external_id)
                yield posting

    def _search(self, board: Board, term: str) -> Iterator[RawPosting]:
        offset = 0
        for _ in range(config.WORKDAY_MAX_PAGES):
            payload = http.post_json(
                f"{_cxs(board)}/jobs",
                {"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": term},
                headers=_HEADERS,
            )
            if not isinstance(payload, dict):
                raise SourceError(f"workday/{board.token}: unexpected payload")
            postings = payload.get("jobPostings") or []
            for job in postings:
                path = job.get("externalPath") or ""
                if not path:
                    continue
                yield RawPosting(
                    source=self.name,
                    external_id=path,
                    url=f"{_host(board)}/en-US/{board.site}{path}",
                    title=job.get("title") or "",
                    location_raw=job.get("locationsText") or "",
                    posted_at=None,        # set during hydration from startDate
                    posted_at_source="startDate",
                    company_name=board.company,
                    extra={"path": path},
                )
            offset += len(postings)
            if not postings or offset >= (payload.get("total") or 0):
                return

    def hydrate(self, board: Board, posting: RawPosting) -> RawPosting:
        detail = http.get_json(f"{_cxs(board)}{posting.extra['path']}", headers=_HEADERS)
        if not isinstance(detail, dict):
            return posting
        info = detail.get("jobPostingInfo") or {}
        start = info.get("startDate")
        return RawPosting(**{
            **posting.__dict__,
            # The detail payload carries the title, and a posting reached by URL
            # rather than by search arrives without one.
            "title": posting.title or info.get("title") or "",
            "description_html": info.get("jobDescription"),
            "url": info.get("externalUrl") or posting.url,
            "location_raw": info.get("location") or posting.location_raw,
            "posted_at": f"{start}T00:00:00Z" if start else None,
            "employment_type": info.get("timeType"),
        })

    def probe(self, slug: str, expect: str) -> ProbeResult:
        # Workday is not slug-guessable: it needs tenant + wd number + site.
        # Board discovery skips this source entirely.
        return ProbeResult("error")
