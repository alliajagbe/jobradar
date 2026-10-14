"""The alumni outreach loop: parse a pasted list, enrich it, draft, log.

Direct applications stopped converting, so outreach became the main channel.
Finding people and tracking them already worked — the Outreach tab has pools, a
seven-day follow-up and per-pool reply rates. What was missing was the step in
between: writing twenty genuinely different notes a day, which by hand takes two
hours and is deadening, and is therefore the step that decides whether any of
this happens at all.

This module does the mechanical half. It never writes prose: a session does
that, because templated prose reads templated and this project has no API key
to call a model with. The split is deliberate — automate the boring half and
leave the half that needs judgment to a reader who can exercise it.

WHY THE VOLUME IS CAPPED. Twenty personal notes a day from a .edu address is
unremarkable; a few hundred near-identical ones in a sitting is a mail blast,
which Gmail flags, recipients recognise, and which risks a student account. The
alumni directory exists for networking, which is exactly this use; a mail-merge
to everyone in it is not. So nothing here makes the batch bigger. It makes each
note cheap.

PERSONAL DATA. A batch holds real names, employers and email addresses, and this
repository is public with four scheduled workflows that run `git add`. So every
path goes through resume.paths.resolve, which raises if a path would land inside
the repository tree. Same defence as the master resume, for the same reason.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

from . import config, contacts
from .resume.paths import resolve

# One follow-up, seven days out, matching the page's FOLLOWUP_DAYS. Changing it
# in one place and not the other would put the tracker and the drafts out of
# step on the only date either of them cares about.
FOLLOWUP_DAYS = 7

# The append-only record of who has been written to. The page's tracker lives in
# localStorage, which Python cannot read, so the tool keeps its own ledger and
# treats it as the answer to "have I already contacted this person".
LEDGER = ("outreach", "sent.csv")
LEDGER_HEAD = ["name", "email", "company", "sent", "batch"]


# The column contract. The sheet and this module have to agree on these exact
# names, because a header reading "Email Address" where the code looks for
# "email" leaves the row silently without an address — and a draft with no
# recipient looks like the tool working.
#
# Deliberately source-agnostic: the same names are read from a Google Sheet, a
# downloaded CSV or an .xlsx, so switching where the list lives is not a code
# change.
SHEET_COLUMNS = {
    # filled by Alli
    "first_name": "her",
    "last_name": "her",
    "job_title": "her",
    "company": "her",
    "email": "her",
    "grad_year": "her",
    "linkedin": "her",
    # filled by the flow
    "status": "flow",      # blank -> drafted -> sent -> replied / dead
    "sent": "flow",
    "followup": "flow",
    "sponsorship": "flow",
    "open_roles": "flow",
    "draft": "flow",
    # filled by Alli as replies arrive
    "reply": "her",
    "notes": "her",
}
HER_COLUMNS = [k for k, v in SHEET_COLUMNS.items() if v == "her"]
FLOW_COLUMNS = [k for k, v in SHEET_COLUMNS.items() if v == "flow"]


def _header_key(name: str) -> str:
    """A sheet heading to its contract name, forgiving the obvious variations.

    "First Name", "first name", "FIRST_NAME" and "Firstname" are all the same
    column. This is the one place that tolerance belongs: tolerating it per
    lookup would mean every caller gets it slightly differently.
    """
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


_COLUMN_BY_KEY = {_header_key(c): c for c in SHEET_COLUMNS}
# Headings a person would plausibly type for the same column.
_COLUMN_ALIASES = {
    "first": "first_name", "firstname": "first_name", "fname": "first_name",
    "last": "last_name", "lastname": "last_name", "surname": "last_name", "lname": "last_name",
    "name": "first_name", "fullname": "first_name",
    "title": "job_title", "jobtitle": "job_title", "role": "job_title", "position": "job_title",
    "organisation": "company", "organization": "company", "employer": "company", "org": "company",
    "emailaddress": "email", "mail": "email", "e": "email",
    "class": "grad_year", "year": "grad_year", "gradyear": "grad_year",
    "graduationyear": "grad_year", "classof": "grad_year",
    "linkedinurl": "linkedin", "profile": "linkedin",
    "followupdue": "followup", "followupdate": "followup",
    "datesent": "sent", "sentdate": "sent",
}


def column_for(heading: str) -> str:
    """The contract column a sheet heading means, or "" when it means none."""
    key = _header_key(heading)
    return _COLUMN_BY_KEY.get(key) or _COLUMN_ALIASES.get(key, "")


@dataclass
class Person:
    """One alum, as pasted and then as enriched."""
    name: str
    title: str = ""
    company: str = ""
    grad_year: str = ""
    email: str = ""
    location: str = ""
    # Kept separate from `name` because the greeting needs the first name
    # alone: "Hi Steve", not "Hi Steve Whitfield Blankenship".
    first_name: str = ""
    last_name: str = ""
    linkedin: str = ""
    row: int = 0                # 1-based sheet row, for writing status back
    # Filled by enrich(), never by the parser.
    open_roles: list = field(default_factory=list)
    certified: int | None = None
    analyst_certified: int | None = None
    skip: str = ""              # why this person was dropped, if they were

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def key(self) -> str:
        """Dedupe key. Email when there is one, else name plus employer.

        Name alone is too loose — two alumni share a name eventually — and
        email alone misses the LinkedIn-only people entirely.
        """
        if self.email:
            return self.email.strip().lower()
        return f"{self.name.strip().lower()}|{self.company.strip().lower()}"


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
# Lines a directory or LinkedIn page pastes in that carry no information.
# "Showing 1-20 of 312 results" and "Page 2 of 16" are the pagination forms
# that became contacts named "Showing 1-4 of 312 results" before they were
# matched here.
_NOISE = re.compile(
    r"^(?:\d+(?:st|nd|rd|th)(?:\s*degree)?|connect|message|follow|view profile|"
    r"shared connections?|·|•|\||see more|load more|results?|next|previous|"
    r"showing\b.*|page \d+(?:\s+of\s+\d+)?|\d+\s+results?(?:\s+found)?|"
    r"sort by\b.*|filters?|clear all)$", re.I)

# A pasted table leads with its column headings, and a heading row parses as a
# perfectly plausible person: the first real paste produced a contact called
# "Name" whose employer was "Company".
_HEADER_CELL = frozenset("""
name names fullname full first last firstname lastname title titles role roles
position job company companies employer organisation organization org
class year grad graduation email emails address contact phone location city
state country degree degrees program major industry function level
""".split())


def _is_header(cells: list) -> bool:
    """Whether a row is a table's heading rather than somebody in it.

    Judged on the row as a whole: a real person can be called Grace Position,
    but a row where every cell is a column label is a header. Requiring at
    least two cells keeps a one-word line from being thrown away.
    """
    if len([c for c in cells if c]) < 2:
        return False
    # Split each cell into words rather than stripping its spaces: "Grad Year"
    # collapsed to "gradyear", which is in no vocabulary anybody would think to
    # write down, so that heading sailed through as a person.
    for cell in cells:
        words = [w for w in re.split(r"[^a-z]+", cell.lower()) if w]
        if not words or any(w not in _HEADER_CELL for w in words):
            return False
    return True


def _split_role(blob: str) -> tuple:
    """"HR Data Analyst at ABB Americas" is one cell holding two fields.

    That is how a directory writes a role and how LinkedIn writes a headline.
    Split on the LAST " at ", because the employer is the trailing part and a
    title can contain the word ("Manager at Large at Acme").
    """
    text = (blob or "").strip()
    cut = text.lower().rfind(" at ")
    if cut < 1:
        return text, ""
    return text[:cut].strip(), text[cut + 4:].strip()


def _line_with_email(line: str) -> list | None:
    """One line holding a whole person, with the address as the anchor.

    "Steve Blankenship x@y.com HR Data Analyst at ABB Americas" is what a
    directory profile looks like once its segments are copied together, and it
    is the shape Alli actually had. Without this the whole line was read as
    somebody's name.
    """
    found = _EMAIL.search(line or "")
    if not found:
        return None
    before = line[:found.start()].strip().strip(",;|")
    after = line[found.end():].strip().lstrip(",;|-\u2013 ")
    if not before:
        return None
    title, company = _split_role(after)
    return [before, found.group(0)] + [c for c in (title, company) if c]


def _clean(cell: str) -> str:
    return " ".join(str(cell or "").replace(" ", " ").split()).strip(" ,;|")


def _row_to_person(cells: list) -> Person | None:
    """A delimited row, mapped by shape rather than by a fixed column order.

    Directory exports and copied tables disagree about column order, and
    guessing wrong silently swaps someone's employer for their job title. So
    each field is identified by what it looks like: an address is the one with
    an @, a graduation year is a bare four-digit year, and the first non-empty
    cell is the name because every format leads with it.
    """
    cells = [_clean(c) for c in cells]
    cells = [c for c in cells if c and not _NOISE.match(c)]
    if not cells or _is_header(cells):
        return None

    email = next((c for c in cells if _EMAIL.fullmatch(c)), "")
    if not email:
        # An address can also be embedded in a longer cell.
        for c in cells:
            found = _EMAIL.search(c)
            if found:
                email = found.group(0)
                break
    year = ""
    for c in cells:
        if _YEAR.fullmatch(c):
            year = c
            break
    rest = [c for c in cells
            if c != email and c != year and not (email and email in c)]
    if not rest:
        return None
    name = rest[0]
    # Of what is left, the longer cell is the job title and the shorter the
    # employer far more often than the reverse. When only one remains it is
    # treated as the employer: that is the field the enrichment needs, and a
    # wrong title costs a hook while a wrong employer costs a wrong fact.
    others = rest[1:]
    title = company = ""
    if len(others) == 1:
        company = others[0]
    elif len(others) >= 2:
        title, company = others[0], others[1]
    # A remaining field that reads "Role at Employer" is both fields.
    if not title and " at " in company.lower():
        title, company = _split_role(company)
    elif title and not company and " at " in title.lower():
        title, company = _split_role(title)
    return Person(name=name, title=title, company=company, grad_year=year, email=email)


def parse_paste(text: str) -> list:
    """People out of whatever a directory page pasted.

    Tolerant on purpose. A copied page arrives as tab-separated cells, as CSV,
    or as consecutive lines of name / title / company, and which one depends on
    the site and the browser. Guessing wrong is not a cosmetic problem: it
    mislabels a real person in a note they will read.

    Nothing is invented. A field that is not present stays empty, and the note
    then has one fewer hook rather than a fabricated one.
    """
    raw = [ln for ln in (text or "").splitlines()]
    kept = [ln for ln in raw if ln.strip() and not _NOISE.match(_clean(ln))]
    lines = [_clean(ln) for ln in kept]
    if not lines:
        return []

    # Delimited, if most lines carry the same delimiter. Checked against the
    # RAW lines: _clean collapses whitespace, so testing the cleaned ones made
    # every tab-separated paste look like prose and parse as nothing.
    #
    # A majority is what stops one stray comma in a job title from turning a
    # line-per-person paste into nonsense. The floor is 1, not 2: requiring two
    # delimited lines meant a one-person paste never took this path and was
    # read as a single cell, which is the shape of pasting one row.
    for delim in ("\t", ","):
        hits = [ln for ln in kept if delim in ln]
        if len(hits) >= max(1, len(kept) // 2):
            out = []
            for row in csv.reader(kept, delimiter=delim):
                person = _row_to_person(row)
                if person:
                    out.append(person)
            # Returned even when empty. A delimited paste that yields nobody
            # genuinely holds nobody — a heading row on its own, say — and
            # falling through to the line-per-field strategy re-read that
            # heading as a single cell and produced a contact named
            # "Name Title Company Class Email".
            return out

    # Otherwise: blocks separated by blank lines, or a fixed run of lines per
    # person. Blank-line separation is checked first because it is unambiguous.
    blocks, current = [], []
    for line in (_clean(l) for l in raw):
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        if not _NOISE.match(line):
            current.append(line)
    if current:
        blocks.append(current)

    if len(blocks) > 1:
        out = [p for p in (_row_to_person(b) for b in blocks) if p]
        if out:
            return out

    only = blocks[0] if blocks else lines

    # Every line carries an address: one person per line, the address marking
    # where their name ends and their role begins.
    with_email = [ln for ln in only if _EMAIL.search(ln)]
    if with_email and len(with_email) == len(only):
        out = [p for p in (_row_to_person(_line_with_email(ln) or [ln]) for ln in only) if p]
        if len(out) == len(only):
            return out

    # A single short block holding one profile. The repeating-run check below
    # needs two people to work from, so one profile alone fell through and
    # became one contact per line. Requires a person-shaped signal, because
    # three bare names are three people and not one person with three fields.
    if len(blocks) == 1 and 2 <= len(only) <= 4 and any(
            _EMAIL.search(ln) or " at " in ln.lower() for ln in only):
        person = _row_to_person(only)
        if person:
            return [person]

    # Otherwise a repeating run: three lines per person is the common shape.
    for size in (3, 2):
        if len(only) >= size * 2 and len(only) % size == 0:
            groups = [only[i:i + size] for i in range(0, len(only), size)]
            out = [p for p in (_row_to_person(g) for g in groups) if p]
            if len(out) == len(groups):
                return out
    return [p for p in (_row_to_person([line]) for line in only) if p]


def read_sheet(path) -> list:
    """People out of a spreadsheet, CSV or .xlsx, by the column contract.

    Source-agnostic on purpose: a Google Sheet read through the connector, a
    File > Download > CSV, and an .xlsx on the Desktop all arrive here as the
    same rows, so where the list lives is not a code change.

    `row` is recorded on each person so status can be written back to the right
    line later. It is 1-based and counts the header, matching what a
    spreadsheet shows and what the Sheets API expects.
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        rows = _rows_from_xlsx(path)
    else:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = [r for r in csv.reader(handle)]
    return rows_to_people(rows)


