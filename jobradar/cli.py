"""JobRadar command line.

Usage:
  python -m jobradar refresh [--source NAME] [--token SLUG] [--limit N]
  python -m jobradar publish
  python -m jobradar stats [--histogram]
  python -m jobradar discover --from-lca [--top N] [--apply]
  python -m jobradar crawl [--limit N] [--apply]
  python -m jobradar tailor brief --find "intel data analyst"
  python -m jobradar tailor render --slug IntelDataAnalyst
  python -m jobradar sponsorship [--years N] [--row-limit N] [--keep-xlsx]
  python -m jobradar verify-boards

Discovery and anything that rewrites a seed file is a dry run until --apply.
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys

from . import config, publish as publish_mod, refresh as refresh_mod, store


def _log(message: str) -> None:
    print(message, flush=True)


def cmd_refresh(args: argparse.Namespace) -> int:
    jobs, report = refresh_mod.run(
        only_source=args.source, only_token=args.token,
        limit_boards=args.limit, progress=_log if args.verbose else None,
    )
    store.save(jobs)
    total, open_ = publish_mod.publish(jobs, report)

    _log("")
    _log(f"boards      {report.boards_ok} ok, {report.boards_failed} failed "
         f"of {report.boards_total}")
    _log(f"postings    {report.postings_seen} seen, {report.new} new")
    _log(f"kept        {report.kept}")
    _log(f"closed      {report.closed}")
    if report.dropped:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(report.dropped.items()))
        _log(f"filtered    {parts}")
    _log(f"published   {total} cards, {open_} open")
    for error in report.errors:
        _log(f"  ! {error}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    total, open_ = publish_mod.publish(list(store.load().values()))
    _log(f"published {total} cards, {open_} open")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    jobs = list(store.load().values())
    if not jobs:
        _log("No data. Run `python -m jobradar refresh` first.")
        return 0
    open_jobs = [j for j in jobs if j["status"] == "open"]
    _log(f"{len(jobs)} records: {len(open_jobs)} open, "
         f"{sum(1 for j in jobs if j['status'] == 'dropped')} filtered, "
         f"{sum(1 for j in jobs if j['status'] == 'closed')} closed")

    reasons = collections.Counter(
        j["drop_reason"].split(":")[0] for j in jobs if j.get("drop_reason")
    )
    if reasons:
        _log("filtered by: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common()))

    signals = collections.Counter(j["sponsorship"]["signal"] for j in open_jobs)
    _log("sponsorship: " + ", ".join(f"{k} {v}" for k, v in signals.most_common()))

    sources = collections.Counter(j["source"] for j in open_jobs)
    _log("sources:     " + ", ".join(f"{k} {v}" for k, v in sources.most_common()))

    if args.histogram:
        # The tuning tool for config.SKILL_SATURATION. A spike at the top of the
        # skills band means the constant is too low and everything saturates.
        _log("")
        buckets = collections.Counter((j["score"] // 5) * 5 for j in open_jobs)
        peak = max(buckets.values()) if buckets else 1
        for bucket in sorted(buckets):
            bar = "#" * max(1, round(40 * buckets[bucket] / peak))
            _log(f"  {bucket:3}-{bucket+4:<3} {bar} {buckets[bucket]}")
        scores = sorted(j["score"] for j in open_jobs)
        if scores:
            _log(f"  median {scores[len(scores)//2]}, max {scores[-1]}")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from . import sponsorship
    from .discovery import discover, save_probes, write_discovered

    employers = sponsorship.employer_table()
    if not employers:
        _log("No sponsorship data. Run `python -m jobradar sponsorship` first.")
        return 1

    ranked = sorted(employers.values(), key=lambda e: -e.analyst_certified)
    companies = [e.display_name for e in ranked[:args.top]]
    _log(f"probing {len(companies)} top analyst sponsors")

    found, probes = discover(
        companies, max_probes=args.max_probes,
        progress=_log if args.verbose else None,
    )
    save_probes(probes)
    _log(f"\n{len(found)} boards found from {len(probes)} probes on record")
    for row in found[:40]:
        _log(f"  {row['company'][:34]:34} {row['source']}/{row['token']}")
    if args.apply:
        total = write_discovered(found)
        _log(f"\nwrote seeds/discovered.csv, {total} boards total")
    else:
        _log("\nDry run. Pass --apply to write seeds/discovered.csv.")
    return 0


def cmd_sponsorship(args: argparse.Namespace) -> int:
    from . import lca

    files = lca.latest_per_year(lca.find_lca_files(), years=args.years)
    if not files:
        _log("No LCA files found on the DOL performance page.")
        return 1
    _log("ingesting one file per fiscal year (the quarterly files are cumulative):")
    paths = []
    for name, url in files:
        _log(f"  {name}")
        paths.append(lca.download(url, name, progress=_log))

    totals = lca.ingest(paths, progress=_log, row_limit=args.row_limit)
    _log("")
    lca.verify(totals, progress=_log)
    written = lca.write_table(totals)
    size = config.SPONSORS_CSV_GZ.stat().st_size
    _log(f"\nwrote {config.SPONSORS_CSV_GZ.name}: {written:,} employers, {size >> 10} KB")

    if not args.keep_xlsx:
        for path in paths:
            path.unlink(missing_ok=True)
        _log("removed the source workbooks")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    """Find boards from the Common Crawl index, keep the ones hiring analysts.

    The two halves are separable on purpose. Enumeration reads Common Crawl,
    which throttles cloud IPs hard enough that a GitHub runner gets 503 where a
    laptop sails through. Validation reads the ATS APIs, which do not care where
    the request comes from. So enumeration runs locally and commits its token
    list, and the long validation pass runs on the runner.
    """
    import csv as _csv

    from . import crawl

    if args.from_file:
        candidates = _read_tokens(config.CC_TOKENS_CSV)
        if not candidates:
            _log(f"No tokens in {config.CC_TOKENS_CSV}. Run `crawl --enumerate` first.")
            return 1
        _log(f"loaded {sum(len(v) for v in candidates.values())} tokens from "
             f"{config.CC_TOKENS_CSV.name}")
        return _validate(candidates, args)

    index_id = args.index or crawl.latest_index()
    _log(f"reading board tokens from Common Crawl index {index_id}")
    sources = [s for s in (args.sources.split(",") if args.sources
                           else crawl.DEFAULT_SOURCES) if s.strip()]
    candidates = {}
    for source in sources:
        try:
            candidates[source] = crawl.tokens_for(
                source, index_id, progress=_log if args.verbose else None)
        except crawl.CrawlError as exc:
            # One throttled source must not end the run, but it must not look
            # like a clean zero either.
            _log(f"  ! {source}: {exc}")
            continue
        _log(f"  {source}: {len(candidates[source])} tokens")
    if not candidates:
        _log("")
        _log("Read no board tokens at all. Common Crawl throttles cloud IPs "
             "hard, so a runner often gets 503 where a laptop does not. "
             "Re-run, or run it locally with `python -m jobradar crawl --apply`.")
        return 1
    if args.limit:
        candidates = {s: set(sorted(t)[:args.limit]) for s, t in candidates.items()}

    if args.enumerate_only:
        config.CC_TOKENS_CSV.parent.mkdir(parents=True, exist_ok=True)
        with config.CC_TOKENS_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = _csv.writer(fh)
            writer.writerow(["source", "token"])
            for source in sorted(candidates):
                for token in sorted(candidates[source]):
                    writer.writerow([source, token])
        total = sum(len(t) for t in candidates.values())
        _log(f"wrote {config.CC_TOKENS_CSV}, {total} tokens")
        return 0

    return _validate(candidates, args)


def _read_tokens(path) -> dict[str, set[str]]:
    import csv as _csv
    if not path.exists():
        return {}
    out: dict[str, set[str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in _csv.DictReader(fh):
            out.setdefault(row["source"], set()).add(row["token"])
    return out


def _validate(candidates: dict, args: argparse.Namespace) -> int:
    import csv as _csv

    from . import crawl

    known = set()
    for path in (config.BOARDS_CSV, config.SEEDS_DIR / "discovered.csv"):
        if path.exists():
            with path.open(newline="", encoding="utf-8") as fh:
                known.update((r["source"], r["token"]) for r in _csv.DictReader(fh))
    total = sum(len(t) for t in candidates.values())
    _log("")
    _log(f"checking {total} candidates against {len(known)} already known")

    kept = crawl.live_boards(candidates, known=known, workers=args.workers,
                             progress=_log)
    kept.sort(key=lambda r: -r["hits"])
    _log("")
    _log(f"{len(kept)} boards carry at least one target role")
    for row in kept[:30]:
        _log(f"  {row['hits']:3} of {row['total']:4}  {row['company'][:30]:30} "
             f"{row['source']}/{row['token']}")

    if args.apply:
        from .discovery import write_discovered
        written = write_discovered([{k: r[k] for k in
                                     ("company", "source", "token", "site", "wd_num")}
                                    for r in kept])
        _log(f"wrote seeds/discovered.csv, {written} boards total")
    else:
        _log("Dry run. Pass --apply to write seeds/discovered.csv.")
    return 0


def cmd_verify_boards(args: argparse.Namespace) -> int:
    """Probe every seeded board. A seed file full of dead tokens costs a failed
    board every run and teaches you to ignore the error log."""
    from concurrent.futures import ThreadPoolExecutor

    from .sources import build_sources, http

    sources = build_sources()
    rows = [r for r in csv.DictReader(config.BOARDS_CSV.open(newline="", encoding="utf-8"))
            if r["source"] != "workday"]

    def probe(row):
        try:
            return row, sources[row["source"]].probe(row["token"], row["company"]).verdict
        except Exception as exc:                      # noqa: BLE001
            return row, f"error:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, rows))
    http.close()

    bad = [(r, v) for r, v in results if v != "confirmed"]
    _log(f"{len(results) - len(bad)}/{len(results)} boards confirmed")
    for row, verdict in bad:
        _log(f"  {verdict:16} {row['company']} -> {row['source']}/{row['token']}")
    return 1 if bad else 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .serve import run
    return run(port=args.port, verbose=args.verbose)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobradar", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="per-board progress")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("refresh", help="pull boards, score, publish")
    p.add_argument("--source", help="only this ATS")
    p.add_argument("--token", help="only this board slug")
    p.add_argument("--limit", type=int, help="only the first N boards")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("publish", help="rewrite docs/data from the store")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("stats", help="what is in the store")
    p.add_argument("--histogram", action="store_true", help="score distribution")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("discover", help="probe for new boards")
    p.add_argument("--from-lca", action="store_true", default=True)
    p.add_argument("--top", type=int, default=300, help="top N sponsors to probe")
    p.add_argument("--max-probes", type=int, default=1500)
    p.add_argument("--apply", action="store_true", help="write seeds/discovered.csv")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("sponsorship", help="ingest DOL H-1B filing data")
    p.add_argument("--years", type=int, default=2, help="fiscal years to ingest")
    p.add_argument("--row-limit", type=int, help="stop early, for a smoke test")
    p.add_argument("--keep-xlsx", action="store_true", help="keep the 252MB source files")
    p.set_defaults(func=cmd_sponsorship)

    from .resume.cli import add_parser as _add_tailor
    _add_tailor(sub)

    p = sub.add_parser("crawl", help="find every board via the Common Crawl index")
    p.add_argument("--index", help="Common Crawl index id (default: newest)")
    p.add_argument("--sources", help="comma separated, default all crawlable")
    p.add_argument("--limit", type=int, help="only the first N tokens per source")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--enumerate", dest="enumerate_only", action="store_true",
                   help="only read tokens from Common Crawl, write data/cc_tokens.csv")
    p.add_argument("--from-file", action="store_true",
                   help="validate the tokens in data/cc_tokens.csv (skips Common Crawl)")
    p.add_argument("--apply", action="store_true", help="write seeds/discovered.csv")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("serve", help="local helper the page's Tailor button talks to")
    p.add_argument("--port", type=int, default=config.HELPER_PORT)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("verify-boards", help="probe every seeded board")
    p.set_defaults(func=cmd_verify_boards)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
