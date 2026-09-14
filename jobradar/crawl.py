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
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import normalize, taxonomy
from .sources import Board, SourceError, build_sources

CC_COLLINFO = "http://index.commoncrawl.org/collinfo.json"

# Where each ATS publishes its public boards. The path segment after the host
# is the board token, which is the same token the JSON APIs take.
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
    response = httpx.get(CC_COLLINFO, timeout=60, follow_redirects=True)
    response.raise_for_status()
    return response.json()[0]["id"]


def tokens_for(source: str, index_id: str, *, progress=None) -> set[str]:
    """Board tokens for one ATS, read out of the Common Crawl URL index."""
    base = f"http://index.commoncrawl.org/{index_id}-index"
    found: set[str] = set()
    for host in CC_HOSTS[source]:
        params = {"url": f"{host}/*", "output": "json"}
        try:
            meta = httpx.get(base, params={**params, "showNumPages": "true"},
                             timeout=90).json()
        except (httpx.HTTPError, ValueError):
            continue
        for page in range(meta.get("pages", 0)):
            try:
                response = httpx.get(base, params={**params, "page": page}, timeout=180)
            except httpx.HTTPError:
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
