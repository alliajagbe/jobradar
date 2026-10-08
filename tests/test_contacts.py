"""Tests for the contact finder.

No network. The OpenAlex half is driven from a fixture payload, and the
employer half from a small hand-built table, so every case names the specific
way the join goes wrong rather than asserting on live data that moves.
"""

from __future__ import annotations

import pytest

from jobradar import config, contacts, sponsorship
from jobradar.matching import Matcher
from jobradar.sponsorship import EmployerStats


def _emp(display, certified, analyst=0):
    from jobradar.normalize import employer_norm
    return EmployerStats(norm_name=employer_norm(display), display_name=display,
                         certified=certified, analyst_certified=analyst,
                         denied=0, last_decision="2026-01-01")


@pytest.fixture
def employers():
    rows = [
        _emp("WASHINGTON UNIVERSITY", 492, 15),
        _emp("WASHINGTON UNIVERSITY IN ST LOUIS", 14, 0),
        _emp("UNIVERSITY OF WASHINGTON", 197, 1),
        _emp("DUKE UNIVERSITY", 234, 24),
        _emp("DUKE UNIVERSITY HEALTH SYSTEM", 31, 0),
        # Digits are stripped by entity_stem, so this one's stem is the single
        # token DUKE. It must not be folded into the university.
        _emp("DUKE 65", 2, 0),
        _emp("UT SOUTHWESTERN MEDICAL CENTER", 398, 19),
        _emp("UNIVERSITY OF TEXAS SOUTHWESTERN MEDICAL CENTER", 1, 0),
        _emp("BOSTON MEDICAL CENTER", 62, 23),
        _emp("CINCINNATI CHILDRENS HOSPITAL MEDICAL CENTER", 171, 15),
        # Penn's real filing entity, plus two of the seven unrelated
        # Pennsylvania state schools that share its last three words.
        _emp("TRUSTEES OF THE UNIVERSITY OF PENNSYLVANIA", 480, 43),
        _emp("UNIVERSITY OF PENNSYLVANIA", 1, 0),
        _emp("KUTZTOWN UNIVERSITY OF PENNSYLVANIA", 14, 2),
        _emp("WEST CHESTER UNIVERSITY OF PENNSYLVANIA", 12, 1),
        _emp("JOHNS HOPKINS UNIVERSITY", 524, 27),
        _emp("JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS LAB", 6, 0),
        _emp("AMAZON", 16793, 2767),
        # Industry is the primary interest, so the fixture carries a mid-size
        # company that clearly sponsors analysts and a small one that barely has.
        _emp("MIDSIZE ANALYTICS", 120, 30),
        _emp("TINY STARTUP", 2, 0),
        _emp("RAND CORPORATION", 60, 8),
    ]
    return {r.norm_name: r for r in rows}


@pytest.fixture
def matcher(employers):
    return Matcher(employers)


# ---- institution resolution ----

def test_sibling_spellings_are_summed(employers, matcher):
    """The undercount this exists to fix: the long spelling held 14 of 506."""
    stats, names = contacts.resolve_institution(
        "Washington University in St. Louis", matcher, employers)
    assert stats.certified == 506
    assert len(names) == 2


def test_a_different_university_with_the_same_words_is_not_merged(employers, matcher):
    """Washington University and University of Washington are 2,000 miles apart.

    A token-set match would have merged them and reported one number for both,
    which is why the rule is an ordered affix.
    """
    stats, names = contacts.resolve_institution("University of Washington", matcher, employers)
    assert stats.certified == 197
    assert all("WASHINGTON UNIVERSITY" not in n for n in names)


def test_a_one_token_stem_does_not_swallow_the_university(employers, matcher):
    """`entity_stem` drops digits, so "DUKE 65" reduces to DUKE alone.

    Without the two-token floor that prefix-matches every Duke record and folds
    an unrelated company into the university's filing history.
    """
    stats, names = contacts.resolve_institution("Duke University", matcher, employers)
    assert stats.certified == 265          # 234 + 31, not 267
    assert not any(n.startswith("DUKE 6") for n in names)


