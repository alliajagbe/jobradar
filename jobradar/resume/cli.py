"""`jobradar tailor ...` subcommands.

Three steps, matching how the work actually happens: `brief` gathers, a person
writes the variant, `render` validates and prints. `check` is the dry run of
`render`, which maps this project's existing dry-run-until-apply convention onto
a shape that reads better here, because the report is the useful output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import yaml
from pathlib import Path

from . import brief as brief_mod
from . import fit as fit_mod
from . import html as H
from . import jd as jd_mod
from . import measure as M
from .model import default_document, load_master, resolve_variant
from .paths import HOME, ResumeError, output_dir, resolve
from .rules import check as check_rules, fabrication_check


def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _report(findings, metrics=None) -> int:
    hard = [f for f in findings if f.severity == "hard"]
    warn = [f for f in findings if f.severity == "warn"]
    if metrics is not None:
        room = (f"fits, {metrics.free_px()}px spare (~{metrics.free_lines()} lines)"
                if metrics.fits_one_page else f"OVER by {metrics.overflow_px()}px")
        _log(f"  layout   {metrics.height_px}px of {M.PAGE_BUDGET} ({room})"
             f"   indents {len(metrics.lefts)}   font {'ok' if metrics.font_ok else 'FALLBACK'}")
        s = metrics.slots.get("summary")
        if s:
            _log(f"  summary  {s['lines']} lines, last line {s['last_frac']:.0%} of the measure")
    _log(f"  rules    {len(hard)} hard, {len(warn)} warnings")
    for f in hard:
        _log(f"    {f}")
    for f in warn:
        _log(f"    {f}")
    return len(hard)


def build_brief(url: str, *, slug: str | None = None,
                progress=None) -> tuple[Path, dict, str]:
    """Fetch a posting and write its brief. Returns (brief_path, job, slug).

    Extracted from cmd_brief so the local helper can call it. cmd_brief read five
    interdependent Namespace attributes and only printed the path it wrote, so a
    request handler would have had to fake a Namespace and scrape stdout.
    """
    from . import ingest

    master = load_master()
    posting = ingest.fetch(url, progress=progress)
    job = _job_from_posting(posting)
    slug = slug or jd_mod.slug_for(job)
    out = resolve("briefs", f"{slug}.md", create_parent=True)
    out.write_text(brief_mod.write(master, job, posting.text, "link", slug),
                   encoding="utf-8")
    return out, job, slug


def cmd_brief(args: argparse.Namespace) -> int:
    master = load_master()
    if getattr(args, "url", None):
        out, job, slug = build_brief(args.url, slug=args.slug, progress=_log)
        _log(f"\n  {job['company']} - {job['title']} ({len(job['snippet'])}+ chars)")
        _log(f"wrote {out}")
        _log(f"next: write ~/.jobradar/variants/{slug}.yaml, then "
             f"`python -m jobradar tailor render --slug {slug}`")
        return 0

    if args.find:
        matches = jd_mod.find_jobs(args.find, limit=6)
        if not matches:
            _log("no match in data/jobs.jsonl")
            return 1
        job = matches[0]
        if len(matches) > 1:
            _log(f"matched: {job['company']} - {job['title']}")
            _log("other candidates:")
            for m in matches[1:4]:
                _log(f"  {m['company'][:20]:20} {m['title'][:46]}")
            _log("")
    else:
        job = jd_mod.job_by_id(args.id)

    slug = args.slug or jd_mod.slug_for(job)
    if args.jd_file:
        text = Path(args.jd_file).read_text(encoding="utf-8")
        sha = "manual"
        _log(f"  using {args.jd_file} ({len(text)} chars)")
    else:
        text, sha = jd_mod.fetch_description(job, refetch=args.refetch, progress=_log)

    out = resolve("briefs", f"{slug}.md", create_parent=True)
    out.write_text(brief_mod.write(master, job, text, sha, slug), encoding="utf-8")
    _log(f"\nwrote {out}")
    _log(f"next: write ~/.jobradar/variants/{slug}.yaml, then "
         f"`python -m jobradar tailor render --slug {slug}`")
    return 0


def _job_from_posting(posting) -> dict:
    """A jobs.jsonl-shaped record for a posting that was never crawled.

    Scored and sponsorship-matched the same way a crawled posting is, so a link
    pasted by hand carries the same signals as one the crawler found. There is
    no reason a job should be worth less because of how it arrived.
    """
    from .. import normalize, score as scoring, sponsorship, taxonomy
    from ..matching import Matcher
    from ..refresh import load_profile

    profile = load_profile()
    preferred = {c.lower() for c in (profile.get("locations", {}).get("preferred") or [])}
    title_n = normalize.title_norm(posting.title)
    match = taxonomy.title_tier(title_n)
    location = normalize.parse_locations(
        getattr(posting, "location", "") or None, None, posting.text, preferred)
    verdict = taxonomy.sponsorship_text_verdict(normalize.sentences(posting.text))

    employers = sponsorship.employer_table()
    stats = method = mscore = None
    if employers:
        matcher = Matcher(list(employers))
        result = matcher.match(posting.company)
        stats = employers.get(result.norm_name) if result.norm_name else None
        method, mscore = result.method, result.score
    signal = sponsorship.resolve(text_verdict=verdict, stats=stats,
                                 match_method=method or "no-data", match_score=mscore,
                                 is_us=location.is_us)
    penalty, level = normalize.title_level_penalty(posting.title)
    scored = scoring.score_job(
        title_tier=match.tier, title_term=match.term, description_text=posting.text,
        location=location, posted_at=None, sponsorship_signal=signal.signal,
        sponsorship_detail=signal.detail, level_penalty=penalty, level_label=level,
        profile_skills=set(profile.get("skills_i_have") or []))
    return {
        "id": f"link:{posting.company.lower().replace(' ', '')}:{abs(hash(posting.url)) % 10**10}",
        "board": posting.board_key or "link", "company": posting.company,
        "title": posting.title, "url": posting.url, "source": posting.source,
        "locations": list(location.locations), "is_remote": location.is_remote,
        "is_us": location.is_us, "score": scored.score, "explain": scored.components,
        "matched_skills": scored.matched_skills, "missing_skills": scored.missing_skills,
        "min_years": scored.min_years, "title_tier": match.tier,
        "sponsorship": signal.as_dict(), "status": "open", "drop_reason": None,
        "snippet": posting.text[:400],
    }


def _load_doc(args) -> tuple[dict, str]:
    master = load_master()
    if args.slug in (None, "") or args.baseline:
        return default_document(master), "baseline"
    path = resolve("variants", f"{args.slug}.yaml")
    if not path.exists():
        raise ResumeError(f"no variant at {path}")
    variant = yaml.safe_load(path.read_text(encoding="utf-8"))
    return resolve_variant(master, variant), args.slug


def cmd_check(args: argparse.Namespace) -> int:
    doc, slug = _load_doc(args)
    t = H.tokens()
    metrics = M.render(H.build(doc), None, font_probe=t["font_probe"])
    _log(f"checking {slug}")
    hard = _report(check_rules(doc, metrics) + fabrication_check(doc), metrics)
    if hard:
        _log("\nNo PDF would be written. Fix the hard failures above.")
    else:
        _log("\nClean.")
    return 1 if hard else 0


def cmd_render(args: argparse.Namespace) -> int:
    doc, slug = _load_doc(args)
    t = H.tokens()
    _log(f"rendering {slug}")

    result = fit_mod.fit(doc, font_probe=t["font_probe"], progress=_log)
    if not result.fits:
        _log(f"\nStill {result.metrics.overflow_px()}px over after every fit step.")
        _log("Everything else is protected, so the tool stops here rather than choosing.")
        _log("Longest slots, with roughly how much to cut to drop a line:")
        for slot, length, cut in fit_mod.longest_slots(result.document, result.metrics):
            _log(f"    {slot:22} {length:4} chars   cut ~{cut}")
        return 1

    page = H.build(result.document, overrides=result.overrides,
                   inline_honors=result.inline_honors, drop_honors=result.drop_honors)
    name = f"AlliAjagbe{slug}Resume.pdf" if slug != "baseline" else "AlliAjagbeResume.pdf"
    # The PDF goes somewhere Finder will show. The working files (html, report)
    # stay in ~/.jobradar next to the brief and the variant.
    pdf_dir = Path(args.out).expanduser() if args.out else output_dir()
    pdf_dir.mkdir(parents=True, exist_ok=True)
    out_dir = resolve("out", slug, create_parent=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdf_dir / name
    if pdf.exists() and not args.force:
        raise ResumeError(f"{pdf} exists. Pass --force to overwrite; you may have "
                          f"already submitted this one.")

    metrics = M.render(page, pdf, font_probe=t["font_probe"])
    findings = check_rules(result.document, metrics) + fabrication_check(result.document)
    findings += _ats_checks(pdf, result.document)

    if result.applied:
        _log("\n  fit steps applied:")
        for step in result.applied:
            _log(f"    - {step}")
    _log("")
    hard = _report(findings, metrics)
    (out_dir / "resume.html").write_text(page, encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps(
        {"slug": slug, "height_px": metrics.height_px, "pages": metrics.pages,
         "indents": metrics.lefts, "font_ok": metrics.font_ok,
         "fit_applied": result.applied,
         "slots": metrics.slots,
         "findings": [{"rule": f.rule, "severity": f.severity, "slot": f.slot,
                       "message": f.message} for f in findings]}, indent=1), encoding="utf-8")

    if hard:
        pdf.unlink(missing_ok=True)
        _log(f"\n{hard} hard failures. PDF deleted; {out_dir/'resume.html'} kept for inspection.")
        return 1
    _log(f"\nwrote {pdf}")
    _log(f"  working files: {out_dir}")
    # Close the loop for a job that came from the page's Tailor button. Silent
    # when the slug was never queued, which is the ordinary CLI case.
    from . import queue as Q
    try:
        if Q.read(slug) is not None:
            Q.update(slug, status=Q.READY, pdf=str(pdf), error=None)
            notify("JobRadar", f"Resume ready: {pdf.name}")
    except ResumeError:
        pass
    if args.open:
        subprocess.run(["open", str(pdf)], check=False)
    if args.reveal:
        subprocess.run(["open", "-R", str(pdf)], check=False)
    return 0


def _ats_checks(pdf: Path, doc: dict):
    """Verify the PDF's text layer, because twenty already-submitted resumes have
    an email no regex matches and nothing in the build caught it."""
    from .rules import Finding
    out = []
    try:
        text = M.extract_text(pdf)
    except ResumeError as exc:
        return [Finding("ats_extract", "warn", str(exc))]
    email = doc["identity"]["email"]
    if email not in text:
        import re
        broken = re.search(r"[A-Za-z0-9._]+ +[A-Za-z0-9._]*@[A-Za-z0-9.]+", text)
        out.append(Finding("ats_email_intact", "hard",
                           f"the email does not survive extraction"
                           + (f", it reads {broken.group(0)!r}" if broken else "")))
    order = []
    for role in doc["experience"]:
        for b in role["bullets"]:
            head = " ".join(b["text"].split()[:5])
            idx = text.find(head)
            if idx < 0:
                out.append(Finding("ats_bullet_intact", "hard",
                                   "does not extract contiguously", b["id"]))
            else:
                order.append((idx, b["id"]))
    if order != sorted(order):
        out.append(Finding("ats_reading_order", "hard",
                           "bullets extract out of document order"))
    return out


def notify(title: str, message: str) -> None:
    """A macOS notification, with the text passed as an argument.

    Job titles come from third-party postings and are the first untrusted text
    this project puts near a shell. Concatenating them into an AppleScript
    string means a posting called `Analyst" & (do shell script "...") & "` runs
    that shell script. `on run {argv}` keeps the text as data.
    """
    script = ('on run argv\n'
              '  display notification (item 1 of argv) with title (item 2 of argv)\n'
              'end run')
    try:
        subprocess.run(["osascript", "-e", script, message, title],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass          # a missing notification must never fail a render


def cmd_queue(args: argparse.Namespace) -> int:
    from . import queue as Q
    entries = Q.all_entries()
    if not entries:
        _log("queue is empty")
        return 0
    for e in entries:
        flag = "  STALLED" if e.stalled else ""
        _log(f"  {e.status:8} {e.slug:34} {e.company[:18]:18} "
             f"{e.age_minutes:5.0f}m{flag}")
        if e.error:
            _log(f"           {e.error[:90]}")
    pend = Q.pending()
    _log("")
    _log(f"{len(entries)} entries, {len(pend)} waiting for a session")
    return 0


def cmd_take(args: argparse.Namespace) -> int:
    from . import queue as Q
    entry = Q.take(args.slug, note=args.note or "")
    _log(f"{entry.slug}: {entry.status}")
    _log(f"  brief: {entry.brief or '(not written yet)'}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    _log(f"resume home   {HOME}")
    try:
        resolve("resume/master.yaml")
        _log("  outside the repository: yes")
    except ResumeError as exc:
        ok = False
        _log(f"  FAIL {exc}")
    for tool in ("pdftotext", "pdfinfo"):
        found = shutil.which(tool)
        _log(f"{tool:14}{found or 'MISSING'}")
        ok = ok and bool(found)
    try:
        import playwright  # noqa: F401
        _log("playwright    installed")
    except ImportError:
        ok = False
        _log("playwright    MISSING - pip install -r requirements-resume.txt")
    try:
        master = load_master()
        _log(f"master.yaml   {len(master.bullets)} bullets, "
             f"{len(master.raw['experience'])} roles, {len(master.raw['projects'])} projects")
        findings = check_rules(default_document(master))
        hard = [f for f in findings if f.severity == "hard"]
        _log(f"  text rules  {len(hard)} hard failures")
        ok = ok and not hard
    except ResumeError as exc:
        ok = False
        _log(f"master.yaml   FAIL {exc}")
    t = H.tokens()
    m = M.render(H.build({"identity": {"name": "x", "phone": "x", "email": "x@x.x", "links": []},
                          "summary": "x", "education": [], "skills": [],
                          "experience": [], "projects": []}),
                 None, font_probe=t["font_probe"])
    _log(f"font          {t['font_probe']}: {'loaded' if m.font_ok else 'NOT AVAILABLE'}")
    ok = ok and m.font_ok
    _log("\n" + ("all good" if ok else "problems above"))
    return 0 if ok else 1


def add_parser(sub) -> None:
    p = sub.add_parser("tailor", help="per-job tailored resumes")
    inner = p.add_subparsers(dest="tailor_command", required=True)

    b = inner.add_parser("brief", help="gather the JD and gap analysis for one job")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="exact job id or url from data/jobs.jsonl")
    g.add_argument("--find", help='fuzzy match, e.g. "intel data analyst"')
    g.add_argument("--url", help="a job posting link, from any site")
    b.add_argument("--slug", help="override the output slug")
    b.add_argument("--refetch", action="store_true", help="ignore the cached description")
    b.add_argument("--jd-file", help="use a saved description instead of refetching")
    b.set_defaults(func=cmd_brief)

    c = inner.add_parser("check", help="validate without writing a PDF")
    c.add_argument("--slug")
    c.add_argument("--baseline", action="store_true", help="check the untailored master")
    c.set_defaults(func=cmd_check)

    r = inner.add_parser("render", help="validate, fit to one page, write the PDF")
    r.add_argument("--slug")
    r.add_argument("--baseline", action="store_true", help="render the untailored master")
    r.add_argument("--out", help="where to write the PDF (default: ~/Desktop/jobs/<month><year>)")
    r.add_argument("--force", action="store_true", help="overwrite an existing PDF")
    r.add_argument("--open", action="store_true", help="open the PDF when done")
    r.add_argument("--reveal", action="store_true", help="show it in Finder")
    r.set_defaults(func=cmd_render)

    q = inner.add_parser("queue", help="what the page has asked for")
    q.set_defaults(func=cmd_queue)

    tk = inner.add_parser("take", help="claim a queued job before working on it")
    tk.add_argument("slug")
    tk.add_argument("--note")
    tk.set_defaults(func=cmd_take)

    d = inner.add_parser("doctor", help="check the setup")
    d.set_defaults(func=cmd_doctor)
