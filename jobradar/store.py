"""Job records on disk, as JSONL in git.

There is no database. The runner that produces this data is ephemeral, so state
has to be committed, and a SQLite binary recommitted on every run would bloat
the repo while giving no readable history. One JSON object per line, sorted by
id, diffs cleanly: `git log -p data/jobs.jsonl` shows exactly which postings
appeared and disappeared on any given day, for free.

The merge rules are the whole point of this module:

- `first_seen` is preserved across runs. A company recycling a requisition every
  thirty days must not keep resurfacing as new.
- A posting is rescored only when its description hash changed, so an unchanged
  board costs nothing beyond the fetch.
- Records are never deleted, only closed. A posting you applied to has to stay
  readable after the company takes it down.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterable

from . import config


def load(path: Path | None = None) -> dict[str, dict]:
    """Read jobs.jsonl into a dict keyed by id. Missing file is an empty run."""
    path = path or config.JOBS_JSONL
    if not path.exists():
        return {}
    jobs: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            jobs[record["id"]] = record
    return jobs


def save(jobs: Iterable[dict], path: Path | None = None) -> int:
    """Write jobs.jsonl sorted by id.

    Sorting is not cosmetic. An unsorted file reorders itself on every run and
    every commit looks like the whole dataset changed, which destroys the
    reason for using JSONL in the first place.
    """
    path = path or config.JOBS_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(jobs, key=lambda r: r["id"])
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)
    return len(records)


def merge(existing: dict, fresh: dict, *, today: str | None = None) -> dict:
    """Fold a freshly fetched record into the one already on file."""
    today = today or date.today().isoformat()
    merged = dict(fresh)
    merged["first_seen"] = existing.get("first_seen", today)
    merged["last_seen"] = today
    # A job that reappears after being closed is open again.
    merged["closed_at"] = None
    return merged


def new_record(fresh: dict, *, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    record = dict(fresh)
    record["first_seen"] = today
    record["last_seen"] = today
    record["closed_at"] = None
    return record
