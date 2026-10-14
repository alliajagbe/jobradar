"""Tests for the alumni outreach loop.

The parser tests carry most of the weight. A paste arrives in whatever shape a
directory page and a browser agreed on, and getting it wrong is not cosmetic:
it mislabels a real person in a note they are about to read.
"""

from __future__ import annotations

import csv

import pytest

from jobradar import outreach as O


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ledger. It is a record of who has been written to."""
    monkeypatch.setattr(O, "resolve", lambda *parts, create_parent=False: (
        tmp_path.joinpath(*parts).parent.mkdir(parents=True, exist_ok=True)
        or tmp_path.joinpath(*parts)))
    monkeypatch.setattr(O, "load_page_backup", lambda: set())
    return tmp_path


ROWS = [("Jane Okafor", "Senior Data Analyst", "Capital One", "2021", "jane@example.com"),
        ("Marcus Webb", "Director of Analytics", "Applied Materials", "2016", "mw@example.com")]


# ---- the parser ----

def test_tab_separated_paste():
    text = "\n".join("\t".join(r) for r in ROWS)
    people = O.parse_paste(text)
    assert [p.name for p in people] == ["Jane Okafor", "Marcus Webb"]
    assert people[0].company == "Capital One"
    assert people[0].email == "jane@example.com"
    assert people[0].grad_year == "2021"


def test_comma_separated_paste_gives_the_same_rows():
    tsv = O.parse_paste("\n".join("\t".join(r) for r in ROWS))
    csvp = O.parse_paste("\n".join(",".join(r) for r in ROWS))
    assert [p.as_dict() for p in tsv] == [p.as_dict() for p in csvp]


def test_tabs_survive_the_cleanup():
    """Delimiters are detected on the raw lines for a reason.

    `_clean` collapses all whitespace, so checking the cleaned lines turned
    every tab-separated paste into prose and parsed it as nothing.
    """
    assert len(O.parse_paste("A Name\tAnalyst\tAcme\nB Name\tAnalyst\tAcme")) == 2


def test_one_field_per_line():
    text = "\n".join(f"{n}\n{t}\n{c}" for n, t, c, _, _ in ROWS)
    people = O.parse_paste(text)
    assert [p.name for p in people] == ["Jane Okafor", "Marcus Webb"]
    assert people[1].company == "Applied Materials"


def test_blocks_separated_by_blank_lines_with_linkedin_noise():
    text = ("Jane Okafor\nSenior Data Analyst\nCapital One\n2nd degree\nConnect\n\n"
            "Marcus Webb\nDirector of Analytics\nApplied Materials\nMessage\n")
    people = O.parse_paste(text)
    assert [p.name for p in people] == ["Jane Okafor", "Marcus Webb"]
    assert people[0].title == "Senior Data Analyst"


def test_a_bare_year_is_not_noise():
    """The pattern that strips "2nd degree" also ate every graduation year
    until it was made to require the ordinal suffix."""
    assert O.parse_paste("Jane Okafor,Analyst,Acme,2021")[0].grad_year == "2021"


def test_a_paste_with_no_addresses_still_parses():
    """An empty email was used as a substring filter, and "" is in every
    string, so this returned nobody at all."""
    people = O.parse_paste("Jane Okafor\tAnalyst\tAcme\nMarcus Webb\tAnalyst\tAcme")
    assert len(people) == 2
    assert all(p.email == "" for p in people)


def test_a_missing_company_stays_empty():
    """Never invented. One fewer hook beats a fabricated fact in a real note."""
    assert O.parse_paste("Solo Person")[0].company == ""


@pytest.mark.parametrize("text", ["", "\n\n   \n", "Connect\nMessage\n2nd degree"])
def test_nothing_useful_yields_nothing(text):
    assert O.parse_paste(text) == []


def test_an_embedded_address_is_found():
    people = O.parse_paste("Jane Okafor,Analyst,Acme,<jane@example.com>")
    assert people[0].email == "jane@example.com"


# ---- the dedupe ----

def test_someone_in_the_ledger_is_skipped():
    """The guard that matters most: two notes to one alum undoes both."""
    people = O.parse_paste("\t".join(ROWS[0]))
    O.append_ledger(people, batch="earlier")
    again = O.enrich(O.parse_paste("\t".join(ROWS[0])))
    assert "already contacted" in again[0].skip


def test_a_duplicate_inside_one_batch_says_so_instead():
    """Not the same thing as having been contacted, and saying so would be a
    plain lie about what happened."""
    text = "\n".join(["\t".join(ROWS[0]), "\t".join(ROWS[1]), "\t".join(ROWS[0])])
    people = O.enrich(O.parse_paste(text))
    assert people[0].skip == ""
    assert people[2].skip == "listed twice in this batch"


def test_dedupe_can_be_overridden():
    O.append_ledger(O.parse_paste("\t".join(ROWS[0])), batch="earlier")
    again = O.enrich(O.parse_paste("\t".join(ROWS[0])), skip_contacted=False)
    assert again[0].skip == ""


def test_the_key_prefers_the_address():
    assert O.Person(name="A", email="X@Example.com ").key == "x@example.com"
    assert O.Person(name="Jane Okafor", company="Acme").key == "jane okafor|acme"


def test_the_same_person_at_a_new_employer_is_still_the_same_person():
    O.append_ledger([O.Person(name="Jane", email="jane@example.com", company="Old")],
                    batch="earlier")
    moved = O.enrich([O.Person(name="Jane", email="jane@example.com", company="New")])
    assert "already contacted" in moved[0].skip


# ---- the batch file ----

def test_the_batch_states_a_miss_rather_than_guessing():
    """288 of 972 feed companies have a filing record, so a miss is ordinary
    and has to read as a miss."""
    people = [O.Person(name="Nobody Known", company="A Firm Nobody Has Heard Of")]
    path = O.write_batch(people)
    text = path.read_text()
    assert "no filing record found" in text
    assert "none found in the feed" in text


def test_the_batch_round_trips_a_draft():
    path = O.write_batch(O.parse_paste("\t".join(ROWS[0])))
    text = path.read_text().replace("draft: ", "draft: Hello Jane, a real note.", 1)
    path.write_text(text)
    rows = O.read_batch(path)
    assert len(rows) == 1
    person, draft, _ = rows[0]
    assert person.name == "Jane Okafor"
    assert person.email == "jane@example.com"
    assert draft == "Hello Jane, a real note."


def test_an_undrafted_person_is_reported_not_logged():
    path = O.write_batch(O.parse_paste("\t".join(ROWS[0])))
    person, draft, _ = O.read_batch(path)[0]
    assert draft == ""
    assert person.skip == "no draft written"


def test_skipped_people_are_listed_but_not_drafted():
    person = O.Person(name="Already Done", company="Acme", skip="already contacted")
    text = O.write_batch([person]).read_text()
    assert "## skipped" in text
    assert "already contacted" in text
    assert O.read_batch(O.write_batch([person])) == []


def test_the_batch_never_lands_in_the_repository():
    """The repo is public and four workflows run `git add` on a schedule. A
    batch holds real names, employers and addresses."""
    from jobradar import config
    path = O.write_batch([O.Person(name="Jane")])
    assert config.ROOT.resolve() not in path.resolve().parents


# ---- handing the batch to the page ----

def test_the_csv_uses_the_column_shape_the_page_already_parses(tmp_path):
    from jobradar import contacts
    people = O.enrich(O.parse_paste("\t".join(ROWS[0])))
    out = tmp_path / "outreach.csv"
    assert O.to_candidates_csv(people, out) == 1
    rows = list(csv.DictReader(out.open()))
    assert list(rows[0].keys()) == contacts.CSV_HEAD
    assert rows[0]["Person"] == "Jane Okafor"
    assert rows[0]["Pool"] == "alumni"
    assert rows[0]["Link"] == "mailto:jane@example.com"
    assert "linkedin.com/search" in rows[0]["LinkedIn"]


def test_the_linkedin_query_drops_a_parenthetical_suffix():
    url = O._linkedin_search("Jane Okafor", "Amazon (United States)")
    assert "United" not in url
    assert "Jane+Okafor+Amazon" in url


def test_the_followup_is_seven_days_out():
    assert O.followup_date("2026-10-13") == "2026-10-20"
    assert O.FOLLOWUP_DAYS == 7


def test_place_is_rendered_for_a_person_not_a_machine():
    assert O._place("austin|TX") == "Austin, TX"
    assert O._place("remote|") == "Remote"
    assert O._place(None) == ""


def test_the_batch_gives_back_the_open_role_it_offered():
    """The hook column means "the specific thing you referenced".

    read_batch rebuilt the person from the file's own fields and dropped the
    open role, so every logged row arrived with an empty hook and the per-pool
    reply rates lost the field that explains which approach worked.
    """
    person = O.Person(name="Jane Okafor", company="Acme", certified=531,
                      analyst_certified=60,
                      open_roles=[{"title": "Finance Analyst", "location": "Austin, TX",
                                   "url": ""}])
    path = O.write_batch([person])
    path.write_text(path.read_text().replace("draft: ", "draft: a real note", 1))
    back, draft, _ = O.read_batch(path)[0]
    assert draft == "a real note"
    assert back.open_roles[0]["title"].startswith("Finance Analyst")
    assert back.certified == 531
    assert back.analyst_certified == 60


def test_the_hook_falls_back_to_their_own_role(tmp_path):
    """Plenty of employers have a filing record and no open req in the feed."""
    person = O.Person(name="Omar Reed", title="Manager, BI", company="Capital One",
                      certified=1584, analyst_certified=972)
    out = tmp_path / "o.csv"
    O.to_candidates_csv([person], out)
    row = next(csv.DictReader(out.open()))
    assert row["Hook"] == "Manager, BI"
    assert row["Certified filings"] == "1584"


# ---- what a real copied page drags in ----

MESSY = """Name\tTitle\tCompany\tClass\tEmail
Showing 1-4 of 312 results

