"""The queue between the page's Tailor button and a Claude Code session."""

from __future__ import annotations

import json

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


# --- the stale-localhost hole -------------------------------------------------
# Refresh started the workflow, the workflow committed to GitHub, and this
# checkout never fast-forwarded, so localhost:8777 served the previous run's
# jobs behind a button that reported success. These pin the two halves: the
# pull happens once per completed run, and a refusal is reported, not hidden.

def test_successful_run_pulls_once_and_only_once(monkeypatch):
    from jobradar import serve

    calls = []
    monkeypatch.setattr(serve, "_pulled_run", None)
    monkeypatch.setattr(serve, "_gh", lambda *a: (0, json.dumps(
        [{"status": "completed", "conclusion": "success",
          "createdAt": "2026-09-22T16:12:19Z", "databaseId": 4242}])))
    monkeypatch.setattr(serve, "_pull_data", lambda: calls.append(1) or {"pulled": True})

    assert serve._refresh_status()["pulled"] is True
    serve._refresh_status()          # the page polls repeatedly
    serve._refresh_status()
    assert len(calls) == 1, "polling must not re-run git on every tick"


def test_a_failed_pull_is_reported_not_swallowed(monkeypatch):
    from jobradar import serve

    monkeypatch.setattr(serve, "_pulled_run", None)
    monkeypatch.setattr(serve, "_gh", lambda *a: (0, json.dumps(
        [{"status": "completed", "conclusion": "success",
          "createdAt": "2026-09-22T16:12:19Z", "databaseId": 99}])))
    monkeypatch.setattr(serve, "_pull_data",
                        lambda: {"pulled": False, "error": "local changes would be overwritten"})

    reply = serve._refresh_status()
    assert reply["conclusion"] == "success"
    assert reply["pulled"] is False, "the page must be able to tell the data is stale"
    assert "local changes" in reply["error"]


def test_an_unfinished_run_does_not_pull(monkeypatch):
    from jobradar import serve

    monkeypatch.setattr(serve, "_pulled_run", None)
    monkeypatch.setattr(serve, "_gh", lambda *a: (0, json.dumps(
        [{"status": "in_progress", "conclusion": None,
          "createdAt": "2026-09-22T16:12:19Z", "databaseId": 7}])))
    monkeypatch.setattr(serve, "_pull_data",
                        lambda: pytest.fail("pulled mid-run"))

    assert "pulled" not in serve._refresh_status()