def _rows_from_xlsx(path) -> list:
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book[book.sheetnames[0]]
    out = []
    for row in sheet.iter_rows(values_only=True):
        out.append(["" if c is None else str(c) for c in row])
    book.close()
    return out


def rows_to_people(rows: list) -> list:
    """Rows (first one the header) into People, mapped by the column contract."""
    # Blank rows are NOT dropped before numbering. Collapsing them first
    # renumbered everybody below a gap, so a status written back landed one
    # line off — on the wrong person, silently.
    if not any(any(str(c).strip() for c in r) for r in rows):
        return []

    # Find the header rather than assuming row 1. People put a title or a note
    # above their headings, and the Sheets design rules positively encourage a
    # legend on a sheet meant to be filled in — either of which would otherwise
    # make the real header invisible and the first alum into column names.
    header, header_at = None, 0
    for offset, row in enumerate(rows[:8]):
        mapped = [column_for(c) for c in row]
        if sum(1 for h in mapped if h) >= 2 and any(
                h in ("first_name", "last_name", "email", "company") for h in mapped):
            header, header_at = mapped, offset
            break
    if header is None:
        raise ValueError(
            "no recognisable header row in the first eight rows. Expected columns "
            "named like " + ", ".join(HER_COLUMNS[:5])
        )

    people = []
    # `index` counts the spreadsheet's own rows, blanks included, so a
    # write-back lands on the line the reader sees.
    for index, raw in enumerate(rows[header_at + 1:], start=header_at + 2):
        if not any(str(c).strip() for c in raw):
            continue
        values = {}
        for column, cell in zip(header, raw):
            if column and str(cell).strip():
                values[column] = " ".join(str(cell).split())
        first = values.get("first_name", "")
        last = values.get("last_name", "")
        full = " ".join(part for part in (first, last) if part).strip()
        if not full and not values.get("email"):
            continue
        person = Person(
            name=full or values.get("email", ""),
            first_name=first, last_name=last,
            title=values.get("job_title", ""),
            company=values.get("company", ""),
            email=values.get("email", ""),
            grad_year=values.get("grad_year", ""),
            linkedin=values.get("linkedin", ""),
            row=index,
        )
        # A row already sent is not a candidate for drafting again. This is the
        # sheet's own answer to the dedupe question, and it beats the ledger
        # because it is the thing Alli actually looks at.
        status = (values.get("status") or "").strip().lower()
        if status in ("sent", "replied", "dead", "drafted"):
            person.skip = f"status is {status}"
        people.append(person)
    return people