Jane Okafor\tSenior Data Analyst\tCapital One\t2021\tjokafor@example.com
2nd degree
Connect

Marcus Webb\tDirector, Analytics\tApplied Materials\t2016\tmwebb@example.com
Message
Load more
"""


def test_a_copied_table_yields_only_the_people():
    """The first real paste produced six contacts from four people.

    A heading row parses as a perfectly plausible person — the batch had a
    contact called "Name" whose employer was "Company" — and so does
    "Showing 1-4 of 312 results".
    """
    people = O.parse_paste(MESSY)
    assert [p.name for p in people] == ["Jane Okafor", "Marcus Webb"]
    assert people[0].company == "Capital One"
    assert people[0].email == "jokafor@example.com"


@pytest.mark.parametrize("row", [
    ["Name", "Title", "Company", "Class", "Email"],
    ["Full Name", "Position", "Employer", "Grad Year"],
    ["first", "last", "organization", "location"],
])
def test_a_heading_row_is_not_a_person(row):
    assert O.parse_paste("\t".join(row)) == []


@pytest.mark.parametrize("line", [
    "Showing 1-20 of 312 results", "Page 2 of 16", "312 results",
    "48 results found", "Sort by: relevance", "Clear all", "Next", "Previous",
])
def test_pagination_and_controls_are_not_people(line):
    assert O.parse_paste(line) == []


def test_a_person_whose_name_matches_a_column_word_survives():
    """A header is judged on the whole row, not on one cell. Somebody really
    can be called Grace Price, and throwing her away would be worse than
    keeping a stray heading."""
    people = O.parse_paste("Grace Price\tAnalyst\tAcme Corp")
    assert [p.name for p in people] == ["Grace Price"]


def test_a_single_heading_word_is_kept():
    """One word is not enough to call a row a header, and discarding it would
    silently drop a one-word name."""
    assert [p.name for p in O.parse_paste("Name")] == ["Name"]


# ---- the shape the directory actually produces ----
#
# Alli's real paste was a single line per person with the address in the
# middle: "Steve Whitfield Blankenship deaconfan90@gmail.com HR Data Analyst at
# ABB Americas". The parser read the entire line as somebody's name, which is
# the worst possible failure here — that name goes into a note they read.

HER_LINE = ("Steve Whitfield Blankenship deaconfan90@gmail.com "
            "HR Data Analyst at ABB Americas")


def test_one_line_per_person_with_the_address_in_the_middle():
    person = O.parse_paste(HER_LINE)[0]
    assert person.name == "Steve Whitfield Blankenship"
    assert person.email == "deaconfan90@gmail.com"
    assert person.title == "HR Data Analyst"
    assert person.company == "ABB Americas"


def test_several_of_those_lines():
    text = HER_LINE + "\nJane Okafor jane@example.com Senior Analyst at Capital One"
    people = O.parse_paste(text)
    assert [p.name for p in people] == ["Steve Whitfield Blankenship", "Jane Okafor"]
    assert people[1].company == "Capital One"


@pytest.mark.parametrize("blob,title,company", [
    ("HR Data Analyst at ABB Americas", "HR Data Analyst", "ABB Americas"),
    ("Senior Analyst, Risk at Capital One", "Senior Analyst, Risk", "Capital One"),
    # The last " at " wins: the employer is the trailing part, and a title can
    # contain the word itself.
    ("Manager at Large at Acme", "Manager at Large", "Acme"),
    ("Consultant", "Consultant", ""),
    ("", "", ""),
])
def test_a_role_and_an_employer_in_one_cell(blob, title, company):
    assert O._split_role(blob) == (title, company)


def test_a_role_cell_is_split_even_without_an_address():
    person = O.parse_paste("Jane Okafor\nSenior Data Analyst at Capital One")[0]
    assert person.name == "Jane Okafor"
    assert person.title == "Senior Data Analyst"
    assert person.company == "Capital One"


def test_a_single_profile_block_is_one_person():
    """The repeating-run check needs two people to work from, so one profile on
    its own fell through and became one contact per line."""
    people = O.parse_paste("Jane Okafor\nSenior Data Analyst at Capital One\njane@example.com")
    assert len(people) == 1
    assert people[0].email == "jane@example.com"
    assert people[0].company == "Capital One"


def test_bare_names_stay_separate_people():
    """The guard on the single-block rule. Three names in a row are three
    people, not one person with a name, a title and an employer."""
    people = O.parse_paste("Jane Okafor\nMarcus Webb\nPriya Rao")
    assert [p.name for p in people] == ["Jane Okafor", "Marcus Webb", "Priya Rao"]
    assert all(p.company == "" for p in people)


def test_two_profile_blocks():
    text = ("Jane Okafor\nAnalyst at Capital One\njane@example.com\n\n"
            "Bo Lee\nAnalyst at ABB\nbo@example.com")
    people = O.parse_paste(text)
    assert [p.name for p in people] == ["Jane Okafor", "Bo Lee"]
    assert [p.company for p in people] == ["Capital One", "ABB"]


def test_a_name_is_never_the_whole_line():
    """The failure that prompted this: everything in one cell, as a name."""
    for text in [HER_LINE, "Jane Okafor jane@example.com Analyst at Acme"]:
        assert "@" not in O.parse_paste(text)[0].name
        assert " at " not in O.parse_paste(text)[0].name


# ---- the spreadsheet contract ----

SHEET = [
    ["First Name", "Last Name", "Job Title", "Company", "Email", "Class of", "status"],
    ["Steve", "Whitfield Blankenship", "HR Data Analyst", "ABB Americas",
     "deaconfan90@gmail.com", "2012", "sent"],
    ["Jane", "Okafor", "Senior Data Analyst", "Capital One", "jane@example.com", "2021", ""],
]


def _write_sheet(tmp_path, rows=None):
    path = tmp_path / "alumni.csv"
    with path.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows or SHEET)
    return path


@pytest.mark.parametrize("heading,column", [
    ("First Name", "first_name"), ("first_name", "first_name"),
    ("FIRST_NAME", "first_name"), ("Firstname", "first_name"),
    ("Email Address", "email"), ("E-Mail", "email"),
    ("Job Title", "job_title"), ("Role", "job_title"),
    ("Employer", "company"), ("Organisation", "company"),
    ("Class of", "grad_year"), ("Graduation Year", "grad_year"),
    ("Follow-up due", "followup"), ("Date Sent", "sent"),
])
def test_headings_map_to_the_contract(heading, column):
    """A header reading "Email Address" where the code wants "email" leaves the
    row silently without an address, and a draft with no recipient looks like
    the tool working."""
    assert O.column_for(heading) == column


def test_an_unknown_heading_is_ignored_not_guessed():
    assert O.column_for("Random Notes Column 7") == ""


def test_the_first_name_is_kept_separate():
    """The greeting needs it alone: "Hi Steve", not the full three names."""
    people = O.rows_to_people(SHEET)
    assert people[0].first_name == "Steve"
    assert people[0].last_name == "Whitfield Blankenship"
    assert people[0].name == "Steve Whitfield Blankenship"


def test_a_sheet_records_the_row_for_writing_back():
    """1-based and counting the header, matching what a spreadsheet shows."""
    people = O.rows_to_people(SHEET)
    assert [p.row for p in people] == [2, 3]


def test_a_row_already_sent_is_not_drafted_again():
    people = O.rows_to_people(SHEET)
    assert people[0].skip == "status is sent"
    assert people[1].skip == ""


@pytest.mark.parametrize("status", ["sent", "Sent", "REPLIED", "dead", "drafted"])
def test_every_handled_status_is_respected(status):
    rows = [SHEET[0], SHEET[1][:6] + [status]]
    assert O.rows_to_people(rows)[0].skip == f"status is {status.lower()}"


def test_the_sheet_status_wins_over_the_ledger(tmp_path):
    """Both are true; the sheet's own column is what she looks at."""
    O.append_ledger([O.Person(name="Steve Whitfield Blankenship",
                              email="deaconfan90@gmail.com", company="ABB Americas")],
                    batch="earlier")
    people = O.enrich(O.rows_to_people(SHEET))
    assert people[0].skip == "status is sent"