def test_boilerplate_before_the_name_is_still_the_same_institution(employers, matcher):
    """"Trustees of the" is organisational boilerplate, not a different school.

    It is also where Penn's filings actually sit, so refusing every suffix match
    would undercount Penn by 480.
    """
    stats, names = contacts.resolve_institution("University of Pennsylvania", matcher, employers)
    assert stats.certified == 481
    assert "TRUSTEES OF THE UNIVERSITY OF PENNSYLVANIA" in names


def test_a_place_name_before_the_name_is_a_different_school(employers, matcher):
    """The over-merge the first live run produced, pinned.

    Kutztown University of Pennsylvania is not the University of Pennsylvania.
    Suffix matching without the boilerplate guard reported 507 certified
    filings for Penn by folding in seven state schools.
    """
    stats, names = contacts.resolve_institution("University of Pennsylvania", matcher, employers)
    assert "KUTZTOWN UNIVERSITY OF PENNSYLVANIA" not in names
    assert "WEST CHESTER UNIVERSITY OF PENNSYLVANIA" not in names
    assert stats.certified == 481          # not 507


def test_a_branch_after_the_name_is_the_same_institution(employers, matcher):
    """Extra tokens after a complete name are a campus, school or lab of it."""
    stats, names = contacts.resolve_institution("Johns Hopkins University", matcher, employers)
    assert stats.certified == 530
    assert "JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS LAB" in names


def test_an_all_generic_affix_never_merges(employers, matcher):
    """"MEDICAL CENTER" is a suffix of hundreds of unrelated hospitals."""
    stats, names = contacts.resolve_institution("Boston Medical Center", matcher, employers)
    assert stats.certified == 62
    assert "CINCINNATI CHILDRENS HOSPITAL MEDICAL CENTER" not in names


def test_an_abbreviated_front_end_is_a_known_undercount(employers, matcher):
    """The case the merge deliberately will not solve, pinned so it stays known.

    "UT Southwestern" abbreviates its own front end, so neither full stem is an
    affix of the other. Guessing that UT means Texas and not Tennessee needs an
    alias table, and every heuristic tried instead misfired worse. What the row
    does give is the audit trail: a single counted record where an institution
    of that size should have several.
    """
    stats, names = contacts.resolve_institution(
        "The University of Texas Southwestern Medical Center", matcher, employers)
    assert stats.certified == 1
    assert len(names) == 1          # visibly unmerged, not silently summed


def test_the_counted_records_are_returned_for_auditing(employers, matcher):
    """Summing is only honest if you can see what was summed."""
    _, names = contacts.resolve_institution("Washington University", matcher, employers)
    assert set(names) == {"WASHINGTON UNIVERSITY", "WASHINGTON UNIVERSITY IN ST LOUIS"}


def test_an_unknown_institution_resolves_to_nothing(employers, matcher):
    stats, names = contacts.resolve_institution("Some Tiny Liberal Arts College", matcher, employers)
    assert stats is None or stats.certified == 0 or not names


@pytest.mark.parametrize("name,expected", [
    ("The University of Texas", "University of Texas"),
    ("Washington University in St. Louis", "Washington University"),
    ("Mount Sinai (New York)", "Mount Sinai"),
])
def test_institution_variants_strip_decoration(name, expected):
    assert expected in contacts.institution_variants(name)


# ---- the mergeable rule itself ----

@pytest.mark.parametrize("a,b,merge", [
    (("WASHINGTON", "UNIVERSITY"), ("WASHINGTON", "UNIVERSITY", "IN", "ST", "LOUIS"), True),
    (("WASHINGTON", "UNIVERSITY"), ("UNIVERSITY", "WASHINGTON"), False),
    (("DUKE",), ("DUKE", "UNIVERSITY"), False),
    (("MEDICAL", "CENTER"), ("BOSTON", "MEDICAL", "CENTER"), False),
    (("CHILDREN", "S", "HOSPITAL"), ("BOSTON", "CHILDREN", "S", "HOSPITAL"), False),
    # Suffix matches turn on whether the leading extras are boilerplate.
    (("UNIVERSITY", "OF", "PENNSYLVANIA"),
     ("TRUSTEES", "OF", "THE", "UNIVERSITY", "OF", "PENNSYLVANIA"), True),
    (("UNIVERSITY", "OF", "PENNSYLVANIA"),
     ("KUTZTOWN", "UNIVERSITY", "OF", "PENNSYLVANIA"), False),
    (("YALE", "UNIVERSITY"), ("YALE", "UNIVERSITY"), True),
    ((), ("YALE", "UNIVERSITY"), False),
])
def test_mergeable(a, b, merge):
    assert contacts._mergeable(a, b) is merge
    assert contacts._mergeable(b, a) is merge     # the rule is symmetric

