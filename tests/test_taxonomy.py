"""Gate tests.

These are the highest-value tests in the project: the gates decide what the tool
shows, and every failure mode here is silent. A title wrongly rejected simply
never appears, and you would not notice for weeks.
"""

from __future__ import annotations

import pytest

from jobradar.normalize import sentences, title_norm
from jobradar.taxonomy import (
    min_years_required, seniority_reject, sponsorship_text_verdict, title_tier,
)


@pytest.mark.parametrize("title,tier", [
    ("Data Analyst", "A"),
    ("Business Analyst", "A"),
    ("Data Analyst, Growth", "A"),
    ("Analytics Engineer", "A"),
    ("Business Intelligence Analyst", "A"),
    ("Data Scientist, Safeguards", "A"),
    ("Data Scientist, Core Data - PhD", "A"),
    ("New Grad Rotational Analyst Program", "C"),
    ("Early Career Analytics Associate", "A-partial"),
    ("Risk Analyst", "B"),
    ("Financial Analyst", "B"),
    ("Security Analyst", "N"),
    ("SOC Analyst", "N"),
    ("QA Analyst", "N"),
    # Not negative any more: a domain is not a different profession. A bare
    # "Credit Analyst" still fails the gate, but on having no analytics term at
    # all, which is a different and more honest reason.
    ("Credit Analyst", None),
    ("Account Executive", "N"),
    ("Software Engineer", None),
    ("Registered Nurse", None),
])
def test_title_tier(title, tier):
    assert title_tier(title_norm(title)).tier == tier


@pytest.mark.parametrize("title,term", [
    ("Senior Data Analyst", "senior"),
    ("Sr. Data Analyst", "sr"),
    ("Staff Data Scientist", "staff"),
    ("Principal Analyst", "principal"),
    ("Director of Analytics", "director"),
    ("Head of Data", "head of"),
    ("Engineering Manager", "manager"),
    ("Data Architect", "architect"),
    ("Lead Data Scientist", "lead"),
    ("Analytics Lead", "lead"),
    ("Data Analyst Intern", "intern"),
    ("Analytics Co-op", "co-op"),
])
def test_seniority_rejects(title, term):
    assert seniority_reject(title_norm(title)) == term


@pytest.mark.parametrize("title", [
    "Data Analyst",
    "Analyst II",
    "Business Analyst I",
    # The one that a naive \blead\b pattern silently destroys. This is an
    # entry-level marketing analytics role, not a leadership position.
    "Lead Generation Analyst",
    "Demand Generation Analyst",
    "Team Lead Support Specialist",
])
def test_seniority_keeps(title):
    assert seniority_reject(title_norm(title)) is None


@pytest.mark.parametrize("text,expected", [
    # THE most important assertion in this suite. Someone on OPT IS authorized
    # to work. If this ever starts returning says_no, the pipeline drops most
    # of its input and the page just looks empty.
    ("Applicants must be authorized to work in the United States.", "silent"),
    ("All applicants must be legally authorized to work in the US.", "silent"),
    ("Must be authorized to work in the U.S. for any employer.", "silent"),
    # Only the longer form disqualifies.
    ("Candidates must be authorized to work in the United States without "
     "sponsorship now or in the future.", "says_no"),
    ("You must be authorized to work in the U.S. without the need for sponsorship.",
     "says_no"),
    ("We are unable to sponsor or take over sponsorship of an employment visa.",
     "says_no"),
    ("This role is not eligible for visa sponsorship.", "says_no"),
    ("We are able to offer visa sponsorship for this role.", "says_yes"),
    ("H-1B sponsorship is available for exceptional candidates.", "says_yes"),
    ("This position requires U.S. citizenship.", "requires_citizenship"),
    ("Must be a US citizen due to federal contract requirements.", "requires_citizenship"),
    ("An active Secret clearance is required for this role.", "requires_clearance"),
    # A preference, not a bar. Dropping these loses good roles.
    ("Candidates with an active Secret clearance are strongly encouraged to apply.",
     "silent"),
    ("We offer competitive pay and great benefits.", "silent"),
])
def test_sponsorship_verdict(text, expected):
    assert sponsorship_text_verdict(sentences(text)).verdict == expected


