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
import re
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
QUEUE = "queue"

# jd.slug_for only ever emits [A-Za-z0-9]+, so this is an allowlist rather than a
# blocklist. It runs before a slug reaches resolve(), because a slug is the one
# value in this system that arrives from a request rather than from a person.
_SLUG_OK = re.compile(r"^[A-Za-z0-9]{1,64}$")


def check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG_OK.match(slug):
        raise ResumeError(
            f"refusing slug {slug!r}: must be 1 to 64 characters of letters and "
            f"digits only, which is what jd.slug_for produces."
        )
    return slug


def resolve(*parts: str, create_parent: bool = False) -> Path:
    """A path under HOME, guaranteed to sit outside the repository and under HOME.

    The `..` rejection is not theoretical. Before it existed,
    `resolve("queue", "../../../tmp/evil.json")` returned `/Users/tmp/evil.json`: the
    only check was "is this inside the repo", so anything that escaped HOME entirely
    sailed through. That was harmless while every caller was a path you typed, and stops
    being harmless the moment a slug arrives over HTTP.
    """
    for part in parts:
        if ".." in Path(part).parts:
            raise ResumeError(
                f"refusing path component {part!r}: '..' can escape "
                f"{HOME} entirely, and this function is reachable from HTTP."
            )
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


def output_dir() -> Path:
    """Where the finished PDF goes.

    NOT under ~/.jobradar. That directory starts with a dot, so Finder hides it,
    and the one file in this whole system that has to be easy to grab is the PDF
    you upload to an application portal. Hiding the deliverable to keep the
    working files tidy is the wrong trade.

    Defaults to the folder Alli already files resumes in, named for the current
    month the way the existing ones are (july2026, august2026, ...), so a
    tailored resume lands beside the ones made by hand.
    """
    import os
    from datetime import date
    override = os.environ.get("JOBRADAR_RESUME_OUT")
    if override:
        return Path(override).expanduser()
    month = date.today().strftime("%B%Y").lower()
    return Path.home() / "Desktop" / "jobs" / month


def slug_dir(kind: str, slug: str) -> Path:
    return resolve(kind, slug, create_parent=True)
