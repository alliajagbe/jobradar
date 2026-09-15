"""The brief: everything needed to write a tailored variant, in one file.

Written for a human (or for Claude Code in session) to read, because that is who
does the rewriting. There is no API key in this project and none is obtainable,
so the loop is: this file out, a variant file back, then render.

The two parts that earn their place are the character budgets and the
pre-filled skeleton. Budgets, because the content is within one percent of the
page and "add a keyword" is not free: something has to come out, and the writer
should know that before starting rather than after a failed render. The
skeleton, because it means no id is ever guessed and no section silently
dropped.
"""

from __future__ import annotations

from datetime import date

from .gap import analyse
from .html import tokens
from .model import Master

# Roughly how many characters of Charter fit a full line at the locked layout.
# Measured, not assumed: see tests/test_resume_layout.py.
CHARS_PER_LINE = 121


def write(master: Master, job: dict, jd_text: str, jd_sha: str, slug: str) -> str:
    t = tokens()
    gap = analyse(master, jd_text, job.get("company", ""))
    sp = job.get("sponsorship") or {}
    L: list[str] = []
    a = L.append

    a(f"# TAILORING BRIEF - {job['company']} / {job['title']}")
    a(f"generated {date.today().isoformat()} - job id `{job['id']}`")
    a(f"jd_sha `{jd_sha}` - {len(jd_text)} chars - board `{job.get('board','?')}`")
    a(f"variant to write: `~/.jobradar/variants/{slug}.yaml`")
    a("")
    a("## 1. The job")
    a(f"- location    {', '.join(job.get('locations') or []) or 'not stated'}"
      f"{'  (remote)' if job.get('is_remote') else ''}")
    a(f"- url         {job.get('url','')}")
    a(f"- jobradar    score {job.get('score')} - tier {job.get('title_tier')}"
      f" - min years {job.get('min_years')}")
    a(f"- sponsorship {sp.get('signal','?')} ({sp.get('detail','')})")
    a("  FOR CONTEXT ONLY. Sponsorship is never referenced in the resume.")
    a("")

    a("## 2. Gap analysis")
    a("")
    a("### Strong - the posting asks for it and it is already on the resume")
    a("| term | JD hits | where |")
    a("|---|---|---|")
    for term, hits, where in gap.strong[:22]:
        a(f"| {term} | {hits} | {where} |")
    a("")
    a("### Underrepresented - confirmed and in a master pool, but NOT currently shown")
    a("This is the free win: adding these fabricates nothing.")
    a("| term | JD hits | pool |")
    a("|---|---|---|")
    for term, hits, owner in gap.underrepresented[:18]:
        a(f"| {term} | {hits} | {owner} |")
    a("")
    a("### Missing - genuinely absent. Flag only, never write.")
    a("| term | JD hits |")
    a("|---|---|")
    for term, hits in gap.missing[:14]:
        a(f"| {term} | {hits} |")
    a("")
    if gap.forbidden:
        a("### FORBIDDEN - the posting asks, the never-add list refuses. Hard-fails render.")
        a("| term | JD hits |")
        a("|---|---|")
        for term, hits in gap.forbidden:
            a(f"| {term} | {hits} |")
        a("")

    a("## 3. Budgets")
    a(f"Layout: {t['font_stack'].split(',')[0]} {t['body_pt']}pt, "
      f"{t['margin_in']}in margins, ~{CHARS_PER_LINE} chars per full line.")
    a("")
    a("| slot | max lines | char budget |")
    a("|---|---|---|")
    a(f"| summary | exactly 3 | {CHARS_PER_LINE*3-30}-{CHARS_PER_LINE*3-8} |")
    a(f"| each skills row | 1 | <= {CHARS_PER_LINE-14} after the bold label |")
    a(f"| each experience bullet | <= 2 | <= {CHARS_PER_LINE*2-12} |")
    a(f"| each project bullet | <= 2 | <= {CHARS_PER_LINE*2-12} |")
    a("")
    a("The page is within one percent of full. Anything added is paid for by a cut.")
    a("")

    a("## 4. Master content")
    for role in master.raw["experience"]:
        drop = "  (DROPPABLE to fit)" if role.get("droppable") else ""
        a(f"### {role['id']} - {role['employer']} | {role['title']} | {role['dates']}{drop}")
        for node in role["bullets"]:
            b = master.bullets[node["id"]]
            a(f"- `{b.id}` [{b.verb_class}] {len(b.text)} chars - "
              f"metrics {list(b.metrics)} tools {list(b.tools)}")
            a(f"  > {b.text}")
            for alt in node.get("alts") or ():
                ab = master.bullets[alt["id"]]
                a(f"  - alt `{ab.id}` {len(ab.text)} chars - metrics {list(ab.metrics)}")
                a(f"    > {ab.text}")
    a("### projects (all four are kept, one bullet each)")
    for proj in master.raw["projects"]:
        b = master.bullets[proj["bullet"]["id"]]
        a(f"- `{b.id}` {len(b.text)} chars - metrics {list(b.metrics)}")
        a(f"  > {b.text}")
    a("")
    a("### skill pools (select and reorder; never add)")
    for g in master.raw["skills"]:
        shown = set(g.get("default") or [])
        items = ", ".join(i if i in shown else f"{i}*" for i in g["items"])
        a(f"- `{g['id']}` **{g['name']}**: {items}")
    a("  (* = in the pool but not currently shown)")
    a("")

    a("## 5. Rules")
    a("- summary EXACTLY 3 rendered lines; each experience bullet AT MOST 2")
    a("- no em or en dashes; no 'headed'; no sponsorship language in the summary")
    a("- 'Bachelor of Technology' never shortened; exactly 5 skill groups")
    a("- one bullet per project; never repeat an opening verb anywhere")
    a("- every number and tool must come from the SOURCE bullet named in `from:`")
    a("- verbs: Analytics Evaluated/Investigated/Synthesized/Examined/Interpreted/"
      "Identified/Quantified/Measured/Diagnosed/Assessed/Uncovered; "
      "Engineering Engineered/Architected/Automated/Implemented/Integrated/"
      "Modernized/Streamlined/Orchestrated/Constructed/Developed; "
      "Business Partnered/Collaborated/Enabled/Facilitated/Presented/Advised/"
      "Translated/Delivered/Conducted; ML Trained/Optimized/Validated/Calibrated/"
      "Fine-tuned/Designed; Dashboards Designed/Created/Delivered/Produced/Implemented")
    a("")

    a("## 6. Write this file")
    a("```yaml")
    a("schema: 1")
    a(f"job: {{id: \"{job['id']}\", company: \"{job['company']}\", "
      f"title: \"{job['title']}\", jd_sha: {jd_sha}}}")
    a("summary: >-")
    a("  <3 rendered lines>")
    a("skills:")
    for g in master.raw["skills"]:
        a(f"  - {{id: {g['id']}, items: []}}   # {g['name']}: select and reorder")
    a("experience:")
    for role in master.raw["experience"]:
        a(f"  - role: {role['id']}")
        a("    bullets:")
        for node in role["bullets"]:
            a(f"      - {{from: {node['id']}, text: \"\"}}")
    a("projects:")
    for proj in master.raw["projects"]:
        a(f"  - {{id: {proj['id']}, bullet: {{from: {proj['bullet']['id']}, text: \"\"}}}}")
    a("```")
    a("An empty `text` keeps the master wording. `from:` is mandatory.")
    a("")
    a("## 7. Full job description")
    a("")
    a(jd_text)
    return "\n".join(L)