def test_mixed_signals_keep_both_sides():
    text = ("We are able to offer visa sponsorship for this role. "
            "Applicants must be authorized to work without sponsorship now or in the future.")
    verdict = sponsorship_text_verdict(sentences(text))
    assert verdict.verdict == "says_no"          # negative wins
    assert verdict.positive_evidence is not None  # but the yes is preserved


@pytest.mark.parametrize("text,years", [
    ("3+ years of experience in analytics, 5+ years preferred", 3),
    ("Minimum 6 years of experience required", 6),
    ("0-2 years of experience", 0),
    # Not a requirement: a description of the data.
    ("We analyze the last 10 years of transaction data", None),
    ("No experience necessary", None),
])
def test_min_years(text, years):
    assert min_years_required(text) == years


@pytest.mark.parametrize("title", [
    # A different profession wearing the word "analyst". A plain substring test
    # missed every one of these: "security analyst" does not appear in
    # "Security Operations Analyst", and eight infosec roles reached the live
    # results because of it.
    "Security Operations Analyst",
    "Security Operations Analyst - Weekend 4x10 Shift",
    "Cyber Threat Analyst",
    "Information Security Analyst II",
    "QA Automation Analyst",
    "Quality Assurance Analyst",
    "Laboratory Analyst",
    "Clinical Research Analyst",
])
def test_different_professions_are_rejected(title):
    assert title_tier(title_norm(title)).tier == "N"


@pytest.mark.parametrize("title", [
    # A different INDUSTRY is not a different profession. Alli is industry
    # agnostic, so credit, claims, fraud and compliance analytics are analytics
    # jobs and must survive. Over-tightening the negative list is the easy way
    # to silently throw away most of the market.
    "Credit Risk Analyst",
    "Claims Operations Analyst",
    "Fraud Data Analyst",
    "Data Scientist - Fraud",
    "Data Analyst, Go-To-Market Sales Insights",
    "Data Analyst, Clinical Data Effectiveness",
    "Compliance Data Analyst",
])
def test_domain_analytics_roles_survive(title):
    assert title_tier(title_norm(title)).tier not in ("N", None), title


# --- clearance ----------------------------------------------------------------
# Alli asked for anything needing a clearance to be dropped. The old group caught
# "security clearance ... required" and "must hold ... clearance" and missed the
# commonest federal phrasing, "must be able to OBTAIN and maintain" one, which
# put a Veterans Affairs role in front of her.

@pytest.mark.parametrize("sentence", [
    "Must be able to obtain and maintain the required federal public trust clearance for this role.",
    "An active security clearance is required.",
    "Must possess a current Top Secret clearance.",
    "Candidates must hold a TS/SCI clearance with polygraph.",
    "Ability to obtain a security clearance.",
    "Applicants must be eligible for a public trust determination.",
    "A full-scope polygraph is required.",
    "US Government clearance is mandatory for this position.",
])
def test_a_posting_needing_a_clearance_is_refused(sentence):
    assert sponsorship_text_verdict(sentences(sentence)).verdict == "requires_clearance"


@pytest.mark.parametrize("sentence", [
    "No security clearance is required for this role.",
    "This position does not require a security clearance.",
    "Clearance is not required.",
    "Experience with data clearance workflows in retail inventory.",
])
def test_saying_a_clearance_is_not_needed_keeps_the_posting(sentence):
    """The opposite sentence is a reason to KEEP a job. A pattern that reads the
    word and not the polarity throws away exactly the roles Alli can take."""
    assert sponsorship_text_verdict(sentences(sentence)).verdict != "requires_clearance"


def test_the_work_authorization_rule_still_holds_with_clearance_added():
    """Load-bearing. Alli is authorized on OPT, and this sentence must never
    filter a posting out. Broadening a neighbouring pattern is exactly when it
    would silently start to."""
    v = sponsorship_text_verdict(sentences(
        "Applicants must be authorized to work in the United States."))
    assert v.verdict == "silent"
