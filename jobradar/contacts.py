"""Find named people worth writing to, and say whether their employer sponsors.

An application is a lottery with hundreds of entrants. A specific message to a
named person about work they actually published is not, and the research pool is
where that works best: academic authorship is public by design, a cold note to a
group about their own paper is conventional rather than presumptuous, and the
institutions are cap-exempt, so sponsoring somebody does not depend on winning
the March lottery.

Two public sources, both already within this project's reach:

  OpenAlex       who is publishing what, where, and in what author position
  the LCA table  whether that employer has ever had a petition certified

The join is the whole point. OpenAlex alone lists interesting people at places
that may never hire her. The filing table alone lists institutions with no named
humans in them. Together they give a person, a paper to open with, and a number
that says whether the place can sponsor at all.

Author position is used as a proxy for who to write to, because in the life and
computational sciences it reliably is one:

  last author    the principal investigator. Runs the group, holds the budget,
                 decides the hiring. The person to ask about a role.
  first author   did the work. A peer conversation, and they know what is open
                 before it is posted.
  middle author  neither. Dropped, because a list that includes everyone is a
                 list nobody writes to.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from urllib.parse import quote_plus

from . import config, sponsorship
from . import matching
from .matching import Matcher, entity_stem
from .normalize import employer_norm
from .sources import http

OPENALEX_WORKS = "https://api.openalex.org/works"

# Enough of a paper record to write a first line, and nothing more.
_SELECT = "id,doi,title,publication_year,publication_date,authorships"

def matching_generic_stems() -> frozenset:
    """The company-side generic stems, reused rather than re-listed.

    "MACHINE INTELLIGENCE RESEARCH INSTITUTE" merged with a bare "MACHINE
    INTELLIGENCE" record in the first industry run, because the institution
    list below is university-flavoured and knew nothing about corporate
    boilerplate. The matcher already maintains that vocabulary.
    """
    return frozenset(matching._GENERIC_STEMS)


# Words that identify nothing on their own. "Washington" is the distinctive
# token in "Washington University"; "University" is not, and neither is
# "Artificial Intelligence". Used to refuse a merge on a generic affix.
_GENERIC_INST = frozenset("""
UNIVERSITY UNIVERSITIES COLLEGE SCHOOL MEDICAL CENTER CENTERS CENTRE HOSPITAL HOSPITALS
HEALTH HEALTHCARE SCIENCES SCIENCE INSTITUTE INSTITUTES RESEARCH LABORATORY LABORATORIES
CLINIC FOUNDATION SYSTEM SYSTEMS DEPARTMENT DIVISION FACULTY GRADUATE NATIONAL STATE
REGIONAL MEMORIAL CHILDRENS CHILDREN GENERAL COMMUNITY COMPREHENSIVE CANCER HEART BRAIN
TRUSTEES BOARD REGENTS AUTHORITY TRUST THE OF AND FOR AT IN S
ARTIFICIAL INTELLIGENCE MACHINE LEARNING AI ML ADVANCED APPLIED INNOVATION
INNOVATIONS CLOUD CYBER SMART GROUP COMPANY CORP INCORPORATED
""".split()) | matching_generic_stems()

# "Washington University in St. Louis" and "Washington University" are one
# employer. "University of Washington" is not either of them, which is why the
# merge below is by ordered token prefix and never by token set.
_CITY_TAIL = re.compile(r"\s+(?:in|at)\s+[A-Z][\w.\-]*(?:\s+[A-Z][\w.\-]*){0,2}$")
_PARENS = re.compile(r"\s*\([^)]*\)")

# OpenAlex institution types, grouped by whether the employer is exempt from
# the H-1B cap under INA 214(g)(5): higher education, affiliated nonprofits,
# nonprofit and governmental research.
#
# This defaults to cap-exempt and that default is the entire point. Ranking by
# filing volume without it puts Amazon (16,793 certified) and Microsoft (7,043)
# at the top of every search, and those are precisely the cap-subject employers
# whose lottery this is meant to route around. A big number next to a company
# name is not an advantage here; it is the thing being avoided.
# Sector groups, named the way Alli thinks about them, each mapping to the
# OpenAlex institution types it covers. `--sector` takes a comma list, so
# industry and nonprofit can run together, which is the default.
SECTOR_TYPES = {
    "industry": ("company",),
    "nonprofit": ("nonprofit",),
    "academic": ("education", "healthcare"),
    "government": ("government", "facility"),
}
DEFAULT_SECTORS = ("industry", "nonprofit")

# Whether the employer has to enter the H-1B lottery, under INA 214(g)(5).
# Deliberately three values and not a boolean: higher education and government
# research are exempt outright, a company never is, and a nonprofit is exempt
# only if it is a research organisation or affiliated with a university.
# RAND and RTI qualify on that basis; Mercy Corps and the Wildlife
# Conservation Society, which the same OpenAlex type returns, do not. Printing
# "exempt" for all of them would be a legal claim this data cannot support.
_CAP = {
    "education": "exempt", "healthcare": "exempt",
    "government": "exempt", "facility": "exempt",
    "nonprofit": "check",
    "company": "subject",
}


@dataclass(frozen=True)
class Contact:
    """One person, with the reason to write to them and the reason it is worth it."""
    person: str
    position: str           # "PI" or "first author"
    institution: str
    sector: str             # OpenAlex institution type: education, healthcare, company, ...
    certified: int | None
    analyst_certified: int | None
    merged_from: str        # which employer records were summed, for auditing
    paper: str
    year: int | None
    published: str          # ISO date, used for ordering: a fresh paper opens better
    link: str
    openalex_author: str
    orcid: str = ""         # from OpenAlex; present for roughly 2 in 3 authors

    # ---- ways to find the person ----
    #
    # There is deliberately no email field. Nothing public and reliable
    # provides one: OpenAlex has no emails, Europe PMC's API returns none and
    # refuses browser requests outright, and ORCID public emails came back
    # empty for every author sampled, because almost nobody marks them public.
    # The remaining options are reading corresponding-author lines out of PDFs,
    # which does not cover industry papers at all, or generating
    # firstname.lastname permutations against a company domain, which is an
    # email harvester. These links go to places the person chose to publish
    # about themselves instead.

    @property
    def linkedin_search(self) -> str:
        """A prefilled people search, not a scrape.

        LinkedIn has no public search API and scraping it risks the account
        doing the scraping, so this is the query she would type by hand, one
        click instead of three. The organisation is stripped of OpenAlex's
        "(United States)" suffix, which otherwise matches nothing.
        """
        org = _PARENS.sub("", self.institution).strip()
        query = quote_plus(f"{self.person} {org}".strip())
        return f"https://www.linkedin.com/search/results/people/?keywords={query}"

    @property
    def scholar_search(self) -> str:
        """Author-profile search. A Scholar profile shows a verified
        institutional email DOMAIN, which is the closest legitimate thing to
        an address that exists without guessing."""
        org = _PARENS.sub("", self.institution).strip()
        return ("https://scholar.google.com/citations?view_op=search_authors&mauthors="
                + quote_plus(f"{self.person} {org}".strip()))

    @property
    def orcid_url(self) -> str:
        return self.orcid or ""

    @property
    def cap(self) -> str:
        """"subject", "exempt" or "check". See `_CAP`."""
        return _CAP.get(self.sector, "check")

    @property
    def pool(self) -> str:
        """The outreach tracker's vocabulary, so a row can be pasted straight in.

        An industry author is the `author` pool: the paper is still a real hook,
        but the sponsorship is a lottery, so it is not the research-group pool.
        """
        return "author" if self.cap == "subject" else "capexempt"

    def as_dict(self) -> dict:
        """For the helper's JSON. `asdict` alone drops the properties.

        `cap` and `pool` are both computed, and both are what the page actually
        uses: one is shown in the candidate row, the other is written into the
        outreach entry when a candidate is promoted.
        """
        return {**asdict(self), "cap": self.cap, "pool": self.pool, "tier": self.tier,
                "linkedin": self.linkedin_search, "scholar": self.scholar_search}

    @property
    def tier(self) -> int:
        """How well the employer's filing history supports an analyst hire.

        Sponsorship is a gate rather than a gradient: past the point where a
        place clearly sponsors analysts, more filings do not make the person
        more worth writing to. Ranking on the raw count instead is what put
        Amazon's 16,793 at the top of every search while a well-matched paper
        at a mid-size company sat below it.
        """
        if (self.certified or 0) >= config.SPONSOR_STRONG_CERTIFIED \
                and (self.analyst_certified or 0) >= config.SPONSOR_STRONG_ANALYST_SOC:
            return 0
        if (self.certified or 0) >= 1:
            return 1
        return 2


def institution_variants(name: str) -> list[str]:
    """Progressively plainer spellings of one institution name.

    OpenAlex writes the full legal-ish name; the DOL table writes whatever the
    petitioner typed. Trying only the full string is how "Washington University
    in St. Louis" matched a 14-filing record while the 492-filing one sat there
    unqueried.
    """
    seen, out = set(), []
    for candidate in (name, _PARENS.sub("", name), _CITY_TAIL.sub("", _PARENS.sub("", name))):
        plain = re.sub(r"^The\s+", "", candidate or "").strip()
        if plain and plain.lower() not in seen:
            seen.add(plain.lower())
            out.append(plain)
    return out


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(t for t in entity_stem(employer_norm(name)).split() if t)


def simple_key(name: str) -> str:
    """A normalisation the browser can reproduce exactly. This is a contract.

    `employer_norm` folds accents and then strips legal, geographic and
    descriptor suffixes against three vocabularies, repeating until stable.
    Reimplementing that in JavaScript is the same trap as reimplementing the
    merge rules, which produced four bugs in a week, so it is NOT ported.

    Instead this is the first six lines of `employer_norm` and nothing else,
    which is five lines of JavaScript and provably the same. The published
    index emits every employer under both forms, so the page's dumb key still
    lands on an entry built with the clever one.

    Any change here must be mirrored in `simpleKey` in docs/app.js, and the
    round-trip test in tests/test_contacts.py pins the pair.
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = text.split()
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    return " ".join(tokens)