def sheet_template() -> str:
    """The header row, as CSV, for creating the sheet with the right names."""
    return ",".join(SHEET_COLUMNS) + "\n"


# ---- enrichment ----

def _place(primary: str | None) -> str:
    """The store writes "austin|TX"; a person reads "Austin, TX"."""
    city, _, state = str(primary or "").partition("|")
    city = " ".join(w.capitalize() for w in city.split())
    return ", ".join(part for part in (city, state.upper()) if part)


def _feed_by_company() -> dict:
    """Open roles grouped by a normalised employer key.

    Built from the published feed rather than the store so it matches what the
    Jobs tab shows. Feed company names are sometimes ATS slugs (`usm2`,
    `cityofnewyork`), which is why a miss here is ordinary and is reported
    rather than guessed at.
    """
    try:
        jobs = json.loads(config.PUBLISH_JOBS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for job in jobs:
        key = contacts.simple_key(job.get("company") or "")
        if key:
            out.setdefault(key, []).append(job)
    return out


def _sponsor_index() -> dict:
    try:
        body = json.loads((config.DOCS_DATA_DIR / "sponsors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body.get("employers") or {}


def load_ledger() -> dict:
    """Everyone already written to, keyed the way Person.key is."""
    path = resolve(*LEDGER)
    if not path.exists():
        return {}
    out = {}
    try:
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                person = Person(name=row.get("name") or "",
                                email=row.get("email") or "",
                                company=row.get("company") or "")
                out[person.key] = row
    except (OSError, csv.Error):
        return {}
    return out


def load_page_backup() -> set:
    """Dedupe keys from the page's exported backup, when one is lying around.

    The tracker lives in localStorage, which Python cannot read, so an export
    is the only way to see contacts logged by hand on the page. Optional: a
    missing backup just means the ledger is the whole picture.
    """
    keys = set()
    try:
        backups = sorted(config.CONTACTS_DIR.glob("jobradar-backup-*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return keys
    for path in backups[:1]:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in (body.get("outreach") or {}).values():
            person = Person(name=entry.get("person") or "",
                            company=entry.get("org") or "")
            keys.add(person.key)
    return keys


def enrich(people: list, *, skip_contacted: bool = True) -> list:
    """Attach open roles and filing history, and mark anyone already contacted.

    The dedupe is the guard that matters most here. Two notes to one alum
    undoes the effort of both, and at twenty a day across weeks it is a matter
    of when rather than whether.
    """
    feed = _feed_by_company()
    sponsors = _sponsor_index()
    seen = load_ledger()
    backup_keys = load_page_backup()

    batch_keys: set = set()
    for person in people:
        # A reason already set upstream wins. The sheet's own status column is
        # what Alli looks at, so "status is sent" is a more useful answer than
        # the ledger's version of the same fact.
        if person.skip:
            continue
        # A duplicate inside one paste is a different problem from somebody
        # written to last week, and saying "already contacted" for the first
        # would be a plain lie about what happened.
        if person.key in batch_keys:
            person.skip = "listed twice in this batch"
            continue
        batch_keys.add(person.key)
        if skip_contacted and (person.key in seen or person.key in backup_keys):
            where = "ledger" if person.key in seen else "page backup"
            person.skip = f"already contacted (in the {where})"
            continue
        if not person.company:
            continue
        for variant in contacts.institution_variants(person.company):
            key = contacts.simple_key(variant)
            if key in feed and not person.open_roles:
                person.open_roles = [
                    {"title": j.get("title"), "url": j.get("url"),
                     "location": _place(j.get("location_primary"))}
                    for j in feed[key][:4]
                ]
            if key in sponsors and person.certified is None:
                person.certified, person.analyst_certified = sponsors[key]
    return people


# ---- where to go looking for alumni ----
#
# The bottleneck is not writing notes, it is generating names. WakeNetwork
# answers "what is this person's email" but not "who should I be writing to",
# and LinkedIn's alumni page answers the second only one filter at a time.
#
# So this ranks the employers worth filtering on: ones with an open role Alli
# would actually apply for AND a filing record showing they sponsor. That turns
# an unbounded search into a worklist, and the ranking is the opinion — an alum
# at a company with 229 certified analyst petitions and three open reqs is
# worth an hour; an alum at a company with neither is a pleasant conversation.

ALUMNI_SCHOOL_SLUG = "wake-forest-university"
ALUMNI_TOOL_URL = f"https://www.linkedin.com/school/{ALUMNI_SCHOOL_SLUG}/people/"
ALUMNI_SCHOOL_PHRASE = "Wake Forest"


def _plain_company(company: str) -> str:
    """The name a profile would actually say, for an exact-phrase search.

    Feed companies carry region and legal suffixes — "DoorDash USA",
    "AbbVie Inc.", "Amazon (United States)" — and quoting those as a phrase
    matches nobody, because a profile says "DoorDash". `institution_variants`
    already peels them for sponsorship matching, so reuse it and take the
    shortest, then drop the punctuation it leaves behind ("Gusto,").
    """
    bare = _PARENS_CO.sub("", company or "").strip()
    variants = contacts.institution_variants(bare) or [bare]
    return (variants[-1] or bare).strip(" ,.;")


def alumni_search(company: str) -> str:
    """LinkedIn people search for alumni at one employer.

    Not the alumni tool. `/school/<slug>/people/` cannot be deep-linked: it
    drops a `keywords` param it does not recognise and renders the unfiltered
    page, so a link into it is a link to nowhere. People search does honour a
    query string, and an exact-phrase school name keeps it to profiles that
    name Wake Forest. Noisier than the alumni tool's own employer filter, but
    it lands on names in one click, which is the whole point.

    Still a link, not a scrape: LinkedIn has no public search API and
    automating it would risk the account doing it. She clicks, reads the
    names, and looks each one up in WakeNetwork for an address.
    """
    from urllib.parse import quote_plus

    query = f'"{ALUMNI_SCHOOL_PHRASE}" "{_plain_company(company)}"'
    return (f"https://www.linkedin.com/search/results/people/"
            f"?keywords={quote_plus(query)}")


def alumni_search_google(company: str) -> str:
    """The same question asked of Google, as a second route.

    Profile pages are public, so `site:linkedin.com/in` returns name and title
    in the snippet without a login and without LinkedIn's search limits. Worth
    having because it fails differently: when LinkedIn throttles the search or
    the phrase match comes back thin, this one still answers.
    """
    from urllib.parse import quote_plus

    query = (f'site:linkedin.com/in "{ALUMNI_SCHOOL_PHRASE}" '
             f'"{_plain_company(company)}" (analyst OR analytics OR data)')
    return f"https://www.google.com/search?q={quote_plus(query)}"


_PARENS_CO = re.compile(r"\s*\([^)]*\)")


def target_companies(*, min_score: int = 55, min_certified: int = 1,
                     limit: int = 60) -> list:
    """Employers to search for alumni at, best first.

    Ranked by whether they clearly sponsor analysts, then by how many open
    roles they have. `min_score` is the page's own default threshold, so this
    agrees with what the Jobs tab shows rather than inventing a second
    standard.
    """
    try:
        jobs = json.loads(config.PUBLISH_JOBS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sponsors = _sponsor_index()

    by_company: dict = {}
    for job in jobs:
        if (job.get("score") or 0) < min_score:
            continue
        by_company.setdefault(job.get("company") or "", []).append(job)

    out = []
    for company, roles in by_company.items():
        if not company:
            continue
        stats = None
        for variant in contacts.institution_variants(company):
            stats = sponsors.get(contacts.simple_key(variant))
            if stats:
                break
        if not stats or stats[0] < min_certified:
            continue
        best = sorted(roles, key=lambda j: -(j.get("score") or 0))
        out.append({
            "company": company,
            "open_roles": len(roles),
            "best_score": best[0].get("score"),
            "certified": stats[0],
            "analyst_certified": stats[1],
            "sponsors_analysts": stats[1] >= config.SPONSOR_STRONG_ANALYST_SOC,
            "example_titles": "; ".join(j.get("title", "")[:48] for j in best[:2]),
            "linkedin_search": alumni_search(company),
            "google_search": alumni_search_google(company),
        })

    out.sort(key=lambda r: (not r["sponsors_analysts"], -r["analyst_certified"],
                            -r["open_roles"], -(r["best_score"] or 0)))
    return out[:limit]


TARGET_COLUMNS = ["company", "open_roles", "best_score", "certified",
                  "analyst_certified", "example_titles", "linkedin_search",
                  "google_search", "searched", "found"]


# ---- the batch file a session drafts into ----

_BATCH_HELP = """<!-- A batch to draft. One block per person.

Fill the `draft:` line under each person and nothing else. Keep each note under
120 words, plain text, one ask, and make the ask a short conversation rather
than a job. If somebody has no usable hook, leave their draft empty and say so
in `note:` — a generic message is worse than none.

Then: python -m jobradar outreach log --batch THIS_FILE
-->
"""


def write_batch(people: list, *, path: Path | None = None) -> Path:
    """The file a session drafts into. Under ~/.jobradar: it holds real names."""
    path = path or resolve("outreach", f"batch-{date.today().isoformat()}.md",
                           create_parent=True)
    lines = [_BATCH_HELP, f"# Outreach batch {date.today().isoformat()}", ""]
    drafting = [p for p in people if not p.skip]
    skipped = [p for p in people if p.skip]
    lines.append(f"{len(drafting)} to draft"
                 + (f", {len(skipped)} skipped" if skipped else ""))
    lines.append("")

    for person in drafting:
        lines.append(f"## {person.name}")
        for label, value in (("title", person.title), ("company", person.company),
                             ("class of", person.grad_year), ("email", person.email),
                             ("location", person.location)):
            if value:
                lines.append(f"- {label}: {value}")
        if person.certified is not None:
            lines.append(f"- sponsorship: {person.certified} certified, "
                         f"{person.analyst_certified} in analyst roles")
        elif person.company:
            lines.append("- sponsorship: no filing record found")
        if person.open_roles:
            lines.append("- open roles there now:")
            for role in person.open_roles:
                where = f" ({role['location']})" if role.get("location") else ""
                lines.append(f"    - {role['title']}{where}")
        elif person.company:
            lines.append("- open roles there now: none found in the feed")
        lines.append("")
        lines.append("draft: ")
        lines.append("note: ")
        lines.append("")

    if skipped:
        lines.append("## skipped")
        for person in skipped:
            lines.append(f"- {person.name}"
                         + (f" ({person.company})" if person.company else "")
                         + f" — {person.skip}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def read_batch(path: Path) -> list:
    """People and their drafts back out of the batch file."""
    text = Path(path).read_text(encoding="utf-8")
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        name = _clean(head)
        if not name or name.lower() == "skipped":
            continue
        fields = {}
        for label in ("title", "company", "email", "location"):
            found = re.search(rf"^- {label}: (.+)$", body, re.M)
            if found:
                fields[label] = _clean(found.group(1))
        year = re.search(r"^- class of: (.+)$", body, re.M)
        draft = re.search(r"^draft: ?(.*?)(?=^(?:note|draft): |\Z)", body, re.M | re.S)
        note = re.search(r"^note: ?(.*?)$", body, re.M)
        person = Person(name=name, grad_year=_clean(year.group(1)) if year else "",
                        **fields)
        # Recover the open role too. It is what the note referenced, and the
        # tracker's hook column means exactly that: the specific thing said.
        # Without this the logged row arrives with an empty hook and the reply
        # rates lose the one field that explains which approach worked.
        role = re.search(r"^    - (.+)$", body, re.M)
        if role:
            person.open_roles = [{"title": _clean(role.group(1)), "url": "",
                                  "location": ""}]
        spon = re.search(r"^- sponsorship: (\d+) certified, (\d+)", body, re.M)
        if spon:
            person.certified = int(spon.group(1))
            person.analyst_certified = int(spon.group(2))
        person.skip = "" if (draft and draft.group(1).strip()) else "no draft written"
        out.append((person, (draft.group(1).strip() if draft else ""),
                    _clean(note.group(1)) if note else ""))
    return out


def append_ledger(people: list, *, batch: str = "") -> int:
    """Record who was written to, so the next batch does not repeat them."""
    path = resolve(*LEDGER, create_parent=True)
    fresh = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if fresh:
            writer.writerow(LEDGER_HEAD)
        for person in people:
            writer.writerow([person.name, person.email, person.company,
                             date.today().isoformat(), batch])
    return len(people)


def to_candidates_csv(people: list, path: Path) -> int:
    """The batch in the column shape the page's CSV import already parses.

    Reuses contacts.CSV_HEAD rather than inventing a second format, so loading
    a batch into the Outreach tab needs no new import path on the page.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(contacts.CSV_HEAD)
        for person in people:
            org = person.company or ""
            # The open req is the sharpest hook, their own role the next best.
            # An empty hook column loses the one field that later explains
            # which approach drew a reply.
            hook = (person.open_roles[0]["title"] if person.open_roles
                    else person.title or "")
            writer.writerow([
                f"wfu|{person.key}", person.name, person.title, org,
                "", "", "alumni",
                hook, person.email and ("mailto:" + person.email) or "",
                date.today().isoformat(),
                person.certified if person.certified is not None else "",
                person.analyst_certified if person.analyst_certified is not None else "",
                "", _linkedin_search(person.name, org), "", "",
            ])
    return len(people)


def _linkedin_search(name: str, org: str) -> str:
    """A prefilled people search, matching Contact.linkedin_search.

    LinkedIn has no public search API and scraping it would risk the account
    doing the scraping, so this is the query she would type, one click instead
    of three.
    """
    from urllib.parse import quote_plus
    plain = re.sub(r"\s*\([^)]*\)", "", org or "").strip()
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote_plus(f"{name} {plain}".strip()))


def followup_date(sent: str | None = None) -> str:
    base = date.fromisoformat(sent) if sent else date.today()
    return (base + timedelta(days=FOLLOWUP_DAYS)).isoformat()