def test_a_csv_round_trips(tmp_path):
    people = O.read_sheet(_write_sheet(tmp_path))
    assert [p.first_name for p in people] == ["Steve", "Jane"]
    assert people[1].company == "Capital One"


def test_an_xlsx_reads_the_same_as_a_csv(tmp_path):
    """Whether the list arrives as a download or a file is not a code change."""
    from openpyxl import Workbook

    book = Workbook()
    for row in SHEET:
        book.active.append(row)
    path = tmp_path / "alumni.xlsx"
    book.save(path)
    from_xlsx = O.read_sheet(path)
    from_csv = O.read_sheet(_write_sheet(tmp_path))
    assert [p.as_dict() for p in from_xlsx] == [p.as_dict() for p in from_csv]


def test_a_sheet_with_no_recognisable_header_is_refused(tmp_path):
    """Better than reading the first person as column names."""
    rows = [["Steve", "deaconfan90@gmail.com"], ["Jane", "jane@example.com"]]
    with pytest.raises(ValueError, match="no recognisable header"):
        O.rows_to_people(rows)


def test_blank_rows_are_skipped():
    rows = [SHEET[0], [""] * 7, SHEET[1], ["", "", "", "", "", "", ""]]
    assert len(O.rows_to_people(rows)) == 1


def test_a_row_with_only_an_email_still_counts():
    """Somebody worth writing to with no name yet is still a row."""
    rows = [SHEET[0], ["", "", "", "Acme", "someone@example.com", "", ""]]
    people = O.rows_to_people(rows)
    assert len(people) == 1
    assert people[0].name == "someone@example.com"


