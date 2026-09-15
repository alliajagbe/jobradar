"""Finding every board, rather than only the ones somebody thought to list.

The curated seed list has a flaw that no amount of curation fixes: it contains
the companies whoever wrote it already knew. That skews it, and the skew is
invisible from inside. The first version of this project seeded 128 boards and
missed 29 of the 30 largest analyst H-1B sponsors in the country.

The manual workaround people use is a search engine: `site:greenhouse.io
"data analyst"` finds boards nobody curated. Scraping Google is not an option
(its terms forbid it, and it blocks automation with CAPTCHAs), but the useful
half of that query does not need Google at all. What the search engine really
provides is an INDEX OF WHICH BOARDS EXIST, and Common Crawl publishes exactly
that, free and without a key.

So: read the board tokens out of the Common Crawl URL index, then let the
ordinary ATS adapters fetch them. That is strictly better than parsing search
results, because a board token yields the complete current board with full
descriptions, real posting dates and structured locations, where a search
result yields a stale snippet and a URL.

A token from the index is a token that existed when the crawl ran, not one that
is live now, so `live_boards` fetches each one and keeps only those actually
carrying a role worth looking at.
"""

from __future__ import annotations

import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import normalize, taxonomy
from .sources import Board, SourceError, build_sources

CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"

# Common Crawl asks crawlers to identify themselves, and throttles hard from
# cloud IP ranges: a GitHub Actions runner gets 503 SlowDown where a laptop
# sails through. Hence the retries, and hence CrawlError below.
CC_HEADERS = {"User-Agent": "jobradar/0.1 (personal job search; +https://github.com/alliajagbe/jobradar)"}
CC_ATTEMPTS = 5


class CrawlError(RuntimeError):
    """The index could not be read.

    This exists because the first version swallowed every HTTP error and
    returned an empty set, so a throttled run reported SUCCESS having found
    nothing and quietly wrote an empty board list over a good one. A discovery
    pass that finds no boards is a failure, and it has to say so.
    """


def _get(url: str, params: dict) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(CC_ATTEMPTS):
        try:
            response = httpx.get(url, params=params, timeout=180,
                                 headers=CC_HEADERS, follow_redirects=True)
        except httpx.HTTPError as exc:
            last = exc
        else:
            if response.status_code == 200:
                return response
            # 503 SlowDown is Common Crawl's rate limit and is always worth
            # waiting out; it is the normal response from a busy runner.
            last = CrawlError(f"{url} -> {response.status_code}")
            if response.status_code not in (429, 500, 502, 503, 504):
                raise last
        if attempt < CC_ATTEMPTS - 1:
            time.sleep(min(60.0, 5.0 * (2 ** attempt)) + random.uniform(0, 3))
    raise CrawlError(f"{url}: {last}")

# Where each ATS publishes its public boards. The path segment after the host
# is the board token, which is the same token the JSON APIs take.
# Lever publishes at jobs.lever.co/{company}, but Common Crawl barely indexes
# it: a full sweep returns a single token where Greenhouse returns thousands.
# It stays supported for an explicit --sources lever, and out of the default.
DEFAULT_SOURCES = ("greenhouse", "ashby")

CC_HOSTS = {
    "greenhouse": ("job-boards.greenhouse.io", "boards.greenhouse.io"),
    "ashby": ("jobs.ashbyhq.com",),
    "lever": ("jobs.lever.co",),
}

# Board tokens are slugs. Anything else on these hosts is a static asset or an
# application path, not a company.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,60}")
_NOT_A_BOARD = {
    "embed", "static", "assets", "images", "img", "css", "js", "favicon.ico",
    "robots.txt", "sitemap.xml", "api", "app", "login", "signup", "search",
    "jobs", "job", "careers", "privacy", "terms", "error", "404", "index.html",
}


def latest_index() -> str:
    return _get(CC_COLLINFO, {}).json()[0]["id"]


def tokens_for(source: str, index_id: str, *, progress=None) -> set[str]:
    """Board tokens for one ATS, read out of the Common Crawl URL index."""
    base = f"http://index.commoncrawl.org/{index_id}-index"
    found: set[str] = set()
    failures: list[str] = []
    for host in CC_HOSTS[source]:
        params = {"url": f"{host}/*", "output": "json"}
        try:
            meta = _get(base, {**params, "showNumPages": "true"}).json()
        except (CrawlError, ValueError) as exc:
            failures.append(f"{host}: {exc}")
            continue
        for page in range(meta.get("pages", 0)):
            try:
                response = _get(base, {**params, "page": page})
            except CrawlError as exc:
                failures.append(f"{host} page {page}: {exc}")
                continue
            for line in response.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                match = re.match(r"https?://[^/]+/([^/?#]+)", record.get("url", ""))
                if not match:
                    continue
                token = match.group(1)
                if token.lower() in _NOT_A_BOARD or not _TOKEN.fullmatch(token):
                    continue
                found.add(token if source == "ashby" else token.lower())
        if progress:
            progress(f"  {host}: {len(found)} tokens so far")
    # Reading zero tokens from a host that certainly has thousands means the
    # index refused us, not that the boards vanished. Say so rather than
    # returning an empty set that looks like a legitimate answer.
    if not found:
        raise CrawlError(
            f"{source}: no tokens read from {', '.join(CC_HOSTS[source])}. "
            + ("; ".join(failures) if failures else "index returned no records")
        )
    return found


def live_boards(
    candidates: dict[str, set[str]],
    *,
    known: set[tuple[str, str]] = frozenset(),
    workers: int = 8,
    progress=None,
) -> list[dict]:
    """Fetch each candidate board and keep the ones carrying a target role.

    A token is kept only if at least one of its live postings passes the title
    gate. Most boards on these platforms belong to companies that never hire
    analysts, and carrying them costs a request on every refresh forever.
    """
    sources = build_sources()
    work = [
        (source, token)
        for source, tokens in candidates.items()
        for token in sorted(tokens)
        if (source, token) not in known
    ]
    kept: list[dict] = []
    done = 0

    def check(item):
        source_name, token = item
        board = Board(company=token, source=source_name, token=token)
        try:
            postings = list(sources[source_name].list_postings(board))
        except SourceError:
            return None
        except Exception:                                    # noqa: BLE001
            return None
        company = None
        hits = 0
        for posting in postings:
            match = taxonomy.title_tier(normalize.title_norm(posting.title))
            if match.tier and match.tier != "N":
                hits += 1
                company = company or posting.company_name
        if not hits:
            return None
        return {"company": company or token, "source": source_name, "token": token,
                "site": "", "wd_num": "", "hits": hits, "total": len(postings)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(check, work):
            done += 1
            if result:
                kept.append(result)
            if progress and done % 200 == 0:
                progress(f"  checked {done}/{len(work)}, keeping {len(kept)}")
    return kept