def _affix_index(employers: dict) -> tuple[dict, dict]:
    """Employers bucketed by the first and last token of their stem.

    `_siblings` scans the whole table, which is fine for a handful of lookups
    and hopeless for all 64,000 of them: that is four billion comparisons and
    it runs inside `publish` on every refresh.

    A merge requires the whole shorter stem to be a prefix or a suffix of the
    longer, so two siblings always share either their first token or their
    last. Bucketing on both ends turns the scan into two small lookups.
    Generic-only buckets are skipped because `_mergeable` refuses them anyway.
    """
    first: dict[str, list] = {}
    last: dict[str, list] = {}
    for key, stats in employers.items():
        toks = _tokens(stats.display_name)
        if not toks:
            continue
        if toks[0] not in _GENERIC_INST:
            first.setdefault(toks[0], []).append((key, toks))
        if toks[-1] not in _GENERIC_INST:
            last.setdefault(toks[-1], []).append((key, toks))
    return first, last


def build_sponsor_index(employers: dict, *, min_certified: int = 1) -> dict:
    """The lookup the page fetches instead of running the join itself.

    Keys are `simple_key` and `employer_norm` spellings; values are
    `[certified, analyst_certified]` AFTER merging an employer with its own
    other spellings, so "Washington University" and "Washington University in
    St. Louis" both read the group total rather than 492 and 14.

    The merge reuses `_mergeable` and `_siblings`' rule unchanged. Those carry
    the regression tests for the Penn over-merge and the `DUKE 65` collapse,
    and having a second implementation here is exactly what this file has been
    bitten by before.
    """
    first, last = _affix_index(employers)
    out: dict[str, list] = {}
    for key, stats in employers.items():
        if stats.certified < min_certified:
            continue
        toks = _tokens(stats.display_name)
        group = {key}
        if toks:
            for bucket in (first.get(toks[0], ()), last.get(toks[-1], ())):
                for other_key, other_toks in bucket:
                    if other_key != key and _mergeable(toks, other_toks):
                        group.add(other_key)
        merged = sponsorship.combine(employers, tuple(sorted(group)))
        if merged is None:
            continue
        value = [merged.certified, merged.analyst_certified]
        # Both key forms, because the page can only compute the dumb one.
        for form in {simple_key(stats.display_name),
                     simple_key(employer_norm(stats.display_name))}:
            if not form:
                continue
            # A collision between genuinely different employers keeps the
            # larger, which is the one she is more likely to mean.
            if form not in out or out[form][0] < value[0]:
                out[form] = value
    return {
        "meta": {
            "strong_certified": config.SPONSOR_STRONG_CERTIFIED,
            "strong_analyst": config.SPONSOR_STRONG_ANALYST_SOC,
            "cap": dict(_CAP),
            "sector_types": {k: list(v) for k, v in SECTOR_TYPES.items()},
            "default_sectors": list(DEFAULT_SECTORS),
            "employers": len(out),
        },
        "employers": out,
    }


