"""Guards on the two inputs that stop being trusted once HTTP can supply them.

Both of these were latent rather than exploitable: every caller was a path or a
URL typed at a command line. The local helper makes both reachable from a
request, so each guard names the hole it closes.
"""

from __future__ import annotations

import pytest

from jobradar.resume.ingest import check_public_url
from jobradar.resume.paths import HOME, ResumeError, resolve


@pytest.mark.parametrize("part", [
    "../../../tmp/evil.json",
    "..",
    "queue/../../etc",
    "../.ssh/id_rsa",
])
def test_resolve_refuses_traversal(part):
    """Before this guard, resolve("queue", "../../../tmp/evil.json") returned
    /Users/tmp/evil.json. The only check was "is this inside the repo", so
    anything escaping HOME entirely went through."""
    with pytest.raises(ResumeError) as exc:
        resolve("queue", part)
    assert ".." in str(exc.value)


def test_resolve_still_accepts_a_normal_slug():
    path = resolve("queue", "IntelDataAnalyst.json")
    assert HOME in path.parents


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8777/",          # the helper itself
    "http://localhost/admin",
    "http://192.168.1.1/",             # home router
    "http://10.0.0.5/admin",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
])
def test_fetch_refuses_non_public_addresses(url):
    """Without this the helper is an open relay into the machine's own network."""
    with pytest.raises(ResumeError) as exc:
        check_public_url(url)
    assert "not a public address" in str(exc.value)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/x",
])
def test_fetch_refuses_non_http_schemes(url):
    with pytest.raises(ResumeError) as exc:
        check_public_url(url)
    assert "only http and https" in str(exc.value)


@pytest.mark.parametrize("url", [
    "https://jobs.lever.co/fresha/902fe757-cfb9-4d21-b8c4-b044ab44bffd",
    "https://job-boards.greenhouse.io/databricks/jobs/123",
])
def test_real_postings_are_still_allowed(url):
    assert check_public_url(url) == url