# ---- author extraction ----

def _work(title, date_, authorships):
    return {"title": title, "publication_year": int(date_[:4]), "publication_date": date_,
            "doi": f"https://doi.org/10.1/{title[:4].lower().strip()}",
            "id": "https://openalex.org/W1", "authorships": authorships}


def _authorship(name, position, inst, inst_type="company", country="US"):
    return {"author_position": position,
            "author": {"id": f"https://openalex.org/A{abs(hash(name)) % 10000}",
                       "display_name": name},
            "institutions": [{"display_name": inst, "type": inst_type,
                              "country_code": country}]}


@pytest.fixture
def fake_works(monkeypatch):
    """Replace the network call. Each test appends to the returned list."""
    payload: list = []

    def _fake(topic, since, pages, per_page):
        yield from payload

    monkeypatch.setattr(contacts, "_works", _fake)
    return payload


@pytest.fixture
def fake_table(monkeypatch, employers):
    monkeypatch.setattr(sponsorship, "employer_table", lambda: employers)
    return employers


# ---- sector selection ----

def test_industry_and_nonprofit_are_the_default():
    """Alli's primary interest is industry, with nonprofits included.

    An earlier version defaulted to cap-exempt only, which quietly made this a
    university search and buried the sector she actually cares about.
    """
    assert contacts.DEFAULT_SECTORS == ("industry", "nonprofit")
    assert set(contacts.types_for(contacts.DEFAULT_SECTORS)) == {"company", "nonprofit"}


def test_academic_is_available_but_not_on_by_default():
    assert "education" not in contacts.types_for(contacts.DEFAULT_SECTORS)
    assert "education" in contacts.types_for(["academic"])


def test_sectors_combine():
    types = contacts.types_for(["industry", "academic"])
    assert set(types) == {"company", "education", "healthcare"}


def test_any_means_no_filter():
    assert contacts.types_for(["any"]) == ()
    assert contacts.types_for(["industry", "any"]) == ()


def test_an_unknown_sector_falls_back_to_the_default():
    """A typo must not silently return everything."""
    assert set(contacts.types_for(["nonsense"])) == {"company", "nonprofit"}


def test_the_default_filters_academic_out_of_a_real_search(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("At Company", "last", "Midsize Analytics", inst_type="company"),
        _authorship("At University", "last", "Duke University", inst_type="education"),
    ]))
    assert [r.person for r in contacts.find("x")] == ["At Company"]


def test_academic_can_be_asked_for(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("At University", "last", "Duke University", inst_type="education")]))
    assert [r.person for r in contacts.find("x", sectors=["academic"])] == ["At University"]


def test_nonprofits_are_included_by_default(fake_works, fake_table):
    """RAND and RTI are the program-evaluation shops that fit her profile."""
    fake_works.append(_work("Impact evaluation", "2026-09-01", [
        _authorship("At RAND", "last", "RAND Corporation", inst_type="nonprofit")]))
    assert [r.person for r in contacts.find("x")] == ["At RAND"]


# ---- the cap signal ----

@pytest.mark.parametrize("inst_type,expected", [
    ("company", "subject"),
    ("education", "exempt"),
    ("healthcare", "exempt"),
    ("government", "exempt"),
    ("nonprofit", "check"),
])
def test_cap_status_per_sector(fake_works, fake_table, inst_type, expected):
    """Three values, not a boolean.

    A nonprofit is cap-exempt only if it is a research organisation or
    university-affiliated. RAND qualifies; Mercy Corps, which the same OpenAlex
    type returns, does not. Printing "exempt" for both would be a legal claim
    this data cannot support.
    """
    inst = {"company": "Midsize Analytics", "education": "Duke University",
            "healthcare": "Duke University", "government": "Duke University",
            "nonprofit": "RAND Corporation"}[inst_type]
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("Someone", "last", inst, inst_type=inst_type)]))
    rows = contacts.find("x", sectors=["any"])
    assert rows[0].cap == expected


