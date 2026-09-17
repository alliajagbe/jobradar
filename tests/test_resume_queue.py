"""The queue between the page's Tailor button and a Claude Code session."""

from __future__ import annotations

import pytest

from jobradar.resume import queue as Q
from jobradar.resume.paths import ResumeError, check_slug


@pytest.fixture
def entry(tmp_path, monkeypatch):
    import jobradar.resume.paths as paths
    monkeypatch.setattr(paths, "HOME", tmp_path)
    e = Q.Entry(slug="TestCoAnalyst", url="https://example.com/job/1",
                company="TestCo", title="Analyst")
    Q.write(e)
    return e


def test_lifecycle(entry):
    assert Q.read("TestCoAnalyst").status == Q.QUEUED
    Q.update("TestCoAnalyst", status=Q.BRIEFED, brief="/tmp/b.md")
    assert Q.read("TestCoAnalyst").status == Q.BRIEFED
    Q.take("TestCoAnalyst")
    assert Q.read("TestCoAnalyst").status == Q.WRITING
    Q.update("TestCoAnalyst", status=Q.READY, pdf="/tmp/r.pdf")
    done = Q.read("TestCoAnalyst")
    assert done.status == Q.READY and done.pdf == "/tmp/r.pdf"


def test_pending_lists_only_what_a_session_should_pick_up(entry):
    assert [e.slug for e in Q.pending()] == ["TestCoAnalyst"]
    Q.update("TestCoAnalyst", status=Q.READY)
    assert Q.pending() == []


def test_unknown_field_is_refused(entry):
    with pytest.raises(ResumeError):
        Q.update("TestCoAnalyst", nonsense=1)


def test_a_malformed_entry_reports_failed_rather_than_raising(entry, tmp_path):
    """A watcher must survive a corrupt file, not stop on it."""
    Q.path_for("TestCoAnalyst").write_text("{not json", encoding="utf-8")
    got = Q.read("TestCoAnalyst")
    assert got.status == Q.FAILED and "unreadable" in got.error


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "..", "has space", "", "a" * 65,
    "semi;colon", "dot.dot", "slash/slash",
])
def test_slugs_from_a_request_are_refused(bad):
    """A slug is the one value here that arrives from HTTP rather than a person,
    and it lands in a filesystem path."""
    with pytest.raises(ResumeError):
        check_slug(bad)


@pytest.mark.parametrize("good", ["IntelDataAnalyst", "A", "Fresha123", "a" * 64])
def test_real_slugs_pass(good):
    assert check_slug(good) == good


def test_notification_text_is_passed_as_an_argument_not_a_script():
    """Job titles come from third-party postings. Concatenating one into an
    AppleScript string means a posting titled `x" & (do shell script "...") & "`
    runs that shell script."""
    import pathlib

    from jobradar.resume.cli import notify
    marker = pathlib.Path("/tmp/jr_queue_injection_probe")
    marker.unlink(missing_ok=True)
    notify("JobRadar", 'Analyst" & (do shell script "touch '
                       '/tmp/jr_queue_injection_probe") & "')
    assert not marker.exists()
