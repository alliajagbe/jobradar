"""Normalization tests: HTML flattening, name and title collapsing, locations."""

from __future__ import annotations

import pytest

from jobradar.normalize import (
    company_norm, employer_norm, html_to_text, parse_locations, sentences,
    title_dedupe_norm, title_level_penalty,
)


def test_greenhouse_double_escaping():
    """Greenhouse escapes its content twice. One pass leaves literal &lt; in the
    text and every downstream regex then matches nothing."""
    raw = "&amp;lt;p&amp;gt;We want&amp;lt;/p&amp;gt;&amp;lt;ul&amp;gt;&amp;lt;li&amp;gt;SQL&amp;lt;/li&amp;gt;&amp;lt;/ul&amp;gt;"
    assert html_to_text(raw, double_unescape=True) == "We want\n\n- SQL"
    assert "&lt;" not in html_to_text(raw, double_unescape=True)


def test_list_boundaries_survive():
    """Bullet boundaries are load-bearing: sentence-level sponsorship matching
    cannot split twelve requirements merged into one line."""
    html = "<ul><li>Owns reporting</li><li>Must not sponsor</li></ul>"
    assert len(sentences(html_to_text(html))) == 2


def test_malformed_html_degrades_rather_than_raises():
    assert "hello" in html_to_text("<p>hello<<<>>").lower()


@pytest.mark.parametrize("raw,expected", [
    ("Stripe, Inc.", "STRIPE"),
    ("The Stripe Company", "STRIPE"),
    ("Acme Holdings Inc LLC", "ACME HOLDINGS"),
    ("Google LLC", "GOOGLE"),
    # Must not strip down to one token; that would fuzzy-match half the file.
    ("Technology Solutions Group", "TECHNOLOGY SOLUTIONS"),
])
def test_employer_norm(raw, expected):
    assert employer_norm(raw) == expected


def test_title_dedupe_collapses_word_order():
    """The common real case: two boards phrasing one role differently."""
    assert (title_dedupe_norm("Analyst, Business Intelligence")
            == title_dedupe_norm("Business Intelligence Analyst"))


def test_title_dedupe_strips_req_ids_and_levels():
    assert (title_dedupe_norm("Data Analyst (R2023188)")
            == title_dedupe_norm("Data Analyst II"))


def test_level_penalty():
    assert title_level_penalty("Analyst II")[0] == -4
    assert title_level_penalty("Analyst III")[0] == -8
    assert title_level_penalty("Data Analyst")[0] == 0


@pytest.mark.parametrize("raw,is_us,primary", [
    ("Remote (US)", True, "remote-us"),
    ("Remote - United States", True, "remote-us"),
    ("US-Remote", True, "remote-us"),
    ("New York, NY", True, "new york|NY"),
    ("Winston-Salem, NC", True, "winston-salem|NC"),
    ("Bengaluru, India", False, "unknown"),
    ("London", False, "unknown"),
])
def test_parse_locations(raw, is_us, primary):
    parsed = parse_locations(raw)
    assert parsed.is_us is is_us
    assert parsed.primary == primary


def test_workday_multiple_locations_is_uninformative_not_foreign():
    """Workday reports "3 Locations" constantly. Reading that as foreign would
    drop a whole tenant."""
    parsed = parse_locations("3 Locations")
    assert parsed.is_us is False
    assert parsed.confidence == "low"      # falls through, not a hard drop


def test_bare_remote_resolves_from_description():
    parsed = parse_locations("Remote", None, "This role is open across the United States.")
    assert parsed.is_us is True
    assert parsed.confidence == "low"


def test_sentences_keep_abbreviations_intact():
    """Splitting on "U.S." cut the no-sponsorship clause in half and the most
    important pattern in the project then matched neither piece."""
    text = "You must be authorized to work in the U.S. without sponsorship."
    assert len(sentences(text)) == 1


@pytest.mark.parametrize("raw", [
    "Wuxi, Jiangsu, cn",
    "Lisboa, Lisboa, pt",
    "Timisoara, TM, ro",
    "Bayan Lepas, Pulau Pinang, my",
])
def test_trailing_iso_country_code_is_foreign(raw):
    """Several boards report location as "City, Region, cn". Without the ISO
    code check these classify as unknown, which is low confidence rather than
    foreign, and a whole non-US enterprise board leaks in looking domestic."""
    parsed = parse_locations(raw)
    assert parsed.is_us is False
    assert parsed.confidence == "high"      # confidently foreign, so it drops


def test_us_iso_suffix_is_not_confused_with_a_state_code():
    """US state codes are also two letters. "Charleston, SC, us" must stay
    domestic even though the check looks at a trailing two-letter token."""
    parsed = parse_locations("Charleston, SC, us")
    assert parsed.is_us is True
    assert parsed.primary == "charleston|SC"


@pytest.mark.parametrize("raw", [
    "Sofia, Sofia City Province, Bulgaria",
    "Beograd, , Serbia",
    "Hatvan, , Hungary",
    "Lima, Peru",
])
def test_country_names_outside_the_curated_city_list(raw):
    assert parse_locations(raw).is_us is False


@pytest.mark.parametrize("raw,primary", [
    ("San Francisco", "san francisco"),
    ("Seattle", "seattle"),
    ("Mountain View", "mountain view"),
])
def test_bare_us_city_without_a_state(raw, primary):
    """Ashby boards routinely report just the city. Treating those as unknown
    costs them 9 of 15 location points for no reason."""
    parsed = parse_locations(raw)
    assert parsed.is_us is True
    assert parsed.primary == primary


# --- US roles misread as foreign ---------------------------------------------
# The expensive direction: a US role classified foreign is dropped with
# drop_reason "location:non-us" and never reaches the page. Two separate
# orderings in _classify did that to 186 open postings, including roles at
# Twitch, SpaceX and Vanguard in San Francisco, Hawthorne and Malvern.

@pytest.mark.parametrize("raw", [
    # An explicit US marker must beat a place name that is also somewhere abroad.
    "Melbourne, Florida, United States",
    "Vancouver, Washington, United States of America",
    "Remote, Georgia, United States, AMER",
    "Atlanta, Georgia, United States",
    "USA - Mountain View, CA",
    "USA - Denver, CO",
    # 24 of the 50 state codes are also ISO country codes. A bare two-letter
    # tail is a state before it is a country.
    "San Francisco, CA",   # CA: Canada
    "Atlanta, GA",         # GA: Gabon
    "Boston, MA",          # MA: Morocco
    "Richmond, VA",        # VA: Vatican City
    "Philadelphia, PA",    # PA: Panama
    "Chicago, IL",         # IL: Israel
    "Baltimore, MD",       # MD: Moldova
    "Phoenix, AZ",         # AZ: Azerbaijan
    "Indianapolis, IN",    # IN: India
    # Ambiguous alone, disambiguated by a US city in the same fragment.
    "Georgia - Atlanta",
])
def test_a_us_posting_is_not_classified_foreign(raw):
    assert parse_locations(raw).is_us, raw


@pytest.mark.parametrize("raw", [
    "Tbilisi, Georgia",      # the country, with no American signal anywhere
    "London, United Kingdom",
    "Toronto, Ontario, Canada",
    "Vancouver, British Columbia, Canada",
    "Melbourne, Victoria, Australia",
    "Bengaluru, India",
    "Sao Paulo, Brazil",
    "Dublin, Ireland",
    "Remote - Canada",
])
def test_a_foreign_posting_is_still_foreign(raw):
    """The fix must not buy US coverage by letting everything through."""
    assert not parse_locations(raw).is_us, raw
