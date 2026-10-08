"""Tests for the contact finder.

No network. The OpenAlex half is driven from a fixture payload, and the
employer half from a small hand-built table, so every case names the specific
way the join goes wrong rather than asserting on live data that moves.
"""

from __future__ import annotations

import pytest

from jobradar import contacts, sponsorship
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

def _work(title, year, authorships):
    return {"title": title, "publication_year": year,
            "doi": f"https://doi.org/10.1/{title[:4].lower()}",
            "id": "https://openalex.org/W1", "authorships": authorships}


def _authorship(name, position, inst, inst_type="education"):
    return {"author_position": position,
            "author": {"id": f"https://openalex.org/A{abs(hash(name)) % 10000}",
                       "display_name": name},
            "institutions": [{"display_name": inst, "type": inst_type}]}


@pytest.fixture
def fake_works(monkeypatch):
    """Replace the network call. Each test sets `payload` before calling find."""
    payload: list = []

    def _fake(topic, since, pages, per_page):
        yield from payload

    monkeypatch.setattr(contacts, "_works", _fake)
    return payload


@pytest.fixture
def fake_table(monkeypatch, employers):
    monkeypatch.setattr(sponsorship, "employer_table", lambda: employers)
    return employers


def test_middle_authors_are_dropped(fake_works, fake_table):
    """A list that includes everyone on the paper is a list nobody writes to."""
    fake_works.append(_work("Extraction at scale", 2026, [
        _authorship("First Person", "first", "Duke University"),
        _authorship("Middle Person", "middle", "Duke University"),
        _authorship("Senior Person", "last", "Duke University"),
    ]))
    rows = contacts.find("anything", min_filings=1)
    assert [r.person for r in rows] == ["Senior Person", "First Person"] or \
           sorted(r.person for r in rows) == ["First Person", "Senior Person"]
    assert "Middle Person" not in [r.person for r in rows]


def test_last_author_is_labelled_the_pi(fake_works, fake_table):
    fake_works.append(_work("A paper", 2026, [
        _authorship("Senior Person", "last", "Duke University")]))
    rows = contacts.find("anything")
    assert rows[0].position == "PI"


def test_the_paper_title_becomes_the_hook(fake_works, fake_table):
    fake_works.append(_work("Asymmetric scoring for clinical extraction", 2026, [
        _authorship("Senior Person", "last", "Duke University")]))
    rows = contacts.find("anything")
    assert rows[0].paper == "Asymmetric scoring for clinical extraction"


def test_one_row_per_person_keeping_the_first_paper_seen(fake_works, fake_table):
    """The loop walks newest first, so the first win is the freshest opener."""
    fake_works.append(_work("Newer work", 2026, [
        _authorship("Senior Person", "last", "Duke University")]))
    fake_works.append(_work("Older work", 2024, [
        _authorship("Senior Person", "last", "Duke University")]))
    rows = contacts.find("anything")
    assert len(rows) == 1
    assert rows[0].paper == "Newer work"


def test_companies_are_excluded_by_default(fake_works, fake_table):
    """The default is cap-exempt, and that default is the point of the feature.

    Amazon files 16,793 certified petitions, which ranks it top of every search
    while being exactly the cap-subject lottery this routes around.
    """
    fake_works.append(_work("A benchmark", 2026, [
        _authorship("Industry Person", "last", "Amazon", inst_type="company"),
        _authorship("Faculty Person", "last", "Duke University"),
    ]))
    rows = contacts.find("anything")
    assert [r.person for r in rows] == ["Faculty Person"]


def test_companies_are_included_when_asked_for(fake_works, fake_table):
    fake_works.append(_work("A benchmark", 2026, [
        _authorship("Industry Person", "last", "Amazon", inst_type="company")]))
    rows = contacts.find("anything", sector="any")
    assert [r.person for r in rows] == ["Industry Person"]
    assert rows[0].pool == "author"          # not the research-group pool


def test_a_faculty_contact_lands_in_the_capexempt_pool(fake_works, fake_table):
    fake_works.append(_work("A paper", 2026, [
        _authorship("Faculty Person", "last", "Duke University")]))
    assert contacts.find("anything")[0].pool == "capexempt"


def test_min_filings_skips_institutions_with_no_record(fake_works, fake_table):
    fake_works.append(_work("A paper", 2026, [
        _authorship("Nobody", "last", "Some Tiny Liberal Arts College")]))
    assert contacts.find("anything", min_filings=1) == []
    assert len(contacts.find("anything", min_filings=0)) == 1


def test_ranking_puts_the_most_analyst_filings_first(fake_works, fake_table):
    fake_works.append(_work("A paper", 2026, [
        _authorship("At Boston", "last", "Boston Medical Center", inst_type="healthcare")]))
    fake_works.append(_work("B paper", 2026, [
        _authorship("At Duke", "last", "Duke University")]))
    rows = contacts.find("anything")
    assert [r.person for r in rows] == ["At Duke", "At Boston"]   # 24 analyst vs 23


# ---- spreading across institutions ----

def _c(person, institution):
    return contacts.Contact(person=person, position="PI", institution=institution,
                            sector="education", certified=100, analyst_certified=10,
                            merged_from="", paper="p", year=2026,
                            link="", openalex_author=person)


def test_one_institution_does_not_fill_the_list():
    """The first real run returned eight of ten contacts at one university."""
    rows = [_c(f"P{i}", "Big U") for i in range(8)] + [_c("Other", "Small U")]
    out = contacts._spread(rows, limit=4, per_institution=2)
    # The cap applies to the first pass; leftover slots are then filled in rank
    # order rather than left empty, so Big U reappears only after Small U has
    # had its turn.
    assert [r.institution for r in out[:3]] == ["Big U", "Big U", "Small U"]


def test_spread_still_fills_the_limit_when_there_is_only_one_institution():
    """A cap must not shrink the list when there is nothing to spread to."""
    rows = [_c(f"P{i}", "Only U") for i in range(6)]
    assert len(contacts._spread(rows, limit=5, per_institution=2)) == 5


def test_spread_preserves_rank_order_within_the_cap():
    rows = [_c("Best", "A"), _c("Second", "A"), _c("Third", "B")]
    out = contacts._spread(rows, limit=3, per_institution=1)
    assert [r.person for r in out] == ["Best", "Third", "Second"]


def test_no_cap_means_straight_rank_order():
    rows = [_c(f"P{i}", "Only U") for i in range(4)]
    out = contacts._spread(rows, limit=3, per_institution=0)
    assert [r.person for r in out] == ["P0", "P1", "P2"]


# ---- csv ----

def test_csv_carries_the_hook_and_the_filing_counts(tmp_path, fake_works, fake_table):
    fake_works.append(_work("Gold sets and asymmetric scoring", 2026, [
        _authorship("Senior Person", "last", "Duke University")]))
    rows = contacts.find("anything")
    path = tmp_path / "out" / "contacts.csv"
    assert contacts.write_csv(rows, path) == 1
    text = path.read_text()
    assert "Gold sets and asymmetric scoring" in text
    assert "Duke University" in text
    assert "265" in text                 # the merged count, not the bare 234
    assert "capexempt" in text           # pasteable into the outreach tracker
