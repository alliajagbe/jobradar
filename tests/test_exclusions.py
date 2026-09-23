"""Employers and sectors to skip regardless of fit."""

from __future__ import annotations

from jobradar.normalize import employer_norm
from jobradar.refresh import load_profile


def test_gambling_companies_are_excluded_by_normalized_name():
    """Matching is on the normalized name, so "DraftKings Inc." and
    "DRAFTKINGS, INC" both hit the same entry."""
    profile = load_profile()
    from jobradar.refresh import _is_excluded
    excluded = frozenset(employer_norm(c) for c in profile["excluded_companies"])
    for spelling in ["DraftKings Inc.", "DRAFTKINGS, INC", "FanDuel Group",
                     "MGM Resorts International", "Caesars Entertainment, Inc.",
                     "Penn Entertainment, Inc.", "Bally's Corporation"]:
        assert _is_excluded(employer_norm(spelling), excluded), spelling


def test_ordinary_employers_are_not_excluded():
    profile = load_profile()
    from jobradar.refresh import _is_excluded
    excluded = frozenset(employer_norm(c) for c in profile["excluded_companies"])
    for name in ["Stripe", "Deloitte Consulting LLP", "Capital One", "Target",
                 "JPMorgan Chase & Co.", "Ernst & Young U.S. LLP", "Sleep Number"]:
        assert not _is_excluded(employer_norm(name), excluded), name


def test_description_terms_are_specific_to_the_industry():
    """Generic words like "betting" or "odds" appear in ordinary analytics
    writing. The list must not contain them, or it silently drops good roles."""
    terms = load_profile()["excluded_description_terms"]
    benign = (
        "We are betting on a data-driven future and improving the odds of "
        "success for our customers. The analyst will model risk and gaming "
        "out scenarios for the leadership team."
    ).lower()
    assert not [t for t in terms if t in benign]


def test_a_real_gambling_description_is_caught():
    terms = load_profile()["excluded_description_terms"]
    posting = ("Join our sportsbook analytics team building models for our "
               "online casino and wagering products.").lower()
    assert [t for t in terms if t in posting]


def test_corporate_variants_of_an_excluded_name_are_caught():
    """"FanDuel Group" normalizes to two tokens, and employer_norm refuses to
    strip a descriptor down to a single token, so exact equality misses it."""
    from jobradar.refresh import _is_excluded
    excluded = frozenset({"FANDUEL", "DRAFTKINGS", "MGM RESORTS"})
    for name in ["FANDUEL", "FANDUEL GROUP", "DRAFTKINGS", "MGM RESORTS INTERNATIONAL"]:
        assert _is_excluded(name, excluded), name


def test_prefix_matching_does_not_swallow_unrelated_names():
    from jobradar.refresh import _is_excluded
    excluded = frozenset({"FANDUEL", "STAKE"})
    for name in ["FANDUELLING SYSTEMS", "STAKEHOLDER ANALYTICS", "STRIPE"]:
        assert not _is_excluded(name, excluded), name


# --- feed order ---------------------------------------------------------------

def test_the_feed_is_ordered_newest_first():
    """Alli's instruction: recency over score. A job posted today is worth more
    than a better-scoring one from two weeks ago that everybody has seen."""
    from jobradar.publish import group
    jobs = [
        {"dedupe_key": "old-strong", "posted_at": "2026-09-01T00:00:00Z", "score": 95},
        {"dedupe_key": "new-weak", "posted_at": "2026-09-22T00:00:00Z", "score": 40},
        {"dedupe_key": "undated", "posted_at": None, "score": 99},
        {"dedupe_key": "new-strong", "posted_at": "2026-09-22T00:00:00Z", "score": 80},
    ]
    base = {k: None for k in (
        "id", "company", "source", "url", "title", "locations", "location_primary",
        "is_remote", "is_us", "location_confidence", "snippet", "explain",
        "matched_skills", "missing_skills", "min_years", "title_tier",
        "sponsorship", "status", "drop_reason", "first_seen", "last_seen")}
    cards = group([{**base, **j} for j in jobs])
    order = [c["dedupe_key"] for c in cards]
    assert order[:2] == ["new-strong", "new-weak"], order
    assert order.index("old-strong") < order.index("undated"), order
    assert order[-1] == "undated", "a card with no date sorts last, not first"