def test_the_template_names_every_column_in_order():
    head = O.sheet_template().strip().split(",")
    assert head == list(O.SHEET_COLUMNS)
    assert head[:5] == ["first_name", "last_name", "job_title", "company", "email"]


def test_the_header_need_not_be_row_one():
    """People put a title or a note above their headings, and the Sheets design
    rules positively encourage a legend on a sheet meant to be filled in."""
    rows = [["Alumni outreach — add rows below."], [],
            ["First Name", "Last Name", "Company", "Email"],
            ["Jane", "Okafor", "Capital One", "jane@example.com"]]
    people = O.rows_to_people(rows)
    assert [p.first_name for p in people] == ["Jane"]


def test_a_blank_row_does_not_renumber_the_people_below_it():
    """Dropping blanks before numbering shifted everybody below a gap, so a
    status written back landed one line off, on the wrong person, silently."""
    rows = [["First Name", "Company", "Email"],
            ["Jane", "Capital One", "jane@example.com"],
            [],
            ["Omar", "Applied Materials", "omar@example.com"]]
    people = O.rows_to_people(rows)
    # Spreadsheet rows: 1 header, 2 Jane, 3 blank, 4 Omar.
    assert [(p.first_name, p.row) for p in people] == [("Jane", 2), ("Omar", 4)]