def test_industry_contacts_go_in_the_author_pool(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("At Company", "last", "Midsize Analytics", inst_type="company")]))
    assert contacts.find("x")[0].pool == "author"


def test_nonprofit_and_academic_contacts_go_in_the_capexempt_pool(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("At RAND", "last", "RAND Corporation", inst_type="nonprofit")]))
    assert contacts.find("x")[0].pool == "capexempt"


# ---- who on the paper ----

def test_middle_authors_are_dropped(fake_works, fake_table):
    """A list that includes everyone on the paper is a list nobody writes to."""
    fake_works.append(_work("Extraction at scale", "2026-09-01", [
        _authorship("First Person", "first", "Midsize Analytics"),
        _authorship("Middle Person", "middle", "Midsize Analytics"),
        _authorship("Senior Person", "last", "Midsize Analytics"),
    ]))
    people = [r.person for r in contacts.find("x")]
    assert sorted(people) == ["First Person", "Senior Person"]


def test_last_author_is_labelled_the_pi(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    assert contacts.find("x")[0].position == "PI"


def test_only_pis_when_asked(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("First Person", "first", "Midsize Analytics"),
        _authorship("Senior Person", "last", "Midsize Analytics"),
    ]))
    rows = contacts.find("x", positions=("last",))
    assert [r.person for r in rows] == ["Senior Person"]


def test_the_paper_title_becomes_the_hook(fake_works, fake_table):
    fake_works.append(_work("Asymmetric scoring for extraction", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    assert contacts.find("x")[0].paper == "Asymmetric scoring for extraction"


def test_one_row_per_person_keeping_the_first_paper_seen(fake_works, fake_table):
    """The feed walks newest first, so the first win is the freshest opener."""
    fake_works.append(_work("Newer work", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    fake_works.append(_work("Older work", "2024-01-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    rows = contacts.find("x")
    assert len(rows) == 1
    assert rows[0].paper == "Newer work"


def test_min_filings_skips_employers_with_no_record(fake_works, fake_table):
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("Nobody", "last", "Some Company Nobody Has Heard Of")]))
    assert contacts.find("x", min_filings=1) == []
    assert len(contacts.find("x", min_filings=0)) == 1


def test_a_non_us_institution_is_dropped(fake_works, fake_table):
    """The OpenAlex country filter is a property of the work, not the author.

    A paper with one US institution anywhere on it passes that filter, so a
    Canadian co-author's employer was being looked up in a table of US
    petitions and reported 116 certified filings belonging to someone else.
    """
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("In Canada", "last", "Midsize Analytics", country="CA"),
        _authorship("In the US", "first", "Midsize Analytics", country="US"),
    ]))
    assert [r.person for r in contacts.find("x")] == ["In the US"]


def test_a_missing_country_is_dropped(fake_works, fake_table):
    """Absent is not the same as US, and guessing would attach a wrong number."""
    fake_works.append(_work("A paper", "2026-09-01", [
        {"author_position": "last",
         "author": {"id": "https://openalex.org/A1", "display_name": "Unknown Place"},
         "institutions": [{"display_name": "Midsize Analytics", "type": "company"}]}]))
    assert contacts.find("x") == []


def test_corporate_boilerplate_does_not_merge(fake_works, fake_table):
    """"Machine Intelligence" is not a distinctive name.

    The first industry run folded a bare MACHINE INTELLIGENCE record into
    Machine Intelligence Research Institute. The institution vocabulary was
    university-flavoured and knew nothing about corporate generics, so the
    matcher's own company stems are unioned in.
    """
    assert "INTELLIGENCE" in contacts._GENERIC_INST
    assert "TECHNOLOGIES" in contacts._GENERIC_INST      # from the matcher's list
    assert contacts._mergeable(("MACHINE", "INTELLIGENCE"),
                               ("MACHINE", "INTELLIGENCE", "RESEARCH", "INSTITUTE")) is False


# ---- ranking ----

def test_sponsorship_is_a_gate_not_a_gradient(fake_works, fake_table):
    """Amazon's 16,793 filings must not outrank a fresher, well-sponsored paper.

    Ranking on the raw count is what put Amazon and Microsoft at the top of
    every search in the first live run. Both employers here clear the strong
    threshold, so recency decides.
    """
    fake_works.append(_work("Old Amazon paper", "2025-02-01", [
        _authorship("At Amazon", "last", "Amazon")]))
    fake_works.append(_work("New midsize paper", "2026-09-01", [
        _authorship("At Midsize", "last", "Midsize Analytics")]))
    rows = contacts.find("x")
    assert [r.person for r in rows] == ["At Midsize", "At Amazon"]


