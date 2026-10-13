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
