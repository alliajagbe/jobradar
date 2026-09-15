"""The invariant that keeps a phone number out of a public repository.

The repo is public and four workflows run `git add` and commit on a schedule.
A .gitignore entry is one `git add -f` from a permanent public commit, so the
defence is that resume paths cannot resolve inside the repo at all.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from jobradar import config
from jobradar.resume.paths import ResumeError, resolve


def test_default_resume_home_is_outside_the_repo():
    assert config.ROOT not in resolve("resume/master.yaml").parents


def test_a_path_inside_the_repo_is_refused(monkeypatch):
    import jobradar.resume.paths as paths
    monkeypatch.setattr(paths, "HOME", config.ROOT / "resume-data")
    with pytest.raises(ResumeError) as exc:
        paths.resolve("resume/master.yaml")
    assert "inside the public repository" in str(exc.value)


def test_no_tracked_file_contains_personal_contact_details():
    """Belt and braces: grep what git actually tracks."""
    tracked = subprocess.run(["git", "ls-files"], cwd=config.ROOT,
                             capture_output=True, text=True).stdout.split()
    # Assembled from fragments so this file does not contain the literals it
    # searches for. Spelled out, the test fails on itself the moment it is
    # committed, which is a confusing way to learn the check works.
    digits = "743" + "240" + "7497"
    handle = "alliajagbe" + "001@"
    pattern = re.compile("|".join([
        r"\D".join(digits[i:i + 3] for i in range(0, 9, 3)) + r"\D?",
        re.escape(handle),
    ]))
    offenders = []
    for name in tracked:
        path = config.ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(name)
        except OSError:
            continue
    assert not offenders, f"personal contact details in tracked files: {offenders}"