def test_a_weakly_sponsoring_employer_ranks_below_a_strong_one(fake_works, fake_table):
    """Recency only decides WITHIN a tier. Two filings is not a strong signal."""
    fake_works.append(_work("Very new tiny paper", "2026-10-01", [
        _authorship("At Tiny", "last", "Tiny Startup")]))
    fake_works.append(_work("Older midsize paper", "2026-01-01", [
        _authorship("At Midsize", "last", "Midsize Analytics")]))
    rows = contacts.find("x")
    assert [r.person for r in rows] == ["At Midsize", "At Tiny"]


def test_tier_thresholds_follow_the_shared_config():
    def tier(certified, analyst):
        return contacts.Contact("p", "PI", "i", "company", certified, analyst,
                                "", "paper", 2026, "2026-01-01", "", "a").tier
    assert tier(config.SPONSOR_STRONG_CERTIFIED, config.SPONSOR_STRONG_ANALYST_SOC) == 0
    assert tier(config.SPONSOR_STRONG_CERTIFIED, config.SPONSOR_STRONG_ANALYST_SOC - 1) == 1
    assert tier(1, 0) == 1
    assert tier(0, 0) == 2


@pytest.mark.parametrize("iso,other,newer_first", [
    ("2026-09-01", "2025-09-01", True),
    ("2026-09-02", "2026-09-01", True),
    ("", "2026-09-01", False),
])
def test_recency_ordering(iso, other, newer_first):
    assert (contacts._neg_date(iso) < contacts._neg_date(other)) is newer_first


@pytest.mark.parametrize("iso", ["", "2026", "2026-10", "not-a-date", "2026-xx-01"])
def test_partial_and_malformed_dates_do_not_raise(iso):
    """OpenAlex sometimes gives a partial date or none at all."""
    assert isinstance(contacts._neg_date(iso), tuple)


# ---- spreading across employers ----

def _c(person, institution, certified=100, analyst=10, published="2026-01-01"):
    return contacts.Contact(person=person, position="PI", institution=institution,
                            sector="company", certified=certified, analyst_certified=analyst,
                            merged_from="", paper="p", year=2026, published=published,
                            link="", openalex_author=person)


def test_one_employer_does_not_fill_the_list():
    """The first real run returned eight of ten contacts at one institution."""
    rows = [_c(f"P{i}", "Big Co") for i in range(8)] + [_c("Other", "Small Co")]
    out = contacts._spread(rows, limit=4, per_institution=2)
    # The cap applies to the first pass; leftover slots are then filled in rank
    # order rather than left empty, so Big Co reappears only after Small Co.
    assert [r.institution for r in out[:3]] == ["Big Co", "Big Co", "Small Co"]


def test_spread_still_fills_the_limit_when_there_is_only_one_employer():
    """A cap must not shrink the list when there is nothing to spread to."""
    rows = [_c(f"P{i}", "Only Co") for i in range(6)]
    assert len(contacts._spread(rows, limit=5, per_institution=2)) == 5


def test_spread_preserves_rank_order_within_the_cap():
    rows = [_c("Best", "A"), _c("Second", "A"), _c("Third", "B")]
    out = contacts._spread(rows, limit=3, per_institution=1)
    assert [r.person for r in out] == ["Best", "Third", "Second"]


def test_no_cap_means_straight_rank_order():
    rows = [_c(f"P{i}", "Only Co") for i in range(4)]
    out = contacts._spread(rows, limit=3, per_institution=0)
    assert [r.person for r in out] == ["P0", "P1", "P2"]


# ---- csv ----

