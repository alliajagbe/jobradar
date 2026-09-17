"""Turning a pasted job link into something tailorable.

The page can only ever hand over a URL. It is a static site on GitHub Pages, so
it cannot fetch a posting itself: every ATS blocks cross-origin reads, and there
is no server to proxy through. All the work therefore happens here, locally.

Two paths. A URL from a known applicant tracking system is decoded into the
board and posting id it already implies, and fetched through the same adapter
the crawler uses, which yields the full structured posting rather than a page of
navigation chrome. Anything else falls back to fetching the HTML and extracting
the densest block of text on it.

The fallback is deliberately unambitious. It will not parse a LinkedIn posting
behind a login wall, and pretending otherwise would produce a resume tailored
against a cookie banner. When it cannot find enough text it says so and points
at `--jd-file`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .. import normalize
from ..sources import Board, build_sources
from .. import config
from .paths import ResumeError

# Enough text that it is plausibly a job description rather than a redirect
# stub or a consent page.
MIN_JD_CHARS = 700


def check_public_url(url: str) -> str:
    """Refuse anything that is not a public http(s) address.

    This fetches a URL that, once the local helper exists, can be supplied by a
    request rather than typed by a person. Without this guard the helper is an
    open relay into the machine's own network: `http://127.0.0.1:8777/`,
    `http://192.168.1.1/`, the cloud metadata address, or `file:///etc/passwd`.

    It is blind SSRF, since the response lands in a brief the caller cannot
    read, which lowers the severity and does not remove it.
    """
    import ipaddress
    import socket

    parts = urlsplit(url if "//" in url else "https://" + url)
    if parts.scheme not in ("http", "https"):
        raise ResumeError(
            f"refusing scheme {parts.scheme!r}: only http and https are fetched, "
            f"so file:// and friends cannot be used to read local files."
        )
    host = parts.hostname
    if not host:
        raise ResumeError(f"no host in {url!r}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ResumeError(f"cannot resolve {host!r}: {exc}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            raise ResumeError(
                f"refusing {host!r}: it resolves to {address}, which is not a public "
                f"address. A job posting does not live on this machine's own network."
            )
    return url


@dataclass(frozen=True)
class Posting:
    company: str
    title: str
    url: str
    text: str
    source: str
    board_key: str | None = None
    external_id: str | None = None


_PATTERNS = (
    ("greenhouse", re.compile(
        r"^(?:job-)?boards\.greenhouse\.io$|^boards-api\.greenhouse\.io$"),
     re.compile(r"^/(?:embed/job_app\?for=)?(?P<token>[^/?]+)/jobs/(?P<id>\d+)")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$"),
     re.compile(r"^/(?P<token>[^/]+)/(?P<id>[0-9a-f-]{16,})")),
    ("lever", re.compile(r"^jobs\.lever\.co$"),
     re.compile(r"^/(?P<token>[^/]+)/(?P<id>[0-9a-f-]{16,})")),
    ("smartrecruiters", re.compile(r"^jobs\.smartrecruiters\.com$|^api\.smartrecruiters\.com$"),
     re.compile(r"^/(?:v1/companies/)?(?P<token>[^/]+)/(?:postings/)?(?P<id>\d+)")),
)

_WORKDAY_HOST = re.compile(r"^(?P<tenant>[^.]+)\.wd(?P<wd>\d+)\.myworkdayjobs\.com$")
_WORKDAY_PATH = re.compile(r"^(?:/[a-z]{2}-[A-Z]{2})?/(?P<site>[^/]+)(?P<path>/job/.+)$")


def identify(url: str) -> dict | None:
    """Decode an ATS URL into the board and posting it names, or None."""
    parts = urlsplit(url if "//" in url else "https://" + url)
    host, path = parts.netloc.lower(), parts.path

    wd = _WORKDAY_HOST.match(host)
    if wd:
        m = _WORKDAY_PATH.match(path)
        if not m:
            return None
        return {"source": "workday", "token": wd.group("tenant"),
                "wd_num": int(wd.group("wd")), "site": m.group("site"),
                "external_id": m.group("path")}

    for source, host_re, path_re in _PATTERNS:
        if host_re.match(host):
            m = path_re.match(path)
            if m:
                return {"source": source, "token": m.group("token"),
                        "external_id": m.group("id")}
    return None


def _from_ats(spec: dict, url: str, progress=None) -> Posting:
    board = Board(company=spec["token"], source=spec["source"], token=spec["token"],
                  site=spec.get("site"), wd_num=spec.get("wd_num"))
    source = build_sources(["data analyst"])[spec["source"]]
    if progress:
        progress(f"  recognised {spec['source']} board {spec['token']}")

    hit = None
    if spec["source"] == "workday":
        # Workday's list endpoint is a keyword search, so a posting is not
        # reachable by listing. Hydrate the path straight from the URL instead.
        from ..sources.workday import Workday
        stub = type("P", (), {"extra": {"path": spec["external_id"]}, "__dict__": {}})()
        raw = {"source": "workday", "external_id": spec["external_id"], "url": url,
               "title": "", "location_raw": "", "locations": (), "department": None,
               "employment_type": None, "posted_at": None, "posted_at_source": "startDate",
               "description_html": None, "description_text": None, "company_name": None,
               "double_escaped": False, "extra": {"path": spec["external_id"]}}
        from ..sources.base import RawPosting
        hit = source.hydrate(board, RawPosting(**raw))
    else:
        for posting in source.list_postings(board):
            if posting.external_id == str(spec["external_id"]):
                hit = posting
                break
        if hit is not None and source.needs_hydration and not (
                hit.description_html or hit.description_text):
            hit = source.hydrate(board, hit)

    if hit is None:
        raise ResumeError(
            f"the board {spec['token']!r} answered but does not list posting "
            f"{spec['external_id']}. It may have closed. Use --jd-file to tailor "
            f"against a saved copy."
        )
    text = hit.description_text or normalize.html_to_text(
        hit.description_html, double_unescape=hit.double_escaped)
    return Posting(
        company=hit.company_name or spec["token"],
        title=hit.title or "Role",
        url=hit.url or url,
        text=text,
        source=spec["source"],
        board_key=board.key,
        external_id=str(spec["external_id"]),
    )


# Elements that are never the job description.
_STRIP = ("script", "style", "nav", "header", "footer", "noscript", "svg", "form",
          "iframe", "aside", "button")


def _from_html(url: str, progress=None) -> Posting:
    if progress:
        progress("  not a known ATS URL, fetching the page")
    try:
        response = httpx.get(url, timeout=45, follow_redirects=True,
                             headers={"User-Agent": config.USER_AGENT,
                                      "Accept": "text/html,application/xhtml+xml"})
    except httpx.HTTPError as exc:
        raise ResumeError(f"could not fetch {url}: {exc}") from exc
    if response.status_code >= 400:
        raise ResumeError(
            f"{url} returned {response.status_code}. Many job boards refuse "
            f"automated requests; save the description to a file and pass --jd-file."
        )

    from lxml import html as lxml_html
    try:
        tree = lxml_html.fromstring(response.text)
    except Exception as exc:                                    # noqa: BLE001
        raise ResumeError(f"could not parse {url}: {exc}") from exc

    for tag in _STRIP:
        for node in tree.xpath(f"//{tag}"):
            node.getparent().remove(node)

    title = ""
    for xp in ("//h1", "//meta[@property='og:title']/@content", "//title"):
        found = tree.xpath(xp)
        if found:
            title = (found[0] if isinstance(found[0], str)
                     else found[0].text_content()).strip()
            if title:
                break

    # The densest block wins. A job page is mostly chrome, and the description
    # is the one subtree with real prose in it.
    best, best_len = None, 0
    for node in tree.xpath("//main|//article|//div|//section"):
        text = normalize.html_to_text(lxml_html.tostring(node, encoding="unicode"))
        if len(text) > best_len:
            best, best_len = text, len(text)
    text = best or normalize.html_to_text(response.text)

    if len(text) < MIN_JD_CHARS:
        raise ResumeError(
            f"only {len(text)} characters of text at {url}, which is not a job "
            f"description. The page probably needs JavaScript or a login. Save the "
            f"description to a file and pass --jd-file."
        )

    host = urlsplit(str(response.url)).netloc
    company = re.sub(r"^(www|jobs|careers|apply)\.", "", host).split(".")[0]
    return Posting(company=company.title(), title=title or "Role",
                   url=str(response.url), text=text, source="link")


def fetch(url: str, *, progress=None) -> Posting:
    check_public_url(url)
    spec = identify(url)
    if spec:
        return _from_ats(spec, url, progress)
    return _from_html(url, progress)
