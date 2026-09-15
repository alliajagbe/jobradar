"""Where resume data lives, and the rule that keeps it out of the repository.

This module exists because of a specific, unrecoverable mistake. The jobradar
repository is PUBLIC, four GitHub Actions workflows run `git add` and commit on
a schedule, and a resume carries a full name, a phone number and an email
address. A .gitignore entry is one `git add -f` or one careless `git add -A`
away from publishing all three permanently to a public git history.

So the defence is not a .gitignore entry. Resume content does not live in the
repository at all: it lives under ~/.jobradar, and `resolve` raises if any path
it is asked for would land inside the repository tree. Writing personal data
into the repo is not discouraged here, it is impossible.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import config


class ResumeError(Exception):
    """Every failure this subpackage can produce."""


# Overridable for tests only. Changing it to point inside the repo still fails
# the containment check below, which is the point.
HOME = Path(os.environ.get("JOBRADAR_RESUME_HOME", Path.home() / ".jobradar"))

MASTER = "resume/master.yaml"
LEXICON = "resume/lexicon.yaml"
JD_CACHE = "jd-cache"
BRIEFS = "briefs"
VARIANTS = "variants"
OUT = "out"


def resolve(*parts: str, create_parent: bool = False) -> Path:
    """A path under HOME, guaranteed to sit outside the repository."""
    path = HOME.joinpath(*parts).expanduser()
    try:
        repo = config.ROOT.resolve()
    except OSError as exc:                                    # pragma: no cover
        raise ResumeError(f"cannot resolve repository root: {exc}") from exc

    resolved = path.resolve() if path.exists() else Path(os.path.abspath(path))
    if resolved == repo or repo in resolved.parents:
        raise ResumeError(
            f"refusing to use {resolved}: it is inside the public repository at "
            f"{repo}. Resume content carries a name, phone number and email, and "
            f"this repo is committed to automatically by four workflows. Set "
            f"JOBRADAR_RESUME_HOME to a directory outside the repo."
        )
    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def master_path() -> Path:
    return resolve(MASTER)


def lexicon_path() -> Path:
    return resolve(LEXICON)


def slug_dir(kind: str, slug: str) -> Path:
    return resolve(kind, slug, create_parent=True)