def test_csv_carries_the_hook_the_cap_and_the_counts(tmp_path, fake_works, fake_table):
    fake_works.append(_work("Gold sets and asymmetric scoring", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    rows = contacts.find("x")
    path = tmp_path / "out" / "contacts.csv"
    assert contacts.write_csv(rows, path) == 1
    text = path.read_text()
    assert "Gold sets and asymmetric scoring" in text
    assert "Midsize Analytics" in text
    assert "subject" in text              # industry means the lottery, and says so
    assert "author" in text               # pasteable into the outreach tracker
    assert "2026-09-01" in text


def test_csv_shows_the_merged_records(tmp_path, fake_works, fake_table):
    """The column that caught the Penn over-merge."""
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("Someone", "last", "Duke University", inst_type="education")]))
    rows = contacts.find("x", sectors=["academic"])
    path = tmp_path / "contacts.csv"
    contacts.write_csv(rows, path)
    text = path.read_text()
    assert "DUKE UNIVERSITY HEALTH SYSTEM" in text
    assert "265" in text                  # the merged count, not the bare 234


# ---- the CSV the page reads ----

def test_author_id_leads_the_csv(tmp_path, fake_works, fake_table):
    """The only stable key in the data.

    A name repeats across institutions and name-plus-employer breaks the moment
    somebody changes jobs, so the page keys candidates and dismissals on the
    OpenAlex author id.
    """
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    rows = contacts.find("x")
    path = tmp_path / "contacts.csv"
    contacts.write_csv(rows, path)
    head = path.read_text().splitlines()[0].split(",")
    assert head[0] == "Author ID"
    assert contacts.CSV_HEAD[0] == "Author ID"
    assert rows[0].openalex_author in path.read_text()


