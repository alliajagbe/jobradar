"""The contract every ATS adapter satisfies.

Two methods rather than one because two of the five sources cannot give you a
description without a second request per posting. The refresh loop calls
`list_postings`, applies the title and location gates (which need only a title
and a location string), and only then calls `hydrate` on the survivors. On a
Workday tenant with 900 open roles that is roughly a dozen detail requests
instead of nine hundred, which is the difference between a two-minute run and a
forty-minute one.

`SourceError` is the single exception type. An adapter that lets an
httpx.ReadTimeout or a KeyError escape ends a 300-board run at board 41, so
every adapter wraps everything it can raise into this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable


class SourceError(Exception):
    """Every failure any adapter can produce."""


@dataclass(frozen=True)
class Board:
    company: str
    source: str
    token: str
    site: str | None = None      # Workday career site segment
    wd_num: int | None = None    # Workday wd{N} host number

    @property
    def key(self) -> str:
        return f"{self.source}:{self.token}" + (f":{self.site}" if self.site else "")


@dataclass(frozen=True)
class RawPosting:
    source: str
    external_id: str
    url: str
    title: str
    location_raw: str = ""
    locations: tuple[str, ...] = ()
    department: str | None = None
    employment_type: str | None = None
    posted_at: str | None = None        # ISO-8601 UTC
    posted_at_source: str = ""          # which field it came from, for auditing recency
    description_html: str | None = None
    description_text: str | None = None
    company_name: str | None = None
    # Greenhouse escapes its `content` field twice. Carried as a flag rather
    # than handled in the adapter so normalize.html_to_text stays the one place
    # that knows how to unwrap a description.
    double_escaped: bool = False
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    verdict: str                 # confirmed | name-mismatch | empty | notfound | error
    http_status: int | None = None
    job_count: int | None = None
    reported_name: str | None = None


@runtime_checkable
class Source(Protocol):
    name: str
    # Greenhouse, Ashby, Lever and SmartRecruiters return the COMPLETE current
    # board, so a posting's absence means it closed. Workday is a paginated
    # search over chosen keywords, where absence only means "did not match this
    # run's terms". See refresh.close_missing.
    closes_by_absence: bool
    needs_hydration: bool

    def list_postings(self, board: Board) -> Iterator[RawPosting]: ...
    def hydrate(self, board: Board, posting: RawPosting) -> RawPosting: ...
    def probe(self, slug: str, expect: str) -> ProbeResult: ...
