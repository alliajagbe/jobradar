"""Turning messy ATS payloads into comparable strings.

Four jobs here, each of which caused a real bug before it was written:

1. `html_to_text` - job descriptions arrive as nested <div><ul><li> and the list
   boundaries are load-bearing. A regex tag-stripper joins twelve bulleted
   requirements into one 800-character line, and every sentence-level
   sponsorship pattern in taxonomy.py then fails to split it.

2. `company_norm` / `employer_norm` - "Stripe, Inc." from an ATS and
   "STRIPE PAYMENTS COMPANY" from the Department of Labor are the same employer.
   Getting these two to meet in the middle is what makes the sponsorship signal
   attachable at all.

3. `title_dedupe_norm` - the same role appears as "Analyst, Business
   Intelligence" on one board and "Business Intelligence Analyst" on another.
   Sorting the tokens is what makes those collide.

4. `parse_locations` - the difference between a US role you can take and a
   Dublin role you cannot, given a field that might say "Remote", "3 Locations",
   or "New York, NY; London".
"""

from __future__ import annotations

import html as _html
import re
import unicodedata
from dataclasses import dataclass

from lxml import html as lxml_html

# ---- HTML ---- #

_BLOCK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


def html_to_text(raw: str | None, *, double_unescape: bool = False) -> str:
    """Flatten an HTML description to plain text, keeping line structure.

    `double_unescape` is for Greenhouse, whose `content` field arrives escaped
    twice: `&amp;lt;p&amp;gt;`. One unescape pass leaves literal `&lt;` all through
    the text, every skill and sponsorship regex then matches nothing, and the
    symptom is a whole 600-job board scoring identically.
    """
    if not raw:
        return ""
    text = _html.unescape(raw)
    if double_unescape:
        text = _html.unescape(text)
    try:
        tree = lxml_html.fromstring(text)
    except Exception:
        # A malformed description must degrade to worse text, never lose the posting.
        return _collapse(re.sub(r"<[^>]+>", " ", text))

    for bad in tree.xpath("//script|//style"):
        bad.getparent().remove(bad)

    parts: list[str] = []

    def walk(node) -> None:
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag == "li":
            parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if tag in _BLOCK_TAGS or tag == "li":
            parts.append("\n")

    walk(tree)
    return _collapse("".join(parts))