def test_read_csv_round_trips_what_write_csv_wrote(tmp_path, fake_works, fake_table):
    fake_works.append(_work("Commas, quotes and all", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    path = tmp_path / "contacts.csv"
    contacts.write_csv(contacts.find("x"), path)
    back = contacts.read_csv(path)
    assert len(back) == 1
    assert back[0]["Person"] == "Senior Person"
    assert back[0]["Hook"].startswith("Commas, quotes and all")
    assert back[0]["Author ID"].startswith("https://openalex.org/")


def test_read_csv_survives_a_broken_file(tmp_path):
    """A CSV half-written by a run in progress must not take the route down."""
    bad = tmp_path / "contacts-bad.csv"
    bad.write_bytes(b"\xff\xfe not, csv, at \x00 all")
    assert contacts.read_csv(bad) == []
    assert contacts.read_csv(tmp_path / "does-not-exist.csv") == []


def test_read_csv_keys_an_old_file_without_the_id_column(tmp_path):
    """Files written before Author ID existed are still worth loading."""
    path = tmp_path / "contacts-old.csv"
    path.write_text("Person,Organisation,Hook\nOld Row,Some Org,a paper\n")
    rows = contacts.read_csv(path)
    assert rows[0]["Author ID"] == "Old Row|Some Org"


def test_read_csv_drops_a_row_with_no_person(tmp_path):
    path = tmp_path / "contacts.csv"
    path.write_text("Author ID,Person,Organisation\nA1,,Some Org\nA2,Real Name,Some Org\n")
    assert [r["Person"] for r in contacts.read_csv(path)] == ["Real Name"]


def test_as_dict_includes_the_computed_fields(fake_works, fake_table):
    """`asdict` alone drops the properties, and the page needs cap and pool."""
    fake_works.append(_work("A paper", "2026-09-01", [
        _authorship("Senior Person", "last", "Midsize Analytics")]))
    d = contacts.find("x")[0].as_dict()
    assert d["cap"] == "subject"
    assert d["pool"] == "author"
    assert d["tier"] == 0
    assert d["person"] == "Senior Person"


# ---- the simple_key contract ----

TRICKY_NAMES = [
    "The University of Texas Southwestern Medical Center",
    "Washington University in St. Louis",
    "Cincinnati Children's Hospital Medical Center",
    "Universite de Montreal",
    "Université de Montréal",
    "Johns Hopkins University  Applied Physics Lab",
    "AT&T Labs Research",
    "Amazon (United States)",
    "Procter & Gamble Co., Ltd.",
    "Dana-Farber Cancer Institute",
    "THE OHIO STATE UNIVERSITY",
    "St. Jude Children's Research Hospital",
    "École Polytechnique",
    "  spaced   out   name  ",
    "123 Numbers 456",
    "",
]


def test_simple_key_matches_the_javascript(tmp_path):
    """The page's simpleKey must agree with this one, character for character.

    If they drift, every lookup silently misses and every employer reads as
    having no filing record, which looks like "no contacts found" rather than
    like a bug. So the JavaScript is lifted out of docs/app.js and run against
    the same inputs rather than being eyeballed.
    """
    import json
    import re as _re
    import subprocess
    from pathlib import Path

    app = (Path(__file__).resolve().parent.parent / "docs" / "app.js").read_text()
    match = _re.search(r"function simpleKey\(name\) \{.*?\n\}", app, _re.S)
    assert match, "simpleKey is no longer in docs/app.js under that name"

    script = match.group(0) + "\n" + (
        "const names = JSON.parse(process.argv[2]);\n"
        "console.log(JSON.stringify(names.map(simpleKey)));\n"
    )
    path = tmp_path / "key.mjs"
    path.write_text(script)
    proc = subprocess.run(["node", str(path), json.dumps(TRICKY_NAMES)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    from_js = json.loads(proc.stdout)
    from_py = [contacts.simple_key(n) for n in TRICKY_NAMES]
    mismatched = [(n, p, j) for n, p, j in zip(TRICKY_NAMES, from_py, from_js) if p != j]
    assert not mismatched, mismatched


@pytest.mark.parametrize("name,expected", [
    ("The Ohio State University", "OHIO STATE UNIVERSITY"),
    ("AT&T Labs", "AT AND T LABS"),
    ("Université de Montréal", "UNIVERSITE DE MONTREAL"),
    ("Amazon (United States)", "AMAZON UNITED STATES"),
    ("  spaced   out  ", "SPACED OUT"),
    ("", ""),
])
def test_simple_key_cases(name, expected):
    assert contacts.simple_key(name) == expected


# ---- the published index ----

def test_index_merges_siblings(employers):
    """The whole reason the index is built in Python rather than in the page."""
    idx = contacts.build_sponsor_index(employers)
    emp = idx["employers"]
    assert emp[contacts.simple_key("Washington University in St. Louis")][0] == 506
    assert emp[contacts.simple_key("Washington University")][0] == 506


def test_index_keeps_different_schools_apart(employers):
    idx = contacts.build_sponsor_index(employers)["employers"]
    assert idx[contacts.simple_key("University of Washington")][0] == 197
    # The Penn over-merge, pinned here too: the index must not inherit it.
    assert idx[contacts.simple_key("University of Pennsylvania")][0] == 481
    assert idx[contacts.simple_key("Kutztown University of Pennsylvania")][0] == 14


def test_index_does_not_let_a_one_token_stem_swallow_a_university(employers):
    idx = contacts.build_sponsor_index(employers)["employers"]
    assert idx[contacts.simple_key("Duke University")][0] == 265      # not 267


def test_index_carries_the_thresholds_the_page_ranks_with(employers):
    """Published so the page's tiering cannot drift from config.py."""
    meta = contacts.build_sponsor_index(employers)["meta"]
    assert meta["strong_certified"] == config.SPONSOR_STRONG_CERTIFIED
    assert meta["strong_analyst"] == config.SPONSOR_STRONG_ANALYST_SOC
    assert meta["cap"]["company"] == "subject"
    assert meta["cap"]["nonprofit"] == "check"
    assert meta["sector_types"]["industry"] == ["company"]


def test_index_honours_the_threshold(employers):
    idx = contacts.build_sponsor_index(employers, min_certified=200)["employers"]
    assert contacts.simple_key("Amazon") in idx
    assert contacts.simple_key("Tiny Startup") not in idx


def test_affix_index_finds_the_same_siblings_as_a_full_scan(employers):
    """The bucketing is an optimisation, so it must change nothing.

    A full scan of 64,000 employers per employer is four billion comparisons
    and this runs inside `publish`, hence the index. If it ever disagrees with
    the scan it is silently dropping merges.
    """
    first, last = contacts._affix_index(employers)
    for key, stats in employers.items():
        toks = contacts._tokens(stats.display_name)
        scanned = set(contacts._siblings(toks, employers))
        bucketed = {key}
        if toks:
            for bucket in (first.get(toks[0], ()), last.get(toks[-1], ())):
                for other, other_toks in bucket:
                    if contacts._mergeable(toks, other_toks):
                        bucketed.add(other)
        assert scanned <= bucketed or not scanned, (stats.display_name, scanned - bucketed)
