"""The queue between a button on the page and a Claude Code session.

The page cannot reach this machine's disk and cannot reach a Claude session. So
the local helper writes a small JSON file per request, and a session watching the
directory picks it up. The file is the entire protocol, which is deliberate: it
survives the helper restarting, the session ending, and the laptop sleeping, and
it can be read and fixed with a text editor.

Status moves queued -> briefed -> writing -> ready, or to failed with a reason.
Nothing here enforces that order; the writer is trusted because the writer is
either the helper or a session, and a stuck entry is better than a rejected one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .paths import QUEUE, ResumeError, check_slug, resolve

QUEUED, BRIEFED, WRITING, READY, FAILED = "queued", "briefed", "writing", "ready", "failed"
ACTIVE = (QUEUED, BRIEFED)

# An entry that has been `writing` longer than this is reported as stalled rather
# than hidden: the usual cause is a session that died mid-job, and silently
# leaving it in place means a card that spins forever.
STALL_MINUTES = 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Entry:
    slug: str
    url: str
    company: str = ""
    title: str = ""
    dedupe_key: str = ""
    status: str = QUEUED
    queued_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    brief: str | None = None
    pdf: str | None = None
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def age_minutes(self) -> float:
        try:
            then = datetime.strptime(self.updated_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return 0.0
        return (datetime.now(timezone.utc).replace(tzinfo=None) - then).total_seconds() / 60

    @property
    def stalled(self) -> bool:
        return self.status == WRITING and self.age_minutes > STALL_MINUTES


def path_for(slug: str) -> Path:
    return resolve(QUEUE, f"{check_slug(slug)}.json", create_parent=True)


def write(entry: Entry) -> Path:
    entry.updated_at = _now()
    path = path_for(entry.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry.as_dict(), indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def read(slug: str) -> Entry | None:
    path = path_for(slug)
    if not path.exists():
        return None
    try:
        return Entry(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        # A malformed entry must not stop the watcher. Report it as failed so the
        # card says something instead of spinning.
        return Entry(slug=slug, url="", status=FAILED, error=f"unreadable entry: {exc}")


def update(slug: str, **fields) -> Entry:
    entry = read(slug)
    if entry is None:
        raise ResumeError(f"no queue entry for {slug!r}")
    for key, value in fields.items():
        if not hasattr(entry, key):
            raise ResumeError(f"queue entries have no field {key!r}")
        setattr(entry, key, value)
    write(entry)
    return entry


def all_entries() -> list[Entry]:
    directory = resolve(QUEUE, create_parent=True)
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        entry = read(path.stem)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda e: e.queued_at, reverse=True)
    return out


def pending() -> list[Entry]:
    """Entries a session should pick up: queued or briefed, plus anything stalled."""
    return [e for e in all_entries() if e.status in ACTIVE or e.stalled]


def take(slug: str, note: str = "") -> Entry:
    """Claim an entry before starting work, so a re-run or a second session does
    not duplicate it. The race is made visible rather than prevented; last
    writer wins, which is fine for one person and one laptop."""
    return update(slug, status=WRITING, note=note or None)