def _collapse(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sentences(text: str) -> list[str]:
    """Split description text into sentence-ish units.

    Splitting on newlines as well as terminators matters: requirement bullets
    frequently carry no full stop at all, and the sponsorship evidence we show
    the user should be one readable line rather than a 4KB blob.
    """
    # The negative lookbehind on a capital letter keeps "U.S." and "Ph.D." in
    # one piece. Splitting there cut "authorized to work in the U.S. without
    # sponsorship" into two fragments and the no-sponsorship pattern then
    # matched neither half. Over-merging two real sentences is harmless here;
    # under-merging silently breaks the most important match in the project.
    parts = re.split(r"(?<=[.!?])(?<![A-Z]\.)\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


# ---- Company names ---- #

# Stripped repeatedly from the end, because "ACME HOLDINGS INC LLC" is a real
# spelling in the DOL data.
_LEGAL_SUFFIXES = {
    "INC", "INCORPORATED", "LLC", "LTD", "LIMITED", "CORP", "CORPORATION",
    "CO", "COMPANY", "LP", "LLP", "PLC", "PC", "PLLC", "GMBH", "SA", "NV",
    "AG", "PTE", "PVT", "SAS", "BV", "AB",
}
_GEO_SUFFIXES = {
    "USA", "US", "AMERICA", "AMERICAS", "NORTHAMERICA", "NA", "GLOBAL",
    "INTERNATIONAL", "WORLDWIDE",
}
# Stripped only when they are not the whole remaining name, so "Technology
# Solutions Group" does not normalize to the empty string.
_DESCRIPTOR_SUFFIXES = {
    "HOLDINGS", "HOLDING", "GROUP", "SERVICES", "SERVICE", "SOLUTIONS",
    "TECHNOLOGIES", "TECHNOLOGY", "SYSTEMS", "LABS", "LABORATORIES",
    "PARTNERS", "VENTURES", "ENTERPRISES", "CONSULTING",
}


def employer_norm(name: str | None) -> str:
    """Normalize an employer name. Applied identically to both sides of a match."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = text.split()
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]

    def strip_repeatedly(toks: list[str], vocab: set[str]) -> list[str]:
        while len(toks) > 1 and toks[-1] in vocab:
            toks = toks[:-1]
        return toks

    tokens = strip_repeatedly(tokens, _LEGAL_SUFFIXES)
    tokens = strip_repeatedly(tokens, _GEO_SUFFIXES)
    # Descriptors are stripped ONCE and only when two tokens survive. Repeating
    # this turns "Technology Solutions Group" into "TECHNOLOGY", which then
    # fuzzy-matches half the DOL file.
    if len(tokens) > 2 and tokens[-1] in _DESCRIPTOR_SUFFIXES:
        tokens = tokens[:-1]
    tokens = strip_repeatedly(tokens, _LEGAL_SUFFIXES)
    return " ".join(tokens)


def company_norm(name: str | None) -> str:
    """Lowercase slug form of a company name, used in the dedupe key."""
    return employer_norm(name).lower()


# ---- Titles ---- #

_REQ_ID = re.compile(r"\b(?:r|req|jr|job)?[-_]?\d{4,}\b", re.I)
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_LEVEL_SUFFIX = re.compile(r"\b(i{1,3}|iv|[1-4])$", re.I)


def title_norm(title: str) -> str:
    text = _html.unescape(title or "").lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def title_level_penalty(title: str) -> tuple[int, str | None]:
    """Trailing level suffix. Level II is often 2-4 years and sometimes worth a
    shot, so this costs points rather than dropping the posting."""
    cleaned = _BRACKETED.sub(" ", title_norm(title))
    cleaned = re.sub(r"[,\-/|]+", " ", cleaned).strip()
    m = _LEVEL_SUFFIX.search(cleaned)
    if not m:
        return 0, None
    level = m.group(1).lower()
    if level in {"ii", "2"}:
        return -4, level.upper()
    if level in {"iii", "iv", "3", "4"}:
        return -8, level.upper()
    return 0, None


_TITLE_NOISE = {
    "remote", "hybrid", "onsite", "on", "site", "fulltime", "full", "time",
    "contract", "temporary", "us", "usa", "united", "states",
}


def title_dedupe_norm(title: str) -> str:
    """Token-sorted title, so two boards phrasing one role differently collide.

    "Analyst, Business Intelligence" and "Business Intelligence Analyst" are the
    common real case and they must produce the same string.
    """
    text = _BRACKETED.sub(" ", title_norm(title))
    text = _REQ_ID.sub(" ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t and t not in _TITLE_NOISE]
    while tokens and _LEVEL_SUFFIX.fullmatch(tokens[-1]):
        tokens = tokens[:-1]
    return " ".join(sorted(set(tokens)))


# ---- Locations ---- #

_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_CODES = set(_STATES.values())

# Bare city names, no state. Ashby boards routinely report just "San Francisco"
# or "Seattle", which without this list classify as unknown and score 6 of 15
# location points instead of the 15 they have earned.
_US_CITIES = frozenset("""
new york brooklyn manhattan|los angeles|chicago|houston|phoenix|philadelphia
san antonio|san diego|dallas|austin|jacksonville|fort worth|columbus|charlotte
indianapolis|san francisco|seattle|denver|nashville|washington|boston|el paso
las vegas|portland|detroit|louisville|memphis|baltimore|milwaukee|albuquerque
tucson|fresno|sacramento|mesa|kansas city|atlanta|omaha|colorado springs|raleigh
virginia beach|long beach|miami|oakland|minneapolis|tulsa|arlington|tampa
new orleans|cleveland|honolulu|anaheim|lexington|riverside|newark|saint paul
santa ana|cincinnati|irvine|orlando|pittsburgh|st louis|saint louis|greensboro
durham|plano|chandler|madison|buffalo|gilbert|reno|winston-salem|winston salem
chesapeake|garland|irving|scottsdale|boise|fremont|richmond|birmingham|spokane
rochester|des moines|tacoma|salt lake city|tallahassee|huntsville|knoxville
worcester|providence|chattanooga|fort lauderdale|tempe|sioux falls|springfield
naperville|syracuse|dayton|savannah|clarksville|fullerton|hampton|bellevue
new haven|cambridge|stamford|hartford|lafayette|ann arbor|berkeley|boulder
somerville|jersey city|hoboken|white plains|greenwich|mclean|reston|bethesda
rockville|silver spring|research triangle park|chapel hill|morrisville|cary
palo alto|menlo park|mountain view|cupertino|sunnyvale|santa clara|santa monica
culver city|redmond|kirkland|redwood city|san mateo|daly city|san jose
pasadena|burbank|glendale|alexandria|charleston|columbia|charlottesville
""".replace(chr(10), "|").split("|"))
_US_CITIES = frozenset(c.strip() for c in _US_CITIES if c.strip())

# Matched anywhere in the fragment, not just as the whole of it: "Remote (US)"
# and "Anywhere in the United States" are both common and both mean US.
_US_MARKER_RE = re.compile(r"(?<![a-z])(u\.?s\.?a?|united states|america)(?![a-z])", re.I)

# Non-US signals. Listed explicitly rather than "anything not recognized as US"
# because an unrecognized US suburb must not be read as foreign.
_FOREIGN = {
    "canada", "toronto", "vancouver", "montreal", "ottawa", "calgary",
    "united kingdom", "uk", "england", "london", "manchester", "edinburgh",
    "ireland", "dublin", "germany", "berlin", "munich", "hamburg",
    "france", "paris", "spain", "madrid", "barcelona", "portugal", "lisbon",
    "netherlands", "amsterdam", "belgium", "brussels", "switzerland", "zurich",
    "sweden", "stockholm", "norway", "oslo", "denmark", "copenhagen",
    "poland", "warsaw", "krakow", "czech", "prague", "romania", "bucharest",
    "italy", "milan", "rome", "austria", "vienna", "greece", "athens",
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi",
    "gurgaon", "gurugram", "noida", "chennai", "kolkata",
    "singapore", "japan", "tokyo", "china", "beijing", "shanghai", "shenzhen",
    "hong kong", "korea", "seoul", "taiwan", "taipei", "vietnam", "hanoi",
    "australia", "sydney", "melbourne", "new zealand", "auckland",
    "israel", "tel aviv", "united arab emirates", "dubai", "abu dhabi",
    "brazil", "sao paulo", "mexico", "mexico city", "guadalajara",
    "argentina", "buenos aires", "chile", "santiago", "colombia", "bogota",
    "south africa", "cape town", "johannesburg", "nigeria", "lagos",
    "kenya", "nairobi", "egypt", "cairo", "turkey", "istanbul",
    "philippines", "manila", "indonesia", "jakarta", "malaysia", "kuala lumpur",
    "thailand", "bangkok", "costa rica", "san jose costa rica",
}

# The rest of the world by name. The curated list above carries the cities that
# actually appear on these boards; this catches everything else. Without it a
# Bosch posting in "Sofia, Sofia City Province, Bulgaria" classifies as unknown,
# which is low confidence rather than foreign, and survives into the results.
_FOREIGN = _FOREIGN | frozenset("""
afghanistan albania algeria andorra angola armenia azerbaijan bahamas bahrain
bangladesh barbados belarus belize benin bhutan bolivia bosnia botswana brunei
bulgaria burundi cambodia cameroon chad chile colombia congo croatia cuba
cyprus czechia ecuador el-salvador estonia eswatini ethiopia fiji finland
gabon gambia georgia ghana guatemala guinea guyana haiti honduras hungary
iceland iran iraq jamaica jordan kazakhstan kosovo kuwait kyrgyzstan laos
latvia lebanon lesotho liberia libya liechtenstein lithuania luxembourg
macedonia madagascar malawi maldives mali malta mauritania mauritius moldova
monaco mongolia montenegro morocco mozambique myanmar namibia nepal nicaragua
niger oman pakistan palestine panama paraguay peru qatar russia rwanda
san-marino saudi senegal serbia seychelles slovakia slovenia somalia
sri-lanka sudan suriname syria tajikistan tanzania togo trinidad tunisia
turkmenistan uganda ukraine uruguay uzbekistan venezuela yemen zambia zimbabwe
scotland wales england
""".split())

# ISO 3166 alpha-2 codes, minus US. Several boards report location as
# "Wuxi, Jiangsu, cn". Without this the fragment classifies as "unknown", which
# is low confidence rather than foreign, and a whole non-US enterprise board
# leaks into the results looking like domestic roles. Checked only against the
# LAST comma-separated component, because US state codes are also two letters
# and "Charleston, SC, us" must stay domestic.
_ISO_COUNTRY = frozenset("""
ad ae af ag ai al am ao ar at au aw az ba bb bd be bf bg bh bi bj bm bn bo br
bs bt bw by bz ca cd cf cg ch ci cl cm cn co cr cu cv cy cz de dj dk dm do dz
ec ee eg er es et fi fj fm fr ga gb gd ge gh gm gn gq gr gt gw gy hk hn hr ht
hu id ie il in iq ir is it jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc
li lk lr ls lt lu lv ly ma mc md me mg mh mk ml mm mn mo mr mt mu mv mw mx my
mz na ne ng ni nl no np nr nz om pa pe pg ph pk pl pr pt py qa ro rs ru rw sa
sb sc sd se sg si sk sl sm sn so sr ss sv sy sz td tg th tj tl tm tn to tr tt
tv tw tz ua ug uy uz va vc ve vn vu ws ye za zm zw
""".split())

# Workday tenants often report "3 Locations" or "Multiple Locations". That is
# uninformative, not foreign, and must fall through to the description scan.
_UNINFORMATIVE = re.compile(r"^\d+\s+locations?$|^multiple locations?$|^various", re.I)

_REMOTE = re.compile(r"\bremote\b|\bwork from home\b|\banywhere\b|\bdistributed\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)


@dataclass(frozen=True)
class LocationParse:
    locations: tuple[str, ...]
    is_us: bool
    is_remote: bool
    is_hybrid: bool
    primary: str          # "city|ST", "remote-us", or "unknown"
    confidence: str       # high | low
    preferred: bool = False


def _split_parts(raw: str) -> list[str]:
    parts = re.split(r"\s*[;|]\s*|\s+/\s+|\s+and\s+|\s*•\s*", raw)
    return [p.strip() for p in parts if p and p.strip()]


def _classify(part: str) -> str:
    """Return 'us', 'foreign', or 'unknown' for one location fragment."""
    low = part.lower().strip().strip(",.")
    if not low or _UNINFORMATIVE.match(low):
        return "unknown"
    for marker in _FOREIGN:
        if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", low):
            return "foreign"
    tail = low.rsplit(",", 1)[-1].strip()
    if tail in _ISO_COUNTRY:
        return "foreign"
    if _US_MARKER_RE.search(low):
        return "us"
    for name, code in _STATES.items():
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            return "us"
        if re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])", part):
            return "us"
    head = low.split(",")[0].strip()
    if head in _US_CITIES:
        return "us"
    return "unknown"


def parse_locations(
    location_raw: str | None,
    extra: list[str] | None = None,
    description: str | None = None,
    preferred_cities: set[str] | None = None,
) -> LocationParse:
    candidates: list[str] = []
    for value in [location_raw or "", *(extra or [])]:
        candidates.extend(_split_parts(value))
    candidates = [c for c in candidates if c]
    joined = " ; ".join(candidates)

    is_remote = bool(_REMOTE.search(joined))
    is_hybrid = bool(_HYBRID.search(joined))

    verdicts = [_classify(c) for c in candidates]
    us_parts = [c for c, v in zip(candidates, verdicts) if v == "us"]
    has_us = bool(us_parts)
    has_foreign = "foreign" in verdicts
    confidence = "high"

    # Bare "Remote" with no country signal. Fall back to the description, which
    # usually says "Remote (US)" or names a state somewhere in the boilerplate.
    if not has_us and not has_foreign and is_remote and description:
        head = description[:4000]
        if re.search(r"\b(United States|U\.S\.|USA)\b", head) or re.search(
            r"\b(" + "|".join(sorted(_STATE_CODES)) + r")\b", head
        ):
            has_us = True
            confidence = "low"

    if not has_us and not has_foreign:
        confidence = "low"

    preferred = False
    primary = "unknown"
    if us_parts:
        first = us_parts[0]
        primary = _canonical_place(first)
        if preferred_cities:
            low = first.lower()
            preferred = any(city.lower() in low for city in preferred_cities)
    elif has_us and is_remote:
        primary = "remote-us"
    if is_remote and (primary == "unknown" or not us_parts) and has_us:
        primary = "remote-us"

    return LocationParse(
        locations=tuple(dict.fromkeys(candidates)),
        is_us=has_us,
        is_remote=is_remote,
        is_hybrid=is_hybrid,
        primary=primary,
        confidence=confidence,
        preferred=preferred,
    )


def _canonical_place(part: str) -> str:
    low = part.lower().strip()
    code = None
    for name, c in _STATES.items():
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            code = c
            break
    if code is None:
        m = re.search(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", part)
        if m and m.group(1) in _STATE_CODES:
            code = m.group(1)
    city = re.split(r"[,(]", part)[0].strip().lower()
    city = re.sub(r"\bremote\b|\bhybrid\b", "", city).strip(" -,")
    # A fragment that is only a country marker ("US-Remote", "Remote - United
    # States") has no city to canonicalize. Collapse it rather than emitting
    # "us" and "united states" as two different primary locations, which would
    # split one remote role into two dedupe keys.
    if _US_MARKER_RE.fullmatch(city.strip()):
        city = ""
    if not city:
        return f"remote-us" if code is None else f"remote-{code.lower()}"
    return f"{city}|{code}" if code else city
