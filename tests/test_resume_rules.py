"""Resume rules, each with a case that passes and a case that fails.

The negative cases are transcribed from things that actually happened. The
shipped 14 September resume fails three of these, and the first real tailored
variant tripped the number check three times on a regex bug.
"""

from __future__ import annotations

import copy

import pytest

from jobradar.resume.model import Bullet
from jobradar.resume.rules import check, fabrication_check


def _doc(**over):
    base = {
        "identity": {"name": "A", "phone": "1", "email": "a@b.c", "links": []},
        "summary": "Master's graduate with a Bachelor of Technology in Data Science.",
        "education": [{"id": "edu.x", "degree": "Bachelor of Technology in Data Science",
                       "school": "S", "location": "L", "grade": "1", "dates": "d",
                       "honors": ""}],
        "skills": [{"id": f"skl.{i}", "name": f"G{i}", "items": ["SQL"]} for i in range(5)],
        "experience": [{"id": "exp.a", "employer": "E", "title": "T", "location": "L",
                        "dates": "d", "bullets": [
                            {"id": "exp.a.b1", "text": "Engineered a pipeline saving 25 hours."}]}],
        "projects": [{"id": "proj.a", "name": "P", "role": "R", "dates": "d",
                      "bullet": {"id": "proj.a.b1", "text": "Trained a model with 91% accuracy."}}],
    }
    base.update(over)
    return base


def _hard(doc, rule):
    return [f for f in check(doc) if f.rule == rule and f.severity == "hard"]


def test_clean_document_has_no_hard_failures():
    assert not [f for f in check(_doc()) if f.severity == "hard"]


def test_btech_must_not_be_shortened():
    """The shipped resume reads "BTech in Data Science"."""
    doc = _doc()
    doc["education"][0]["degree"] = "BTech in Data Science"
    doc["summary"] = "Master's graduate."
    assert _hard(doc, "btech_never_shortened")


@pytest.mark.parametrize("text", ["A dash — here", "A range 2020–2021"])
def test_em_and_en_dashes_are_rejected(text):
    doc = _doc()
    doc["experience"][0]["bullets"][0]["text"] = text
    assert _hard(doc, "no_em_dash")


def test_repeated_opening_verb_is_rejected():
    """The shipped resume opens two bullets "Designed" and two "Built"."""
    doc = _doc()
    doc["projects"][0]["bullet"]["text"] = "Engineered another thing entirely."
    found = _hard(doc, "no_repeated_verb")
    assert found and "engineered" in found[0].message


def test_five_skill_groups_exactly():
    doc = _doc()
    doc["skills"] = doc["skills"][:4]
    assert _hard(doc, "five_skill_groups")


def test_never_add_list_is_enforced():
    doc = _doc()
    doc["skills"][0]["items"] = ["SQL", "Apache Airflow"]
    assert _hard(doc, "never_add")


def test_dbt_and_airbyte_are_allowed():
    """Confirmed genuine on 15 Sep 2026; the handover's never-add list was stale."""
    doc = _doc()
    doc["skills"][0]["items"] = ["SQL", "dbt", "Airbyte"]
    assert not _hard(doc, "never_add")


def test_headed_is_rejected():
    doc = _doc()
    doc["experience"][0]["bullets"][0]["text"] = "Headed a data pipeline."
    assert _hard(doc, "no_headed")


def test_sponsorship_language_is_rejected_in_the_summary():
    doc = _doc(summary="Bachelor of Technology graduate seeking an opportunity, open to visa sponsorship.")
    assert _hard(doc, "no_sponsorship_language")


@pytest.mark.parametrize("bad,rule", [
    ("Built an agro-ecological model.", "spelling"),
    ("Built a data based recommendation.", "spelling"),
    ("Built Python based workflows.", "spelling"),
])
def test_spellings(bad, rule):
    doc = _doc()
    doc["experience"][0]["bullets"][0]["text"] = bad
    assert _hard(doc, rule)


def _sourced(text, metrics):
    src = Bullet(id="exp.a.b1", text="x", verb_class="", metrics=tuple(metrics), tools=())
    doc = _doc()
    doc["experience"][0]["bullets"][0] = {"id": "exp.a.b1", "text": text, "source": src}
    return doc


def test_a_number_not_in_the_source_bullet_is_rejected():
    doc = _sourced("Engineered a pipeline generating $500 monthly.", ["$20K"])
    assert [f for f in fabrication_check(doc) if f.rule == "no_invented_metric"]


def test_numbers_present_in_the_source_pass():
    doc = _sourced("Engineered a pipeline generating $20K monthly.", ["$20K"])
    assert not fabrication_check(doc)


@pytest.mark.parametrize("token", ["22K+", "75K+", "2000+", "$3.6M", "0.97", "12%"])
def test_magnitude_and_plus_survive_tokenisation(token):
    """A regex that stopped at K reported a fabricated "22K" against a source
    whose ledger said "22K+". Three false positives on the first real variant."""
    doc = _sourced(f"Engineered a pipeline over {token} records.", [token])
    assert not fabrication_check(doc)