def test_rows_are_numbered_as_the_spreadsheet_shows_them():
    rows = [["note"], [], ["First Name", "Email"], ["Jane", "jane@example.com"]]
    assert O.rows_to_people(rows)[0].row == 4


# ---- where to go looking for alumni ----
#
# The bottleneck was never writing notes, it was generating names. WakeNetwork
# answers "what is this person's address" but not "who should I write to", so
# this ranks the employers worth filtering LinkedIn's alumni tool on.

def test_the_alumni_search_is_a_link_not_a_scrape():
    """LinkedIn has no public search API, and automating it would risk the
    account doing it. This is their own tool, used as intended."""
    url = O.alumni_search("Capital One")
    assert url.startswith("https://www.linkedin.com/school/wake-forest-university/people/")
    assert "keywords=Capital+One" in url


def test_the_country_suffix_is_dropped_from_the_search():
    assert "United" not in O.alumni_search("Amazon (United States)")


def test_targets_are_ranked_by_whether_they_sponsor_analysts(monkeypatch, tmp_path):
    """An alum at a company with 229 certified analyst petitions and three open
    reqs is worth an hour. An alum at a company with neither is a pleasant
    conversation. The ranking is that opinion."""
    import json as _json
    from jobradar import config

    jobs = tmp_path / "jobs.json"
    jobs.write_text(_json.dumps([
        {"company": "Strong Co", "title": "Data Analyst", "score": 80},
        {"company": "Strong Co", "title": "Data Scientist", "score": 70},
        {"company": "Weak Co", "title": "Data Analyst", "score": 90},
        {"company": "No Record Co", "title": "Data Analyst", "score": 95},
        {"company": "Low Score Co", "title": "Janitor", "score": 20},
    ]))
    monkeypatch.setattr(config, "PUBLISH_JOBS_JSON", jobs)
    monkeypatch.setattr(O, "_sponsor_index", lambda: {
        "STRONG CO": [500, 60], "WEAK CO": [8, 0], "LOW SCORE CO": [900, 90],
    })

    rows = O.target_companies()
    names = [r["company"] for r in rows]
    assert names[0] == "Strong Co"               # sponsors analysts, two reqs
    assert "Weak Co" in names                    # has a record, ranked below
    assert "No Record Co" not in names           # nothing to say about sponsorship
    assert "Low Score Co" not in names           # no role she would apply for
    assert rows[0]["open_roles"] == 2
    assert rows[0]["sponsors_analysts"] is True
    assert "linkedin.com/school" in rows[0]["alumni_search"]


def test_a_target_row_carries_what_the_search_needs(monkeypatch, tmp_path):
    import json as _json
    from jobradar import config

    jobs = tmp_path / "jobs.json"
    jobs.write_text(_json.dumps([
        {"company": "Strong Co", "title": "Senior Data Analyst", "score": 80}]))
    monkeypatch.setattr(config, "PUBLISH_JOBS_JSON", jobs)
    monkeypatch.setattr(O, "_sponsor_index", lambda: {"STRONG CO": [500, 60]})
    row = O.target_companies()[0]
    assert set(O.TARGET_COLUMNS) >= set(row) - {"sponsors_analysts"}
    assert row["example_titles"].startswith("Senior Data Analyst")


def test_no_feed_means_no_targets_rather_than_a_crash(monkeypatch, tmp_path):
    from jobradar import config
    monkeypatch.setattr(config, "PUBLISH_JOBS_JSON", tmp_path / "missing.json")
    assert O.target_companies() == []