def _mergeable(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether two employer name stems are the same institution spelled differently.

    Ordered affix, never token set, and from either end:

      prefix  WASHINGTON UNIVERSITY  <  WASHINGTON UNIVERSITY IN ST LOUIS
      suffix  UT SOUTHWESTERN MEDICAL CENTER  >  ...TEXAS SOUTHWESTERN MEDICAL CENTER

    The suffix half exists because institutions abbreviate their own front end.
    "UT Southwestern Medical Center" and "The University of Texas Southwestern
    Medical Center" are one place, and neither string is a prefix of the other,
    so prefix-only matching reported 1 certified filing for a pair that hold 399.

    Two guards, each closing a specific way this goes wrong:

    - At least two shared tokens, unless the stems are identical. `entity_stem`
      drops digits, so "DUKE 65" reduces to the single token DUKE, which would
      otherwise prefix-match every Duke record and fold an unrelated company
      into the university's filing history.
    - The shared part must carry a non-generic token. "MEDICAL CENTER" is a
      suffix of hundreds of unrelated hospitals; "SOUTHWESTERN MEDICAL CENTER"
      is a suffix of one institution's several spellings.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) < 2:
        return False
    if not any(t not in _GENERIC_INST for t in short):
        return False

    # Prefix: extra tokens AFTER a complete institution name are a branch,
    # campus or school of that same institution.
    #   WASHINGTON UNIVERSITY  +  IN ST LOUIS
    #   JOHNS HOPKINS UNIVERSITY  +  APPLIED PHYSICS LAB
    if long_[:len(short)] == short:
        return True

    # Suffix: extra tokens BEFORE a complete institution name usually make it a
    # DIFFERENT institution that shares a system name, so this is only a merge
    # when those leading tokens are organisational boilerplate.
    #
    #   TRUSTEES OF THE UNIVERSITY OF PENNSYLVANIA   is Penn
    #   KUTZTOWN UNIVERSITY OF PENNSYLVANIA          is not
    #
    # Without this the first live run reported 507 certified filings for Penn by
    # folding in Kutztown, Millersville, West Chester, East Stroudsburg,
    # Slippery Rock, Indiana and Commonwealth: seven unrelated state schools
    # that merely end with the same three words.
    if long_[-len(short):] == short:
        return all(t in _GENERIC_INST for t in long_[:len(long_) - len(short)])
    return False


def _siblings(stem_tokens: tuple[str, ...], employers: dict) -> list[str]:
    """Employer keys that are the same institution as `stem_tokens`."""
    if not stem_tokens or all(t in _GENERIC_INST for t in stem_tokens):
        return []
    return [key for key, stats in employers.items()
            if _mergeable(stem_tokens, _tokens(stats.display_name))]


def resolve_institution(name: str, matcher: Matcher, employers: dict):
    """Filing history for one institution, merged across its own spellings.

    Returns (stats, merged_names). The merged names are
    handed back rather than hidden: summing is only honest if you can see what
    was summed.

    What this deliberately does NOT do is resolve an abbreviated front end.
    "UT Southwestern Medical Center" and "The University of Texas Southwestern
    Medical Center" are one institution, but neither full stem is an affix of
    the other, so they stay separate and the second reports 1 certified filing
    where the pair hold 399.

    Merging them needs an acronym table, and every heuristic tried in its place
    produced a worse error than the undercount: matching on the rarest shared
    token finds nothing, and matching on the last distinctive token announces
    that University of Washington "may also file as" Washington University, a
    different school two thousand miles away. So the merge stays conservative
    and `merged_names` is returned for auditing: the row shows exactly which
    records were counted, and an abbreviated name is visibly a single record.
    """
    best, best_names = None, ()
    for variant in institution_variants(name):
        match = matcher.match(variant)
        names = tuple(getattr(match, "names", ()) or ())
        if not names:
            continue
        keys = set(names)
        for seed in names:
            keys.update(_siblings(_tokens(employers[seed].display_name), employers))
        # The institution as OpenAlex spells it, too: the matched record may be
        # an abbreviation that shares no affix with the other spellings.
        keys.update(_siblings(_tokens(variant), employers))
        stats = sponsorship.combine(employers, tuple(sorted(keys)))
        if stats and (best is None or stats.certified > best.certified):
            best, best_names = stats, tuple(sorted(keys))
        if best is not None:
            break

    return best, best_names


def _works(topic: str, since: str, pages: int, per_page: int):
    """Pages of OpenAlex works matching one topic, newest first."""
    cursor = "*"
    for _ in range(max(1, pages)):
        params = {
            "filter": (f"title_and_abstract.search:{topic},"
                       f"from_publication_date:{since},"
                       "authorships.institutions.country_code:us"),
            "per-page": str(per_page),
            "cursor": cursor,
            "select": _SELECT,
            "sort": "publication_date:desc",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        body = http.get_json(f"{OPENALEX_WORKS}?{query}",
                             headers={"User-Agent": config.USER_AGENT})
        if not isinstance(body, dict):
            return
        results = body.get("results") or []
        yield from results
        cursor = ((body.get("meta") or {}).get("next_cursor")) or ""
        if not cursor or len(results) < per_page:
            return


def find(topics, *, since: str | None = None, pages: int = 2, per_page: int = 100,
         min_filings: int = 1, positions=("last", "first"), limit: int = 40,
         sectors=DEFAULT_SECTORS, per_institution: int = 2,
         progress=None) -> list[Contact]:
    """People to write to, newest well-sponsored paper first.

    `min_filings` of 1 is deliberate: an employer with no certified petition on
    record is not a reason to spend an hour writing to someone there, and there
    are more good targets than there is time.
    """
    say = progress or (lambda _m: None)
    allowed = types_for(sectors)
    since = since or (date.today() - timedelta(days=548)).isoformat()
    employers = sponsorship.employer_table()
    matcher = Matcher(employers)
    inst_cache: dict[str, tuple] = {}

    seen_authors: dict[str, Contact] = {}
    for topic in ([topics] if isinstance(topics, str) else list(topics)):
        say(f"  searching OpenAlex: {topic}")
        found = 0
        for work in _works(topic, since, pages, per_page):
            title = (work.get("title") or "").strip()
            if not title:
                continue
            for authorship in work.get("authorships") or []:
                position = authorship.get("author_position")
                if position not in positions:
                    continue
                author = authorship.get("author") or {}
                author_id = author.get("id") or ""
                person = (author.get("display_name") or "").strip()
                if not person or not author_id:
                    continue
                for inst in authorship.get("institutions") or []:
                    inst_name = (inst.get("display_name") or "").strip()
                    inst_type = (inst.get("type") or "").strip()
                    if not inst_name:
                        continue
                    # The OpenAlex country filter is a property of the WORK: it
                    # keeps papers with at least one US institution anywhere on
                    # them, not papers whose every author is in the US. So each
                    # institution is checked again here. Without this a
                    # Canadian co-author's employer was looked up in a table of
                    # US petitions and reported 116 certified filings, which is
                    # a number about a different organisation entirely.
                    if (inst.get("country_code") or "").upper() != "US":
                        continue
                    if allowed and inst_type not in allowed:
                        continue
                    if inst_name not in inst_cache:
                        inst_cache[inst_name] = resolve_institution(inst_name, matcher, employers)
                    stats, names = inst_cache[inst_name]
                    certified = stats.certified if stats else 0
                    if certified < min_filings:
                        continue
                    # One row per person. The newest paper is the better opener,
                    # and the loop walks newest first, so the first win stays.
                    if author_id in seen_authors:
                        continue
                    seen_authors[author_id] = Contact(
                        person=person,
                        position="PI" if position == "last" else "first author",
                        institution=inst_name,
                        sector=inst_type,
                        certified=certified,
                        analyst_certified=stats.analyst_certified if stats else 0,
                        merged_from="; ".join(names),
                        paper=title,
                        year=work.get("publication_year"),
                        published=(work.get("publication_date") or ""),
                        link=work.get("doi") or work.get("id") or "",
                        openalex_author=author_id,
                        orcid=(author.get("orcid") or ""),
                    )
                    found += 1
                    break
        say(f"    {found} new contacts")

    # Tier first, then recency. Within employers that clearly sponsor analysts,
    # the freshest paper is the better opener, and "I read your paper from last
    # month" is a different message from one about work two years old.
    rows = sorted(seen_authors.values(),
                  key=lambda c: (c.tier, _neg_date(c.published), c.person))
    return _spread(rows, limit, per_institution)


def types_for(sectors) -> tuple[str, ...]:
    """OpenAlex institution types for a sector list. Empty tuple means any."""
    names = [sectors] if isinstance(sectors, str) else list(sectors)
    if "any" in names:
        return ()
    out: list[str] = []
    for name in names:
        out.extend(SECTOR_TYPES.get(name, ()))
    return tuple(dict.fromkeys(out)) or types_for(DEFAULT_SECTORS)


def _neg_date(iso: str):
    """Sort key putting the newest ISO date first, blanks last.

    A plain `-date` is not available on a string, and reversing the whole sort
    would also reverse the tier, so the date is inverted on its own. OpenAlex
    sometimes gives a partial date ("2026-10") or none, so parts are padded to
    three and anything non-numeric is treated as absent rather than raising.
    """
    parts = (iso or "").split("-")[:3]
    nums = []
    for part in parts:
        try:
            nums.append(-int(part))
        except ValueError:
            break
    nums += [0] * (3 - len(nums))
    return (not iso, tuple(nums))


def _spread(rows: list[Contact], limit: int, per_institution: int) -> list[Contact]:
    """Take the best rows, but not all from one place.

    Institutions tie on filing count, so a straight sort returns whole
    departments: the first real run gave eight of ten contacts at one
    university. Forty contacts where thirty share an employer is not forty
    options, it is one option and a lot of names. So each institution
    contributes up to `per_institution` on the first pass, and the remaining
    slots are filled from what is left, still in rank order.
    """
    if per_institution <= 0:
        return rows[:limit]
    taken: dict[str, int] = {}
    first, rest = [], []
    for row in rows:
        key = row.institution
        if taken.get(key, 0) < per_institution:
            taken[key] = taken.get(key, 0) + 1
            first.append(row)
        else:
            rest.append(row)
    return (first + rest)[:limit]


# The CSV header, in one place because three things depend on it agreeing:
# write_csv, the helper's /api/contacts reader, and the page's parser.
CSV_HEAD = ["Author ID", "Person", "Their role", "Organisation", "Sector",
            "H-1B cap", "Pool", "Hook", "Link", "Published",
            "Certified filings", "In analyst roles", "ORCID", "LinkedIn", "Scholar",
            "Counted from"]


def write_csv(rows: list[Contact], path) -> int:
    """A CSV whose columns are the outreach tracker's fields, in its order.

    `Author ID` leads because it is the only stable key in the data. A person's
    name repeats across institutions, and name-plus-employer breaks the moment
    somebody moves, so the page keys candidates and dismissals on the OpenAlex
    author id rather than on anything a human would read.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEAD)
        for c in rows:
            writer.writerow([c.openalex_author, c.person, c.position, c.institution,
                             c.sector, c.cap, c.pool,
                             f"{c.paper} ({c.year})" if c.year else c.paper,
                             c.link, c.published, c.certified, c.analyst_certified,
                             c.orcid, c.linkedin_search, c.scholar_search,
                             c.merged_from])
    return len(rows)


def read_csv(path) -> list[dict]:
    """Rows of a contacts CSV as dicts, keyed by the header's own names.

    Used by the local helper so the page does not have to find the file itself.
    Returns [] for anything unreadable rather than raising: a CSV half-written
    by a run still in progress must not take the whole route down.
    """
    try:
        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    # A file without the id column is from before it existed, or hand-edited.
    # It is still useful, so fall back to a key built from what is there.
    out = []
    for row in rows:
        if not (row.get("Person") or "").strip():
            continue
        if not (row.get("Author ID") or "").strip():
            row["Author ID"] = f"{row.get('Person', '')}|{row.get('Organisation', '')}"
        out.append(row)
    return out
