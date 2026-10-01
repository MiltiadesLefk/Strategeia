"""Live UI check: isolated backend + frontend, headless Chrome, screenshots (plan.md QM-5).

Turns CLAUDE.md's "Verifying a frontend change actually works" recipe into one
command, so every change is checked the same way instead of with a hand-rolled
script each time. Idea from LLMQuant/quant-mind's single-harness workflow; no
code copied.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8103 --fe-port 5203 \
        --out <dir> --routes / /scan /trade-plans /portfolio /settings

What it does:
  1. Makes a throwaway work dir with its own SQLite DB and settings.json. The
     user's backend/runtime/ files are never read or written, and Docker's
     ports (8000/5173) are refused.
  2. Starts uvicorn (backend/) and vite (frontend/) on the given ports with
     ALLOW_UNAUTHENTICATED_API=true, DB_PATH, SETTINGS_PATH, CORS_ORIGINS,
     STRATEGEIA_DEV_TICKERS (default NVDA,AAPL) and VITE_API_BASE_URL set, and
     waits until both answer.
  3. Optional --seed FILE: a Python file that may define
         seed(ctx)            runs once the backend is up, before the browser
                              (e.g. ctx.api("POST", "/api/...") or sqlite3 on ctx.db_path)
         interact(page, ctx)  runs after the route sweep, on a fresh page, for
                              clicks/forms; call ctx.screenshot(page, "name")
     ctx has: be_url, fe_url, out_dir, work_dir, db_path, settings_path,
     api(method, path, **httpx_kwargs) -> httpx.Response, screenshot(page, name),
     log(msg).
  4. Visits every route in headless Chrome, screenshots each (full page) into
     --out, and records console errors, uncaught page errors, HTTP >= 400
     responses and failed requests.
  5. Always stops both servers (Windows: `taskkill /T /F` on each process tree;
     POSIX: the process group), on success, failure, Ctrl-C or SIGTERM, and
     deletes the work dir unless --keep-data.
  6. Prints a summary (screenshot paths, issues per page), writes summary.json
     into --out, and exits 1 if any issue isn't matched by an --allow regex,
     2 if setup failed (port busy, a server didn't start).

A fresh settings file means llm_provider "none", no Telegram, no Finnhub. Keep
it that way unless the feature under test needs otherwise (--settings-json).
"""

from __future__ import annotations

import argparse
import atexit
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True

# Tool output (pytest, tsc, uvicorn logs) can contain non-ASCII; a cp1252
# Windows console would raise UnicodeEncodeError printing it. Degrade instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
USER_RUNTIME_DIR = BACKEND_DIR / "runtime"

# Docker's published ports (docker-compose.yml) — the user's running app.
FORBIDDEN_PORTS = {8000, 5173}
DEFAULT_BE_PORT = 8190
DEFAULT_FE_PORT = 5290
DEFAULT_ROUTES = ["/", "/scan", "/analysis", "/trade-plans", "/portfolio", "/settings"]
DEFAULT_TICKERS = "NVDA,AAPL"

SERVER_START_TIMEOUT_SECONDS = 90
# After `load`, wait this long for the network to go quiet (react-query
# fetches, yfinance-backed endpoints on a cold cache can take a while). A
# page that never settles is noted, not failed — polling pages never idle.
DEFAULT_SETTLE_TIMEOUT_MS = 30_000
DEFAULT_EXTRA_WAIT_MS = 750
LOG_TAIL_LINES = 40

_WINDOWS = os.name == "nt"


# --- make sure we run inside the backend venv (it has playwright + httpx) ----


