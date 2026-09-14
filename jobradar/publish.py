"""Writing what the page reads.

The store keeps everything. The page gets a trimmed copy, because the browser
downloads it on every visit and full descriptions would make that several
megabytes for content the reader is going to click through to anyway.

Grouping happens here rather than in the browser: several postings can share a
dedupe_key (the same role on two boards, or a repost under a new ATS id), and
the page should show one card with the alternate links attached rather than
three near-identical rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import config


def group(jobs: list[dict]) -> list[dict]:
    """Collapse postings sharing a dedupe_key into one card."""
    buckets: dict[str, list[dict]] = {}
    for job in jobs:
        buckets.setdefault(job["dedupe_key"], []).append(job)

    cards: list[dict] = []
    for key, members in buckets.items():
        # Prefer an open posting, then the highest score, as the card's face.
        members.sort(key=lambda j: (j.get("status") == "open", j.get("score", 0)), reverse=True)
        primary = members[0]
        others = [
            {"source": m["source"], "url": m["url"], "company": m["company"]}
            for m in members[1:]
        ]
        card = {k: primary[k] for k in (
            "id", "dedupe_key", "company", "source", "url", "title", "locations",
            "location_primary", "is_remote", "is_us", "location_confidence",
            "posted_at", "snippet", "score", "explain", "matched_skills",
            "missing_skills", "min_years", "title_tier", "sponsorship",
            "status", "drop_reason",
        )}
        card["first_seen"] = min(m.get("first_seen", "") for m in members)
        card["last_seen"] = max(m.get("last_seen", "") for m in members)
        card["also_at"] = others
        cards.append(card)

    cards.sort(key=lambda c: (-c["score"], c["company"]))
    return cards


def publish(jobs: list[dict], report=None) -> tuple[int, int]:
    """Write docs/data/jobs.json and meta.json. Returns (cards, open cards)."""
    config.DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cards = group(jobs)
    open_cards = [c for c in cards if c["status"] == "open"]

    config.PUBLISH_JOBS_JSON.write_text(
        json.dumps(cards, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    repo = os.environ.get("GITHUB_REPOSITORY")
    actions_url = (
        f"https://github.com/{repo}/actions/workflows/refresh.yml"
        if repo else config.ACTIONS_URL
    )
    meta = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(cards),
        "open": len(open_cards),
        "dropped": sum(1 for c in cards if c["status"] == "dropped"),
        "closed": sum(1 for c in cards if c["status"] == "closed"),
        "actions_url": actions_url,
        "sources": sorted({c["source"] for c in cards}),
        "has_sponsorship_data": config.SPONSORS_CSV_GZ.exists(),
    }
    if report is not None:
        meta["run"] = {
            "boards_ok": report.boards_ok,
            "boards_failed": report.boards_failed,
            "postings_seen": report.postings_seen,
            "new": report.new,
            "closed": report.closed,
            "dropped_by_reason": report.dropped,
            "errors": report.errors[:20],
        }
    config.PUBLISH_META_JSON.write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return len(cards), len(open_cards)
