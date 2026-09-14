"""The only HTTP client in the process, plus the politeness that keeps it alive.

Everything here exists because of a specific failure mode:

- A per-host minimum interval, because 300 boards fetched flat out earns a 429.
- Full jitter on backoff, because 300 boards retrying in lockstep is simply a
  second thundering herd arriving a few seconds later.
- A per-host circuit breaker, because once a host starts refusing you, every
  remaining board on it costs four attempts and sixty seconds of sleep to learn
  the same thing, and the run's log fills with 200 copies of one message.
- Wrapping every exception into SourceError, because the refresh loop must be
  able to lose one board without losing the run.
"""

from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlsplit

import httpx

from .. import config
from .base import SourceError

_client: httpx.Client | None = None
_client_lock = threading.Lock()

_last_request: dict[str, float] = {}
_pace_lock = threading.Lock()

_failures: dict[str, int] = {}
_failure_lock = threading.Lock()


def client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=config.HTTP_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            )
        return _client


def close() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def _host_key(url: str) -> str:
    host = urlsplit(url).hostname or ""
    for known in config.HOST_MIN_INTERVAL:
        if known != "default" and host.endswith(known):
            return known
    return host


def _pace(host: str) -> None:
    interval = config.HOST_MIN_INTERVAL.get(host, config.HOST_MIN_INTERVAL["default"])
    with _pace_lock:
        previous = _last_request.get(host, 0.0)
        wait = previous + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


class CircuitOpen(SourceError):
    """This host has failed enough times that we stop asking for this run."""


def reset_circuits() -> None:
    with _failure_lock:
        _failures.clear()


def _check_circuit(host: str) -> None:
    with _failure_lock:
        if _failures.get(host, 0) >= config.CIRCUIT_BREAKER_FAILURES:
            raise CircuitOpen(f"{host}: circuit open after {_failures[host]} failures")


def _record(host: str, *, ok: bool) -> None:
    with _failure_lock:
        _failures[host] = 0 if ok else _failures.get(host, 0) + 1


def request(method: str, url: str, **kwargs) -> httpx.Response:
    """One HTTP call with pacing, retries and the circuit breaker.

    Raises SourceError and nothing else. A 404 is returned rather than raised,
    because for a board probe "this slug does not exist" is an answer.
    """
    host = _host_key(url)
    _check_circuit(host)
    last: Exception | None = None

    for attempt in range(config.HTTP_ATTEMPTS):
        _pace(host)
        try:
            response = client().request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last = exc
            _record(host, ok=False)
        else:
            if response.status_code < 400 or response.status_code == 404:
                _record(host, ok=True)
                return response
            if response.status_code not in (429, 500, 502, 503, 504):
                _record(host, ok=True)  # A 403 is an answer, not a flaky host.
                raise SourceError(f"{method} {url} -> {response.status_code}")
            last = SourceError(f"{method} {url} -> {response.status_code}")
            _record(host, ok=False)
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = 0.0
                if delay > config.RETRY_AFTER_CAP:
                    raise SourceError(f"{url}: Retry-After {delay}s exceeds cap")
                if delay > 0:
                    time.sleep(delay)
                    continue

        if attempt < config.HTTP_ATTEMPTS - 1:
            # Full jitter. Fixed backoff would have every board on this host
            # retry at the same instant.
            time.sleep(random.uniform(0, min(60.0, 2.0 ** (attempt + 1))))

    _check_circuit(host)
    raise SourceError(f"{method} {url} failed after {config.HTTP_ATTEMPTS} attempts: {last}")


def get_json(url: str, **kwargs) -> object:
    response = request("GET", url, **kwargs)
    if response.status_code == 404:
        raise SourceError(f"GET {url} -> 404")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"GET {url}: response was not JSON: {exc}") from exc


def post_json(url: str, payload: dict, **kwargs) -> object:
    response = request("POST", url, json=payload, **kwargs)
    if response.status_code == 404:
        raise SourceError(f"POST {url} -> 404")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"POST {url}: response was not JSON: {exc}") from exc