def _venv_python() -> Path | None:
    for candidate in (
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
        BACKEND_DIR / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return candidate
    return None


def _ensure_venv() -> None:
    try:
        import httpx  # noqa: F401
        import playwright  # noqa: F401
        return
    except ImportError:
        pass
    venv = _venv_python()
    if venv is None or Path(sys.executable).resolve() == venv.resolve():
        sys.exit(
            "ui_check: playwright/httpx not importable. Run it with backend/.venv's Python and "
            "install dev deps: backend/.venv/Scripts/python -m pip install -r backend/requirements-dev.txt"
        )
    sys.exit(subprocess.call([str(venv), *sys.argv]))


# --- process management -------------------------------------------------------


@dataclass
class Server:
    name: str
    proc: subprocess.Popen
    log_path: Path
    log_file: object


_servers: list[Server] = []


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if _WINDOWS:
        # /T takes the whole tree: vite's esbuild child, anything uvicorn forked.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if not _WINDOWS:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        proc.wait(timeout=5)


def stop_servers() -> None:
    """Idempotent; registered with atexit and called from `finally`."""
    while _servers:
        srv = _servers.pop()
        try:
            _kill_tree(srv.proc)
        except Exception as exc:  # never let teardown mask the real result
            print(f"ui_check: warning: could not stop {srv.name} (pid {srv.proc.pid}): {exc}")
        try:
            srv.log_file.close()
        except Exception:
            pass


def _on_signal(signum, _frame):
    raise KeyboardInterrupt(f"signal {signum}")


def start_server(name: str, cmd: list[str], cwd: Path, env: dict, log_path: Path) -> Server:
    log_file = open(log_path, "wb")
    kwargs: dict = {}
    if _WINDOWS:
        # Own process group: a Ctrl-C in our console reaches only us, and
        # teardown (not a half-delivered console signal) stops the children.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs
    )
    srv = Server(name, proc, log_path, log_file)
    _servers.append(srv)
    return srv


