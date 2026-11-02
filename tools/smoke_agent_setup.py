#!/usr/bin/env python3
"""Offline end-to-end smoke test of the agent setup (stdlib only).

Starts the real ``rustxng`` binary against a fake Mwmbl API served from
localhost and checks what an agent relies on:

* ``/healthz`` answers,
* ``/config`` reports a ``RustXNG ...`` version and the project ``GIT_URL``,
* ``/search?format=json&max_results=5`` returns the fake engine's known result,
* ``/opensearch.xml`` advertises the port actually listened on.

Cases:

* ``default-port``: no override, the built-in default port 29483 is used;
* ``cli-port``: ``--port`` overrides ``server.port`` from the settings file;
* ``env-port``: ``SEARXNG_PORT`` overrides ``server.port``.

No external network and no LLM are used. The child gets an isolated settings
file, every inherited ``SEARXNG_*``/``RUSTXNG_*`` variable is removed, and
``RUSTXNG_IDLE_TIMEOUT=30`` makes a leaked child exit on its own. Only the
processes started here are ever stopped; an occupied default port is an error.

Usage:
    python tools/smoke_agent_setup.py --binary target/debug/rustxng
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PORT = 29483
GIT_URL = "https://github.com/sahenjp/RustXNG"
HOST = "127.0.0.1"
QUERY = "rustxng smoke"
MAX_RESULTS = 5
IDLE_TIMEOUT_SECONDS = "30"
READY_TIMEOUT_SECONDS = 90.0
REQUEST_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 15.0

KNOWN_URL = "https://example.org/rustxng-smoke/known-result"
KNOWN_TITLE = "RustXNG smoke known result"
KNOWN_EXTRACT = "The fake Mwmbl server answered this query."
# More hits than max_results, so the cap is exercised.
FILLER_COUNT = 7

# A urllib opener that never goes through an HTTP proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SmokeFailure(Exception):
    """A check failed; the message says which."""


# --------------------------------------------------------------------------
# Fake Mwmbl API
# --------------------------------------------------------------------------


def fake_mwmbl_results() -> list:
    """The JSON array the fake ``/api/v1/search/`` returns (Mwmbl's shape)."""
    results = [
        {
            "url": KNOWN_URL,
            # Mwmbl splits text into highlighted fragments.
            "title": [{"value": "RustXNG smoke "}, {"value": "known result"}],
            "extract": [{"value": KNOWN_EXTRACT}],
        }
    ]
    for index in range(1, FILLER_COUNT + 1):
        results.append(
            {
                "url": f"https://example.org/rustxng-smoke/filler-{index}",
                "title": [{"value": f"RustXNG smoke filler {index}"}],
                "extract": [{"value": f"Filler result number {index}."}],
            }
        )
    return results


class FakeMwmbl:
    """A localhost HTTP server answering like ``api.mwmbl.org/api/v1``."""

    def __init__(self) -> None:
        self.queries: list = []
        self._lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (stdlib API)
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path.rstrip("/") != "/api/v1/search":
                    self.send_error(404, "not found")
                    return
                query = urllib.parse.parse_qs(parsed.query).get("s", [""])[0]
                with owner._lock:
                    owner.queries.append(query)
                body = json.dumps(fake_mwmbl_results()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer((HOST, 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def api_url(self) -> str:
        return f"http://{HOST}:{self.port}/api/v1"

    def seen_queries(self) -> list:
        with self._lock:
            return list(self.queries)

    def __enter__(self) -> "FakeMwmbl":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def resolve_binary(path: str) -> str:
    """The binary path, adding ``.exe`` on Windows when needed."""
    candidates = [path]
    if os.name == "nt" and not path.lower().endswith(".exe"):
        candidates.insert(0, path + ".exe")
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise SmokeFailure(
        f"binary not found: {path!r} (tried {', '.join(candidates)}); "
        "build it first with `cargo build --locked --bin rustxng`"
    )


def free_port(exclude: set) -> int:
    """An ephemeral localhost port that is currently free."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((HOST, 0))
            port = sock.getsockname()[1]
        if port not in exclude:
            return port
    raise SmokeFailure("could not find a free ephemeral port")


def port_accepts(port: int) -> bool:
    """Whether something accepts TCP connections on localhost:port."""
    try:
        with socket.create_connection((HOST, port), timeout=1.0):
            return True
    except OSError:
        return False


def port_bindable(port: int) -> bool:
    """Whether localhost:port can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def ensure_default_port_free() -> None:
    """Fail clearly (never kill anything) if the default port is taken."""
    if port_accepts(DEFAULT_PORT) or not port_bindable(DEFAULT_PORT):
        raise SmokeFailure(
            f"default port {DEFAULT_PORT} on {HOST} is already in use by another "
            "process; stop it yourself and re-run (this tool never kills "
            "processes it did not start)"
        )


def toml_string(value: str) -> str:
    """A TOML basic string literal."""
    return json.dumps(value)  # JSON string escaping is valid TOML here


def write_settings(path: str, api_url: str, port: int | None) -> None:
    """An isolated settings file with only the fake Mwmbl engine."""
    lines = [
        "use_default_settings = false",
        "",
        "[server]",
        f"bind_address = {toml_string(HOST)}",
        'secret_key = "rustxng-smoke-test-secret"',
        "limiter = false",
        "image_proxy = false",
    ]
    if port is not None:
        lines.append(f"port = {port}")
    lines += [
        "",
        "[search]",
        'autocomplete = ""',
        'formats = ["html", "json"]',
        'embedding_url = ""',
        'local_index_path = ""',
        "",
        "[outgoing]",
        "request_timeout = 5.0",
        "max_request_timeout = 10.0",
        "retries = 0",
        "",
        "[[engines]]",
        'name = "mwmbl"',
        'engine = "mwmbl"',
        'shortcut = "mwm"',
        f"base_url = {toml_string(api_url)}",
        "disabled = false",
        "",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def child_environment(extra: dict) -> dict:
    """The parent environment without RustXNG/SearXNG overrides or proxies."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("SEARXNG_", "RUSTXNG_"))
        and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    env["NO_PROXY"] = f"{HOST},localhost"
    env["no_proxy"] = env["NO_PROXY"]
    env["RUSTXNG_IDLE_TIMEOUT"] = IDLE_TIMEOUT_SECONDS
    env.setdefault("RUST_LOG", "info,rustxng=debug")
    env.update(extra)
    return env


def http_get(url: str) -> tuple:
    """GET a URL; return (status, content type, body text)."""
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, response.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return error.code, error.headers.get("Content-Type", ""), body


def stop_child(process: subprocess.Popen) -> None:
    """Stop the child this tool started (and nothing else), on any OS."""
    if process.poll() is not None:
        return
    try:
        process.terminate()  # SIGTERM on POSIX, TerminateProcess on Windows
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            print(f"warning: child pid {process.pid} did not exit", file=sys.stderr)
    except OSError:
        pass


def read_log(path: str, limit: int = 20000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        return f"<could not read log: {error}>"
    return text if len(text) <= limit else "...\n" + text[-limit:]


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def wait_ready(process: subprocess.Popen, port: int) -> None:
    """Bounded wait for /healthz; fail early when the child exits."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    url = f"http://{HOST}:{port}/healthz"
    last_error = "no attempt"
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise SmokeFailure(f"rustxng exited with code {code} before becoming ready")
        try:
            status, _, _ = http_get(url)
            if status == 200:
                return
            last_error = f"HTTP {status}"
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(0.25)
    raise SmokeFailure(
        f"/healthz on port {port} not ready after {READY_TIMEOUT_SECONDS:.0f}s "
        f"(last error: {last_error})"
    )


def check_config(base: str) -> None:
    status, _, body = http_get(f"{base}/config")
    if status != 200:
        raise SmokeFailure(f"/config returned HTTP {status}")
    config = json.loads(body)
    version = config.get("version", "")
    if not isinstance(version, str) or not version.startswith("RustXNG"):
        raise SmokeFailure(f"/config version {version!r} does not start with 'RustXNG'")
    git_url = (config.get("brand") or {}).get("GIT_URL")
    if git_url != GIT_URL:
        raise SmokeFailure(f"/config brand.GIT_URL is {git_url!r}, expected {GIT_URL!r}")


def check_search(base: str, fake: FakeMwmbl) -> None:
    params = urllib.parse.urlencode(
        {
            "q": QUERY,
            "format": "json",
            "max_results": str(MAX_RESULTS),
            "engines": "mwmbl",
        }
    )
    before = len(fake.seen_queries())
    status, content_type, body = http_get(f"{base}/search?{params}")
    if status != 200:
        raise SmokeFailure(f"/search returned HTTP {status}: {body[:500]}")
    if "json" not in content_type.lower():
        raise SmokeFailure(f"/search content type is {content_type!r}, expected JSON")
    payload = json.loads(body)
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise SmokeFailure(f"/search returned no results: {body[:500]}")
    if len(results) > MAX_RESULTS:
        raise SmokeFailure(
            f"/search returned {len(results)} results, max_results={MAX_RESULTS}"
        )
    known = [result for result in results if result.get("url") == KNOWN_URL]
    if not known:
        urls = [result.get("url") for result in results]
        raise SmokeFailure(f"known fake result {KNOWN_URL} missing from {urls}")
    if known[0].get("title") != KNOWN_TITLE:
        raise SmokeFailure(
            f"known result title is {known[0].get('title')!r}, expected {KNOWN_TITLE!r}"
        )
    if KNOWN_EXTRACT not in (known[0].get("content") or ""):
        raise SmokeFailure(
            f"known result content {known[0].get('content')!r} lacks the fake extract"
        )
    seen = fake.seen_queries()[before:]
    if QUERY not in seen:
        raise SmokeFailure(f"fake Mwmbl did not receive the query (got {seen!r})")


def check_opensearch(base: str, port: int) -> None:
    status, _, body = http_get(f"{base}/opensearch.xml")
    if status != 200:
        raise SmokeFailure(f"/opensearch.xml returned HTTP {status}")
    expected = f"http://{HOST}:{port}/"
    for path in ("search?q=", "autocompleter?q="):
        if f'template="{expected}{path}' not in body:
            raise SmokeFailure(
                f"/opensearch.xml lacks {expected}{path}... for port {port}:\n{body}"
            )


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------


def run_case(
    name: str,
    binary: str,
    workdir: str,
    fake: FakeMwmbl,
    expected_port: int,
    settings_port: int | None,
    args: list,
    env: dict,
    unused_port: int | None = None,
) -> None:
    case_dir = os.path.join(workdir, name)
    os.makedirs(case_dir)
    settings_path = os.path.join(case_dir, "settings.toml")
    log_path = os.path.join(case_dir, "rustxng.log")
    write_settings(settings_path, fake.api_url, settings_port)

    command = [binary, "--config", settings_path] + args
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    print(f"[{name}] starting: {' '.join(command)} (expecting port {expected_port})")

    log = open(log_path, "wb")
    process = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=child_environment(env),
            cwd=case_dir,
            creationflags=creationflags,
        )
        try:
            wait_ready(process, expected_port)
            base = f"http://{HOST}:{expected_port}"
            check_config(base)
            check_search(base, fake)
            check_opensearch(base, expected_port)
            if unused_port is not None and port_accepts(unused_port):
                raise SmokeFailure(
                    f"settings port {unused_port} is listening; the override was ignored"
                )
            if process.poll() is not None:
                raise SmokeFailure(f"rustxng exited early with code {process.returncode}")
        except (SmokeFailure, ValueError, OSError, urllib.error.URLError) as error:
            stop_child(process)
            log.close()
            print(f"----- rustxng log ({name}) -----\n{read_log(log_path)}\n-----")
            raise SmokeFailure(f"[{name}] {error}") from error
    finally:
        if process is not None:
            stop_child(process)
        if not log.closed:
            log.close()
    print(f"PASS {name} (port {expected_port})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--binary", required=True, help="path to the rustxng binary")
    parser.add_argument(
        "--keep-temp", action="store_true", help="keep the temporary directory"
    )
    options = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="rustxng-smoke-")
    failures = []
    try:
        binary = resolve_binary(options.binary)
        with FakeMwmbl() as fake:
            print(f"fake Mwmbl at {fake.api_url}; temp dir {workdir}")

            def case(name, **kwargs):
                try:
                    run_case(name, binary, workdir, fake, **kwargs)
                except SmokeFailure as error:
                    print(f"FAIL {error}")
                    failures.append(name)

            # 1. No override: the built-in default port.
            try:
                ensure_default_port_free()
            except SmokeFailure as error:
                print(f"FAIL [default-port] {error}")
                failures.append("default-port")
            else:
                case(
                    "default-port",
                    expected_port=DEFAULT_PORT,
                    settings_port=None,
                    args=[],
                    env={},
                )

            # 2. --port wins over server.port.
            settings_port = free_port({DEFAULT_PORT})
            cli_port = free_port({DEFAULT_PORT, settings_port})
            case(
                "cli-port",
                expected_port=cli_port,
                settings_port=settings_port,
                args=["--port", str(cli_port)],
                env={},
                unused_port=settings_port,
            )

            # 3. SEARXNG_PORT wins over server.port.
            settings_port = free_port({DEFAULT_PORT})
            env_port = free_port({DEFAULT_PORT, settings_port})
            case(
                "env-port",
                expected_port=env_port,
                settings_port=settings_port,
                args=[],
                env={"SEARXNG_PORT": str(env_port)},
                unused_port=settings_port,
            )
    except SmokeFailure as error:
        print(f"FAIL {error}")
        failures.append("setup")
    finally:
        if options.keep_temp:
            print(f"kept {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    if failures:
        print(f"smoke test FAILED: {', '.join(failures)}")
        return 1
    print("smoke test PASSED: default-port, cli-port, env-port")
    return 0


if __name__ == "__main__":
    sys.exit(main())
