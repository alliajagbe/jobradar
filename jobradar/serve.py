"""The local helper: the only thing on this machine a browser can talk to.

Why it exists: the page is static HTML on GitHub Pages. It cannot reach this
disk and it cannot reach a Claude Code session. So a button that triggers real
work needs something listening locally, and this is the smallest thing that
does it. Standard library only; nothing in this project uses a web framework and
none is needed.

SECURITY. This is a port on a laptop that a public website is allowed to talk
to, which makes it the most exposed surface in the project. Three checks run on
EVERY request, not just the preflight, and each one closes a specific hole:

  Origin        an allowlist. CORS controls who may READ a response, not who
                may SEND a request, so without this any site you visit can
                queue jobs on your machine.
  Host          must be loopback. Without this, a malicious domain can point
                its own DNS at 127.0.0.1 and the browser will treat it as
                same-origin. SimpleHTTPRequestHandler checks nothing.
  Content-Type  must be application/json on POST. This is what forces a
                preflight: a POST with text/plain is a CORS "simple request",
                delivered and executed with only the reply withheld, so the
                Origin check would never get to run.

It binds to 127.0.0.1, which the kernel will not route external packets to, so
it is not reachable from the network at all. It shuts itself down after
IDLE_TIMEOUT so the port is open while you work rather than indefinitely.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config
from .resume import queue as Q
from .resume.paths import ResumeError, check_slug

PORT = 8777
HOST = "127.0.0.1"

# Who may talk to us. The GitHub Pages origin is the point of the whole feature;
# the localhost entries are for when the helper serves the page itself.
ALLOWED_ORIGINS = frozenset({
    "https://alliajagbe.github.io",
    f"http://localhost:{PORT}",
    f"http://127.0.0.1:{PORT}",
})
ALLOWED_HOSTS = frozenset({f"localhost:{PORT}", f"127.0.0.1:{PORT}"})

# Seconds without a request before the helper exits, or 0 to stay up.
#
# Zero by default now. The timeout was defence in depth on top of the Origin,
# Host and Content-Type checks, which are the control that actually stops a
# malicious page. It narrowed the exposure window and cost a dead page at the
# exact moment Alli sat down to work, twice. Under launchd KeepAlive it would
# also be theatre: the process would restart seconds after exiting.
IDLE_TIMEOUT = int(os.environ.get("JOBRADAR_IDLE_TIMEOUT", "0"))

_last_request = time.monotonic()
_lock = threading.Lock()


def _touch() -> None:
    global _last_request
    with _lock:
        _last_request = time.monotonic()


class Handler(SimpleHTTPRequestHandler):
    verbose = False

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(config.DOCS_DIR), **kw)

    # ---- logging ---- #

    def log_message(self, fmt, *args):
        if self.verbose:
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}",
                  flush=True)

    # ---- security ---- #

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        # A same-origin GET for a static file sends no Origin. That is fine:
        # only the API routes require one.
        return origin is None or origin in ALLOWED_ORIGINS

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS

    def _guard(self, *, require_origin: bool) -> bool:
        """Returns True if the request may proceed; writes the refusal if not."""
        if not self._host_ok():
            self._json(403, {"error": "bad Host; this helper only answers on loopback"})
            return False
        if not self._origin_ok():
            self._json(403, {"error": "origin not allowed"})
            return False
        if require_origin and self.headers.get("Origin") is None:
            self._json(403, {"error": "missing Origin"})
            return False
        return True

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # Chrome's Private Network Access preflight for a public page reaching a
        # private address. Without this the browser refuses before we are asked.
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ---- routes ---- #

    def do_OPTIONS(self):                                    # noqa: N802
        _touch()
        if not self._host_ok():
            self._json(403, {"error": "bad Host"})
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):                                        # noqa: N802
        _touch()
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return super().do_GET()          # the static site, from docs/
        if not self._guard(require_origin=False):
            return None
        if path == "/api/health":
            return self._json(200, {"ok": True, "port": PORT})
        if path == "/api/refresh":
            return self._json(200, _refresh_status())
        if path == "/api/queue":
            return self._json(200, {"entries": [e.as_dict() for e in Q.all_entries()]})
        if path.startswith("/api/queue/"):
            try:
                slug = check_slug(path.rsplit("/", 1)[-1])
            except ResumeError as exc:
                return self._json(400, {"error": str(exc)})
            entry = Q.read(slug)
            if entry is None:
                return self._json(404, {"error": "no such entry"})
            return self._json(200, entry.as_dict())
        return self._json(404, {"error": "no such route"})

    def do_POST(self):                                       # noqa: N802
        _touch()
        path = urlparse(self.path).path
        if not self._guard(require_origin=True):
            return None
        # Requiring JSON is what forces a preflight, which is what lets the
        # Origin check above run at all. See the module docstring.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            return self._json(415, {"error": "Content-Type must be application/json"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": f"bad JSON: {exc}"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "body must be an object"})

        if path == "/api/tailor":
            return self._tailor(body)
        if path == "/api/refresh":
            return self._json(*_start_refresh())
        if path.startswith("/api/queue/"):
            try:
                slug = check_slug(path.rsplit("/", 1)[-1])
            except ResumeError as exc:
                return self._json(400, {"error": str(exc)})
            try:
                entry = Q.update(slug, **{k: v for k, v in body.items()
                                          if k in Q.Entry.__dataclass_fields__})
            except ResumeError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, entry.as_dict())
        return self._json(404, {"error": "no such route"})

    def _tailor(self, body: dict):
        from .resume import ingest, jd

        url = (body.get("url") or "").strip()
        if not url:
            return self._json(400, {"error": "url is required"})
        try:
            ingest.check_public_url(url)
        except ResumeError as exc:
            return self._json(400, {"error": str(exc)})

        # A slug supplied by the caller is never trusted; derive it instead.
        stub = {"company": body.get("company") or "Link",
                "title": body.get("title") or "Role"}
        slug = jd.slug_for(stub) or "PastedLink"
        try:
            slug = check_slug(slug)
        except ResumeError:
            slug = "PastedLink"

        entry = Q.Entry(slug=slug, url=url, company=stub["company"],
                        title=stub["title"], dedupe_key=body.get("dedupe_key") or "")
        Q.write(entry)
        threading.Thread(target=_fetch_brief, args=(slug, url), daemon=True).start()
        return self._json(202, entry.as_dict())


# The refresh workflow lives on GitHub, and triggering it needs authentication.
# The helper runs on your machine where `gh` is already logged in, so it can do
# it without a token ever reaching the browser. The workflow name is a constant,
# so nothing from a request is interpolated into the command.
WORKFLOW = "refresh.yml"


def _gh(*args: str) -> tuple[int, str]:
    if not shutil.which("gh"):
        return 1, "the gh CLI is not installed, so the helper cannot start a workflow"
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or done.stderr).strip()


def _start_refresh() -> tuple[int, dict]:
    code, out = _gh("workflow", "run", WORKFLOW)
    if code != 0:
        return 502, {"error": out[:300]}
    return 202, {"ok": True, "message": "refresh started"}


# The workflow commits the new jobs to GitHub, not to this checkout. When the
# page is served from disk at localhost:8777 rather than from Pages, a refresh
# that succeeded upstream left the local data untouched and the page showed the
# previous run's jobs forever. This is exactly the failure Alli hit: Refresh
# worked, the workflow worked, Pages worked, and localhost served data three
# days old because nothing pulled it back down.
_pulled_run: int | None = None


def _pull_data() -> dict:
    """Fast-forward this checkout so the local page serves the new jobs.

    Fast-forward only, and never stashing: uncommitted work belongs to Alli and
    a helper moving it around behind her back is worse than a stale page. On
    refusal the reason is reported rather than swallowed.
    """
    git = shutil.which("git")
    if not git:
        return {"pulled": False, "error": "git is not installed"}
    try:
        done = subprocess.run([git, "-C", str(config.ROOT), "pull", "--ff-only", "origin", "main"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"pulled": False, "error": str(exc)[:200]}
    if done.returncode != 0:
        return {"pulled": False, "error": (done.stderr or done.stdout).strip()[:200]}
    return {"pulled": True}


def _refresh_status() -> dict:
    global _pulled_run
    code, out = _gh("run", "list", f"--workflow={WORKFLOW}", "--limit", "1",
                    "--json", "status,conclusion,createdAt,databaseId")
    if code != 0:
        return {"ok": False, "error": out[:200]}
    try:
        rows = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "could not read gh output"}
    if not rows:
        return {"ok": True, "status": "none"}
    row = rows[0]
    reply = {"ok": True, "status": row.get("status"),
             "conclusion": row.get("conclusion"), "id": row.get("databaseId"),
             "created_at": row.get("createdAt")}
    # Pull once per completed run, so polling does not re-run git every 2s.
    run_id = row.get("databaseId")
    if (row.get("status") == "completed" and row.get("conclusion") == "success"
            and run_id is not None and run_id != _pulled_run):
        _pulled_run = run_id
        reply.update(_pull_data())
    return reply


def _fetch_brief(slug: str, url: str) -> None:
    """Fetch the posting and write its brief, off the request thread.

    Mechanical work only. The writing stays with a person, because that is the
    part with no API key behind it.
    """
    from .resume.cli import build_brief

    try:
        out, _job, _slug = build_brief(url, slug=slug)
        Q.update(slug, status=Q.BRIEFED, brief=str(out))
    except Exception as exc:                                 # noqa: BLE001
        Q.update(slug, status=Q.FAILED, error=str(exc)[:400])


def _idle_watch(server: ThreadingHTTPServer) -> None:
    if not IDLE_TIMEOUT:
        return
    while True:
        time.sleep(30)
        with _lock:
            idle = time.monotonic() - _last_request
        if idle > IDLE_TIMEOUT:
            print(f"\nidle {IDLE_TIMEOUT // 60} minutes, shutting down", flush=True)
            server.shutdown()
            return


def run(*, port: int = PORT, verbose: bool = False) -> int:
    Handler.verbose = verbose
    server = ThreadingHTTPServer((HOST, port), Handler)
    threading.Thread(target=_idle_watch, args=(server,), daemon=True).start()
    print(f"jobradar helper on http://{HOST}:{port}")
    print(f"  serving  {config.DOCS_DIR}")
    print(f"  accepts  {', '.join(sorted(ALLOWED_ORIGINS))}")
    if IDLE_TIMEOUT:
        print(f"  exits after {IDLE_TIMEOUT // 60} idle minutes. Ctrl-C to stop.")
    else:
        print("  stays up. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