def port_in_use(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def log_tail(path: Path, n: int = LOG_TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").rstrip().splitlines()
    except OSError:
        return "(no log)"
    return "\n".join("    | " + ln for ln in lines[-n:])


def wait_http(srv: Server, url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        if srv.proc.poll() is not None:
            raise SetupError(f"{srv.name} exited early (code {srv.proc.returncode}); log tail:\n{log_tail(srv.log_path)}")
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    return
                last_err = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last_err = f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            last_err = str(exc)
        time.sleep(0.5)
    raise SetupError(f"{srv.name} not ready at {url} after {timeout:.0f}s ({last_err}); log tail:\n{log_tail(srv.log_path)}")


class SetupError(Exception):
    pass


# --- the check ------------------------------------------------------------------


@dataclass
class PageReport:
    label: str
    url: str
    screenshots: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # every issue seen
    notes: list[str] = field(default_factory=list)  # informational only


class Ctx:
    """Handed to the --seed hooks."""

    def __init__(self, be_url: str, fe_url: str, out_dir: Path, work_dir: Path, db_path: Path, settings_path: Path):
        self.be_url = be_url
        self.fe_url = fe_url
        self.out_dir = out_dir
        self.work_dir = work_dir
        self.db_path = db_path
        self.settings_path = settings_path
        self.extra_screenshots: list[str] = []

    def api(self, method: str, path: str, **kwargs):
        import httpx

        kwargs.setdefault("timeout", 120)
        return httpx.request(method, self.be_url + path, **kwargs)

    def screenshot(self, page, name: str, full_page: bool = True) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "shot"
        path = self.out_dir / f"hook-{safe}.png"
        page.screenshot(path=str(path), full_page=full_page)
        self.extra_screenshots.append(str(path))
        return str(path)

    def log(self, msg: str) -> None:
        print(f"  [hook] {msg}", flush=True)


def load_hooks(path: str | None):
    if not path:
        return None
    p = Path(path).resolve()
    if not p.is_file():
        raise SetupError(f"--seed file not found: {p}")
    spec = importlib.util.spec_from_file_location("ui_check_seed", p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not (hasattr(module, "seed") or hasattr(module, "interact")):
        raise SetupError(f"--seed file {p} defines neither seed(ctx) nor interact(page, ctx)")
    return module


def attach_listeners(page, report_ref: list, fe_origin: str) -> None:
    """report_ref[0] is the PageReport currently being filled, so one page
    object can be reused across routes without re-registering listeners."""

    def on_console(msg):
        if msg.type == "error":
            loc = msg.location or {}
            where = f" ({loc.get('url', '')}:{loc.get('lineNumber', '')})" if loc.get("url") else ""
            report_ref[0].issues.append(f"console error: {msg.text}{where}")

    def on_pageerror(exc):
        report_ref[0].issues.append(f"page error: {exc}")

    def on_response(resp):
        if resp.status >= 400:
            report_ref[0].issues.append(f"HTTP {resp.status} {resp.request.method} {resp.url}")

    def on_requestfailed(req):
        failure = req.failure or ""
        # A navigation or unmount cancelling an in-flight fetch isn't a bug.
        if "ERR_ABORTED" in failure or "NS_BINDING_ABORTED" in failure:
            report_ref[0].notes.append(f"aborted: {req.method} {req.url}")
            return
        report_ref[0].issues.append(f"request failed: {req.method} {req.url} ({failure})")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)
    page.on("requestfailed", on_requestfailed)


def slug(route: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", route).strip("-")
    return s or "root"


def run_browser(args, ctx: Ctx, hooks) -> list[PageReport]:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    width, height = (int(v) for v in args.viewport.lower().split("x"))
    reports: list[PageReport] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed)
        try:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            current: list[PageReport] = [PageReport("(startup)", "")]
            attach_listeners(page, current, ctx.fe_url)
            for i, route in enumerate(args.routes, 1):
                url = ctx.fe_url + route
                rep = PageReport(route, url)
                current[0] = rep
                reports.append(rep)
                print(f"  -> {route}", flush=True)
                try:
                    page.goto(url, wait_until="load", timeout=args.settle_timeout)
                except PWTimeout:
                    rep.issues.append(f"navigation timeout after {args.settle_timeout} ms")
                try:
                    page.wait_for_load_state("networkidle", timeout=args.settle_timeout)
                except PWTimeout:
                    rep.notes.append(f"network not idle after {args.settle_timeout} ms (screenshot taken anyway)")
                page.wait_for_timeout(args.extra_wait)
                shot = ctx.out_dir / f"{i:02d}-{slug(route)}.png"
                page.screenshot(path=str(shot), full_page=True)
                rep.screenshots.append(str(shot))
            if hooks is not None and hasattr(hooks, "interact"):
                rep = PageReport("(interact hook)", "")
                current[0] = rep
                reports.append(rep)
                print("  -> interact hook", flush=True)
                hook_page = context.new_page()
                attach_listeners(hook_page, current, ctx.fe_url)
                try:
                    hooks.interact(hook_page, ctx)
                    hook_page.wait_for_timeout(args.extra_wait)
                except Exception as exc:
                    # First line of the message, not of the traceback's tail:
                    # Playwright errors end in a multi-line call log.
                    first = (str(exc).strip().splitlines() or [""])[0]
                    rep.issues.append(f"hook error: {type(exc).__name__}: {first}")
                    print(traceback.format_exc())
                    try:
                        ctx.screenshot(hook_page, "on-hook-error")
                    except Exception:
                        pass
                rep.screenshots.extend(ctx.extra_screenshots)
            context.close()
        finally:
            browser.close()
    return reports


def build_settings(args, settings_path: Path) -> None:
    if not args.settings_json:
        return  # no file -> AppSettings defaults (llm "none", no Telegram)
    raw = args.settings_json
    if raw.startswith("@"):  # @file.json: sidesteps shell JSON quoting
        try:
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        except OSError as exc:
            raise SetupError(f"--settings-json file unreadable: {exc}")
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SetupError(f"--settings-json is not valid JSON: {exc}")
    if not isinstance(overrides, dict):
        raise SetupError("--settings-json must be a JSON object")
    # Refuse keys AppSettings doesn't know: pydantic would silently drop a
    # typo and the check would quietly run against the default instead.
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from app.config import AppSettings

        unknown = sorted(set(overrides) - set(AppSettings.model_fields))
        if unknown:
            raise SetupError(f"--settings-json has keys AppSettings doesn't define: {unknown}")
        AppSettings.model_validate(overrides)
    except SetupError:
        raise
    except ImportError:
        pass
    except Exception as exc:
        raise SetupError(f"--settings-json fails AppSettings validation: {exc}")
    settings_path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


# Git Bash (MSYS) rewrites any argument starting with "/" into a Windows path
# before Python sees it: "/scan" arrives as "C:/Program Files/Git/scan".
_MSYS_MANGLED = re.compile(r"^[A-Za-z]:[\\/](?:.*[\\/])?Git[\\/](.*)$", re.IGNORECASE)


def normalize_route(route: str) -> str:
    """'/scan' and 'scan' both mean /scan; an MSYS-mangled '/scan' is undone."""
    m = _MSYS_MANGLED.match(route)
    if m:
        return "/" + m.group(1).replace("\\", "/")
    if re.match(r"^[A-Za-z]:[\\/]", route):
        raise ValueError(
            f"route {route!r} looks like a Windows path (Git Bash rewrote a leading '/'?); "
            "write routes without the leading slash, or prefix the command with MSYS_NO_PATHCONV=1"
        )
    return route if route.startswith("/") else "/" + route


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Start an isolated Strategeia backend+frontend, screenshot routes in headless Chrome, "
        "report console errors and failed requests. Exit 0 = clean, 1 = issues, 2 = setup failed.",
    )
    p.add_argument("--be-port", type=int, default=DEFAULT_BE_PORT, help=f"backend port (default {DEFAULT_BE_PORT})")
    p.add_argument("--fe-port", type=int, default=DEFAULT_FE_PORT, help=f"frontend port (default {DEFAULT_FE_PORT})")
    p.add_argument("--out", help="screenshot + log dir (default: a new dir under the system temp dir)")
    p.add_argument("--routes", nargs="+", default=DEFAULT_ROUTES, help=f"routes to visit (default: {' '.join(DEFAULT_ROUTES)})")
    p.add_argument(
        "--settings-json",
        metavar="JSON|@FILE",
        help='AppSettings overrides for the isolated settings.json: inline JSON, e.g. \'{"paper_starting_cash": 50000}\', '
        "or @path/to/file.json (easier from PowerShell)",
    )
    p.add_argument("--seed", metavar="FILE", help="Python file defining seed(ctx) and/or interact(page, ctx)")
    p.add_argument("--tickers", default=DEFAULT_TICKERS, help=f'STRATEGEIA_DEV_TICKERS (default {DEFAULT_TICKERS}; "" = full universe)')
    p.add_argument("--allow", action="append", default=[], metavar="REGEX", help="issue text matching this regex is reported but doesn't fail the run (repeatable)")
    p.add_argument("--viewport", default="1440x900", help="WIDTHxHEIGHT (default 1440x900)")
    p.add_argument("--settle-timeout", type=int, default=DEFAULT_SETTLE_TIMEOUT_MS, help="ms to wait for load / network idle per route")
    p.add_argument("--extra-wait", type=int, default=DEFAULT_EXTRA_WAIT_MS, help="ms to wait after network idle before the screenshot")
    p.add_argument("--headed", action="store_true", help="show the browser window (debugging)")
    p.add_argument("--keep-data", action="store_true", help="keep the temp work dir (DB + settings) afterwards")
    args = p.parse_args(argv)
    try:
        args.routes = [normalize_route(r) for r in args.routes]
    except ValueError as exc:
        p.error(str(exc))
    if not re.fullmatch(r"\d+x\d+", args.viewport.lower()):
        p.error("--viewport must look like 1440x900")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    for port in (args.be_port, args.fe_port):
        if port in FORBIDDEN_PORTS:
            print(f"ui_check: port {port} is the user's Docker app - pick another (e.g. 81xx/52xx)")
            return 2
    if args.be_port == args.fe_port:
        print("ui_check: --be-port and --fe-port must differ")
        return 2
    busy = [port for port in (args.be_port, args.fe_port) if port_in_use(port)]
    if busy:
        print(f"ui_check: port(s) already in use: {busy} - a leftover server? pick other ports or stop it")
        return 2
    allow = [re.compile(a) for a in args.allow]

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out).resolve() if args.out else Path(tempfile.gettempdir()) / "strategeia-ui-check" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="strategeia-ui-data-"))
    db_path = work_dir / "verify.db"
    settings_path = work_dir / "settings.json"
    # Belt and braces: the whole point is never touching the user's data.
    for f in (db_path, settings_path):
        if USER_RUNTIME_DIR.resolve() in f.resolve().parents:
            print(f"ui_check: refusing to use {f}: inside backend/runtime/")
            return 2

    be_url = f"http://localhost:{args.be_port}"
    fe_url = f"http://localhost:{args.fe_port}"
    ctx = Ctx(be_url, fe_url, out_dir, work_dir, db_path, settings_path)

    signal.signal(signal.SIGTERM, _on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _on_signal)
    atexit.register(stop_servers)

    reports: list[PageReport] = []
    setup_error = ""
    started = time.monotonic()
    try:
        hooks = load_hooks(args.seed)
        build_settings(args, settings_path)

        base_env = dict(os.environ)
        # Inherited values must not leak in (a shell with the real paths or
        # a real API secret exported); every isolation var is set explicitly.
        for k in ("DB_PATH", "SETTINGS_PATH", "API_SHARED_SECRET", "VITE_API_SHARED_SECRET", "STRATEGEIA_DEV_TICKERS"):
            base_env.pop(k, None)
        be_env = {
            **base_env,
            "ALLOW_UNAUTHENTICATED_API": "true",
            "DB_PATH": str(db_path),
            "SETTINGS_PATH": str(settings_path),
            "CORS_ORIGINS": f"http://localhost:{args.fe_port},http://127.0.0.1:{args.fe_port}",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if args.tickers:
            be_env["STRATEGEIA_DEV_TICKERS"] = args.tickers
        fe_env = {**base_env, "VITE_API_BASE_URL": be_url, "BROWSER": "none"}

        venv = _venv_python() or Path(sys.executable)
        node = shutil.which("node")
        if node is None:
            raise SetupError("node not found on PATH")
        vite = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite.exists():
            raise SetupError("frontend/node_modules missing - run `npm install` in frontend/")

        print(f"ui_check: backend :{args.be_port}, frontend :{args.fe_port}, data {work_dir}", flush=True)
        be = start_server(
            "backend",
            [str(venv), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.be_port)],
            BACKEND_DIR,
            be_env,
            out_dir / "backend.log",
        )
        fe = start_server(
            "frontend",
            [node, str(vite), "--port", str(args.fe_port), "--strictPort"],
            FRONTEND_DIR,
            fe_env,
            out_dir / "frontend.log",
        )
        wait_http(be, f"http://127.0.0.1:{args.be_port}/api/health", SERVER_START_TIMEOUT_SECONDS)
        wait_http(fe, fe_url + "/", SERVER_START_TIMEOUT_SECONDS)
        print(f"ui_check: both servers up ({time.monotonic() - started:.1f}s)", flush=True)

        if hooks is not None and hasattr(hooks, "seed"):
            print("  -> seed hook", flush=True)
            try:
                hooks.seed(ctx)
            except Exception:
                print(traceback.format_exc())
                raise SetupError("seed(ctx) raised (traceback above)")

        reports = run_browser(args, ctx, hooks)
    except SetupError as exc:
        setup_error = str(exc)
    except KeyboardInterrupt:
        setup_error = "interrupted"
    finally:
        stop_servers()
        still_up = [port for port in (args.be_port, args.fe_port) if port_in_use(port)]
        if not args.keep_data:
            shutil.rmtree(work_dir, ignore_errors=True)

    # --- summary ---
    print()
    failing = 0
    allowed_count = 0
    summary_pages = []
    for rep in reports:
        blocking = [i for i in rep.issues if not any(a.search(i) for a in allow)]
        allowed = [i for i in rep.issues if i not in blocking]
        failing += len(blocking)
        allowed_count += len(allowed)
        status = "FAIL" if blocking else "ok"
        print(f"[{status}] {rep.label}")
        for s in rep.screenshots:
            print(f"       screenshot: {s}")
        for i in blocking:
            print(f"       ISSUE   {i}")
        for i in allowed:
            print(f"       allowed {i}")
        for n in rep.notes:
            print(f"       note    {n}")
        summary_pages.append(
            {"route": rep.label, "url": rep.url, "screenshots": rep.screenshots, "issues": blocking, "allowed": allowed, "notes": rep.notes}
        )
    if still_up:
        print(f"WARNING: port(s) {still_up} still answering after teardown - check for a leftover process")
    if args.keep_data:
        print(f"Kept isolated data: {work_dir}")
    print(f"Logs: {out_dir / 'backend.log'} , {out_dir / 'frontend.log'}")
    print(f"Output: {out_dir}")
    result = "SETUP FAILED" if setup_error else ("FAILED" if failing else "PASSED")
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "result": result,
                "setup_error": setup_error,
                "be_port": args.be_port,
                "fe_port": args.fe_port,
                "pages": summary_pages,
                "ports_still_up": still_up,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if setup_error == "interrupted":
        print("UI CHECK INTERRUPTED (servers stopped)")
        return 130
    if setup_error:
        print(f"UI CHECK SETUP FAILED: {setup_error}")
        return 2
    print(
        f"UI CHECK {result}: {len(args.routes)} route(s), {failing} issue(s)"
        + (f", {allowed_count} allowed" if allowed_count else "")
        + f", {time.monotonic() - started:.1f}s"
    )
    return 1 if failing else 0


if __name__ == "__main__":
    _ensure_venv()
    sys.exit(main())
