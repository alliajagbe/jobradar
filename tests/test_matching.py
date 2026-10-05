"""Employer-name matching.

A wrong match here is worse than no match: it renders a confident "412 certified
filings" beside a company that has never sponsored anyone. The refusal rules
matter more than the acceptance rule.
"""

from __future__ import annotations

import pytest

from jobradar.matching import Matcher
from jobradar.normalize import employer_norm


def test_exact_and_suffix_stripping():
    m = Matcher(["STRIPE", "DATABRICKS", "ANTHROPIC"])
    assert m.match("Stripe, Inc.").method == "exact"
    assert m.match("Databricks Inc.").norm_name == "DATABRICKS"
    # PBC is a legal form; without it "Anthropic PBC" never meets "Anthropic".
    assert employer_norm("Anthropic PBC") == "ANTHROPIC"


def test_ambiguity_is_refused():
    """Two plausible employers means we do not know which. An honest "no record"
    beats a confident number attached to the wrong company."""
    m = Matcher(["ACME SOLUTIONS LLC2", "ACME SOLUTIONS GROUPX"])
    result = m.match("Acme Solutions")
    assert result.norm_name is None
    assert result.method == "ambiguous"


def test_short_names_match_only_on_an_exact_first_token():
    """Four-letter startup names are common and not hopeless: Ramp files as
    "RAMP BUSINESS CORPORATION". But they must not reach the fuzzy scorer,
    where they match nearly everything."""
    m = Matcher(["RAMP BUSINESS", "RAMPART SECURITY SYSTEMS", "CALM"])
    assert m.match("Ramp").norm_name == "RAMP BUSINESS"
    assert m.match("Ramp").method == "short-name"
    assert m.match("Calm").method == "exact"


def test_short_name_with_several_candidates_is_refused():
    m = Matcher(["RAMP BUSINESS", "RAMP HOLDINGS"])
    assert m.match("Ramp").norm_name is None


def test_unknown_company_returns_no_record():
    m = Matcher(["STRIPE", "GOOGLE"])
    assert m.match("Some Tiny Startup").norm_name is None


# --- Lever descriptions -------------------------------------------------------
# Lever does not always populate its *Plain fields. A Thunkable posting had an
# empty descriptionPlain and 4338 characters of HTML in `description`, so the
# job arrived with NO text: no skills to match, and a score built on the title
# alone. It read 41; with the description it reads 80. This matters out of
# proportion to one posting, because the Lever board count is going from 5 to
# roughly 2000.

def _lever_posting(**over):
    job = {"id": "abc", "text": "Senior Data Analyst", "hostedUrl": "https://x/y",
           "categories": {"location": "San Francisco, CA"}, "createdAt": 1700000000000}
    job.update(over)
    return job


def test_a_lever_html_description_is_not_dropped():
    from jobradar.sources.lever import Lever
    from jobradar.sources.base import Board
    board = Board(source="lever", token="acme", company="Acme")
    got = Lever()._to_posting(
        _lever_posting(description="<p>Build <b>dashboards</b> in SQL.</p>"), board)
    assert got.description_html, "HTML description must be carried through"
    assert "dashboards" in (got.description_html or "").lower()


def test_plain_text_is_still_preferred_when_present():
    from jobradar.sources.lever import Lever
    from jobradar.sources.base import Board
    board = Board(source="lever", token="acme", company="Acme")
    got = Lever()._to_posting(
        _lever_posting(descriptionPlain="Plain wins.",
                       description="<p>HTML loses.</p>"), board)
    assert got.description_text == "Plain wins."
    assert not got.description_html, "no need to carry both"


# --- sibling filing entities --------------------------------------------------
# The ambiguity refusal is right in principle and was misfiring in practice. A
# company that files under several legal entities produced two candidates tied
# at 100, and the matcher refused rather than guess, so GlobalFoundries read as
# "no filing record found" while holding 125 certifications. Third occurrence
# this week, after EA and Hometap.

def test_entity_designations_reduce_to_one_stem():
    from jobradar.matching import entity_stem
    assert entity_stem("GLOBALFOUNDRIES U S") == "GLOBALFOUNDRIES"
    assert entity_stem("GLOBALFOUNDRIES U S 2") == "GLOBALFOUNDRIES"
    assert entity_stem("ACME HOLDINGS LLC") == "ACME"


def test_words_that_distinguish_real_companies_are_not_stripped():
    """Technologies, Systems and Solutions separate different employers, so
    stripping them would merge companies that are not related."""
    from jobradar.matching import entity_stem
    assert entity_stem("ZOOMINFO TECHNOLOGIES") != entity_stem("ZOOMINFO SYSTEMS")
    assert entity_stem("AMERICAN AIRLINES") != entity_stem("AMERICAN EXPRESS")


def test_sibling_entities_are_matched_together():
    from jobradar.matching import Matcher
    m = Matcher(["GLOBALFOUNDRIES U S", "GLOBALFOUNDRIES U S 2"])
    got = m.match("GlobalFoundries")
    assert got.method == "sibling-entities", got.method
    assert set(got.names) == {"GLOBALFOUNDRIES U S", "GLOBALFOUNDRIES U S 2"}


@pytest.mark.parametrize("query,table", [
    ("Consulting", ["CONSULTING SERVICES", "CONSULTING HOLDINGS"]),
    ("Technology", ["TECHNOLOGY SERVICES", "TECHNOLOGY HOLDINGS"]),
    ("Analytics",  ["ANALYTICS GROUP LLC", "ANALYTICS HOLDINGS INC"]),
])
def test_a_generic_stem_is_not_evidence_of_a_shared_parent(query, table):
    """Found while measuring the fix: 186 employers file under several entities
    and the generic ones are where merging would be wrong. "Consulting
    Services" and "Consulting Holdings" both reduce to CONSULTING and are
    unrelated firms."""
    from jobradar.matching import Matcher
    assert Matcher(table).match(query).method == "ambiguous"


def test_different_companies_are_still_refused():
    """The guard this fix must not disable. An honest "no record" beats a
    confident number attached to the wrong employer."""
    from jobradar.matching import Matcher
    m = Matcher(["AMERICAN AIRLINES", "AMERICAN EXPRESS"])
    got = m.match("American")
    assert got.method == "ambiguous"
    assert got.norm_name is None and not got.names


def test_combine_sums_the_filings_of_sibling_entities():
    from jobradar.sponsorship import EmployerStats, combine
    table = {
        "GLOBALFOUNDRIES U S": EmployerStats("GLOBALFOUNDRIES U S", "GlobalFoundries U.S., Inc.",
                                             116, 23, 2, "2026-01-01"),
        "GLOBALFOUNDRIES U S 2": EmployerStats("GLOBALFOUNDRIES U S 2", "GlobalFoundries U.S. 2, LLC",
                                               9, 0, 0, "2025-06-01"),
    }
    got = combine(table, tuple(table))
    assert got.certified == 125
    assert got.analyst_certified == 23
    assert got.display_name == "GlobalFoundries U.S., Inc.", "the widest entity names the match"
    assert got.last_decision == "2026-01-01", "most recent decision wins"


def test_combine_on_a_single_name_is_unchanged():
    from jobradar.sponsorship import EmployerStats, combine
    one = EmployerStats("ACME", "Acme Inc", 5, 1, 0, "2026-01-01")
    assert combine({"ACME": one}, ("ACME",)) is one
    assert combine({"ACME": one}, ()) is None
