"""Employer-name matching.

A wrong match here is worse than no match: it renders a confident "412 certified
filings" beside a company that has never sponsored anyone. The refusal rules
matter more than the acceptance rule.
"""

from __future__ import annotations

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
