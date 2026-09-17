"""The formatting and truthfulness rules, as mechanical checks.

Every rule here was previously enforced by reading the document carefully. The
user's own notes call the three-line summary "the single most repeated
correction", which is the clearest possible sign that a human reading a PDF is
the wrong instrument.

Two rules can only be checked against a rendered layout, not against text:
`summary_three_lines` and `bullet_max_two_lines`. That is why the renderer is a
browser. The rest are pure text predicates and are the cheapest thing in the
project to test.

Evidence that these are not hypothetical: run them against the resume that was
shipped on 14 September and four fail. "BTech in Data Science" shortens
"Bachelor of Technology"; "Designed" and "Built" each open two bullets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Never-add list from the handover doc, minus dbt and Airbyte, which the user
# confirmed on 15 Sep 2026 are genuine. The handover was stale on that point.
NEVER_ADD = (
    "Spark", "PySpark", "SAS", "SPSS", "Minitab", "Oracle", "Kibana", "OBIEE",
    "Datanet", "EWFM", "IEX", "Aspect", "Erlang", "JDBC", "Serenity", "NoSQL",
    "Apache Airflow", "Airflow", "Fivetran", "Hex", "Redshift", "H2O", "Scala",
    "Informatica", "Qlik", "Think-cell",
)

_EM_DASH = re.compile(r"[‒–—―−]")
_SHORT_BTECH = re.compile(r"\b(B\.?\s?Tech\b|B\.?\s?T\.?\b|Bachelor of Tech(?!nology))", re.I)
_HEADED = re.compile(r"\bheaded\b", re.I)
_SPONSOR = re.compile(
    r"\bsponsor\w*|\bvisa\b|\bH-?1B\b|\bOPT\b|\bCPT\b|work authoriz\w*|"
    r"seeking (?:an? )?(?:opportunit|role|position)|looking for a (?:role|position)", re.I)
_SPELLING = ((re.compile(r"agro[\s-]ecological", re.I), "agroecological"),
             (re.compile(r"\bdata based\b", re.I), "data-based"),
             (re.compile(r"\bPython based\b", re.I), "Python-based"))
# The suffix must be able to carry BOTH a magnitude and a plus: "22K+" is one
# token, and an alternation that stops at K reports a fabricated "22K" against a
# source whose ledger says "22K+". Three false positives on the first real run.
_NUM = re.compile(r"\$?\d[\d,.]*\s*(?:[KMBx]\b)?\+?%?")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str          # hard | warn
    message: str
    slot: str | None = None

    def __str__(self) -> str:
        where = f" [{self.slot}]" if self.slot else ""
        return f"{self.severity.upper():4} {self.rule}{where}: {self.message}"


def _all_text(doc: dict) -> list[tuple[str, str]]:
    out = [("summary", doc["summary"])]
    for edu in doc["education"]:
        out.append((edu["id"], f"{edu['degree']} {edu['school']} {edu.get('honors') or ''}"))
    for group in doc["skills"]:
        out.append((group["id"], f"{group['name']}: {', '.join(group['items'])}"))
    for role in doc["experience"]:
        for b in role["bullets"]:
            out.append((b["id"], b["text"]))
    for proj in doc["projects"]:
        out.append((proj["bullet"]["id"], proj["bullet"]["text"]))
    return out


def _norm_num(tok: str) -> str:
    return tok.replace(",", "").replace(" ", "").rstrip(".").upper()


def check(doc: dict, metrics=None) -> list[Finding]:
    """Every rule. `metrics` is a measure.Metrics; layout rules are skipped
    without it so `check` can run before a render."""
    out: list[Finding] = []
    texts = _all_text(doc)
    joined = " ".join(t for _, t in texts)

    # --- layout rules, only checkable against a rendered page ---
    if metrics is not None:
        summary = metrics.slots.get("summary")
        if summary and summary["lines"] != 3:
            out.append(Finding("summary_three_lines", "hard",
                               f"summary renders {summary['lines']} lines, must be exactly 3",
                               "summary"))
        for slot, m in metrics.slots.items():
            if slot.startswith(("exp.", "proj.")) and m["lines"] > 2:
                out.append(Finding("bullet_max_two_lines", "hard",
                                   f"renders {m['lines']} lines, max 2", slot))
            if slot.startswith("skl.") and m["lines"] > 1:
                out.append(Finding("skill_row_one_line", "warn",
                                   f"renders {m['lines']} lines", slot))
            if m["lines"] > 1 and m["last_frac"] < 0.30:
                out.append(Finding("stub_line", "warn",
                                   f"last line is {m['last_frac']:.0%} of the measure; "
                                   f"a whole line for very little", slot))
        if len(metrics.lefts) > 2:
            out.append(Finding("indent_levels", "hard",
                               f"{len(metrics.lefts)} distinct bullet indents {metrics.lefts}, "
                               f"expected at most 2"))
        if not metrics.fits_one_page:
            out.append(Finding("one_page", "hard",
                               f"overflows by {metrics.overflow_px()}px "
                               f"(~{max(1, metrics.overflow_px() // 13)} lines)"))
        if metrics.pages not in (None, 1):
            out.append(Finding("one_page", "hard", f"PDF has {metrics.pages} pages"))
        if not metrics.font_ok:
            out.append(Finding("font_loaded", "hard",
                               "the body font did not load, so every measurement above is "
                               "against a fallback and cannot be trusted"))

    # --- text rules ---
    for slot, text in texts:
        if _EM_DASH.search(text):
            out.append(Finding("no_em_dash", "hard", "contains an em or en dash", slot))
        if _HEADED.search(text):
            out.append(Finding("no_headed", "hard",
                               "'headed' describes people and teams, not pipelines", slot))
        for pattern, want in _SPELLING:
            if pattern.search(text):
                out.append(Finding("spelling", "hard", f"use {want!r}", slot))

    if _SHORT_BTECH.search(joined):
        out.append(Finding("btech_never_shortened", "hard",
                           "'Bachelor of Technology' must never be shortened"))
    if "Bachelor of Technology" not in joined:
        out.append(Finding("btech_present", "warn", "'Bachelor of Technology' does not appear"))
    if _SPONSOR.search(doc["summary"]):
        out.append(Finding("no_sponsorship_language", "hard",
                           "the summary mentions sponsorship, work authorization or "
                           "seeking a role", "summary"))
    if len(doc["skills"]) != 5:
        out.append(Finding("five_skill_groups", "hard",
                           f"{len(doc['skills'])} skill groups, must be exactly 5"))
    for group in doc["skills"]:
        if " " in group["name"]:
            out.append(Finding("single_word_group", "warn",
                               f"group name {group['name']!r} is not one word", group["id"]))

    for proj in doc["projects"]:
        if isinstance(proj.get("bullet"), list) and len(proj["bullet"]) > 1:
            out.append(Finding("one_bullet_per_project", "hard",
                               "a project may carry at most one bullet", proj["id"]))

    # opening verbs
    seen: dict[str, str] = {}
    for role in doc["experience"]:
        for b in role["bullets"]:
            _verb(b["id"], b["text"], seen, out)
    for proj in doc["projects"]:
        _verb(proj["bullet"]["id"], proj["bullet"]["text"], seen, out)

    lower = joined.lower()
    for term in NEVER_ADD:
        if re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", lower):
            out.append(Finding("never_add", "hard",
                               f"{term!r} is on the never-add list and is not a confirmed skill"))
    return out


def _verb(slot: str, text: str, seen: dict, out: list) -> None:
    word = re.sub(r"[^a-z]", "", text.split()[0].lower()) if text.split() else ""
    if not word:
        return
    if word in seen:
        out.append(Finding("no_repeated_verb", "hard",
                           f"opening verb {word!r} already used by {seen[word]}", slot))
    else:
        seen[word] = slot


def fabrication_check(doc: dict) -> list[Finding]:
    """Every number and tool in a rewritten bullet must come from its source.

    Scoped to the SOURCE BULLET, not to the master as a whole. The realistic
    failure is not invention, it is migration: a rephrase that pulls Castlery's
    $500 into the Benori bullet because both are about pipelines. Checking
    against the whole master would wave that through.
    """
    out: list[Finding] = []
    nodes = [(b, b.get("source")) for r in doc["experience"] for b in r["bullets"]]
    nodes += [(p["bullet"], p["bullet"].get("source")) for p in doc["projects"]]
    for node, src in nodes:
        if src is None:
            continue
        allowed_nums = {_norm_num(m) for m in src.metrics}
        for tok in _NUM.findall(node["text"]):
            norm = _norm_num(tok)
            if not norm or norm in {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}:
                continue
            if norm not in allowed_nums:
                out.append(Finding("no_invented_metric", "hard",
                                   f"{tok.strip()!r} is not in the source bullet "
                                   f"{src.id}, whose numbers are {sorted(src.metrics)}",
                                   node["id"]))
    return out
