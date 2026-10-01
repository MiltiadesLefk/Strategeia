"""One command that checks a change the same way every time (plan.md QM-5).

Idea from LLMQuant/quant-mind's `scripts/verify.sh` (one deterministic harness
that every contributor, human or agent, runs before calling a change done);
no code copied.

    backend/.venv/Scripts/python scripts/verify.py            # everything except build/UI
    backend/.venv/Scripts/python scripts/verify.py --build    # + npm run build
    backend/.venv/Scripts/python scripts/verify.py --ui       # + the Playwright UI check
    scripts/verify.sh ... / scripts/verify.ps1 ...             # thin wrappers, same flags

Steps, in order (each one's output is captured; a failing step prints its tail):
  1. backend tests   pytest tests -q -p no:cacheprovider      (from backend/)
  2. backend lint    ruff, "serious errors only" rule set      (see BACKEND_LINT_RULES)
  3. frontend types  tsc -b --noEmit                           (from frontend/)
  4. frontend lint   oxlint (npm run lint's tool)              (see LINT POLICY)
  5. frontend tests  node --test frontend/tests/*.test.mjs     (pure helpers in src/lib)
  6. frontend build  npm run build                             (--build only)
  7. UI check        scripts/ui_check.py on isolated ports     (--ui only)

Every step runs even after an earlier one fails (--fail-fast stops instead), so
one run shows everything that's wrong. Exit code 0 only if every step passed.

LINT POLICY (the non-obvious choice here; see notes/Decisions.md):
  * Lint *errors* always fail the run.
  * Frontend lint *warnings* are reported (count + the lines) but don't fail the
    run by default: the tree carries pre-existing oxlint warnings in other
    people's code that this harness shouldn't force anyone to fix first.
    --strict-lint turns them into failures (oxlint --deny-warnings).
  * Backend lint gates on ruff's syntax-error / undefined-name rules only. The
    default ruff rule set reports ~200 pre-existing style findings (import
    order, naive datetimes, ...) — a gate that is red on day one gets ignored.
    Ruff lives in backend/requirements-dev.txt, not requirements.txt (the Docker
    image installs that). If ruff isn't installed the step falls back to
    a syntax-only check and says so.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
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
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Syntax errors (E9), invalid comparisons like `is 1` (F63), misplaced
# statements like `return` outside a function (F7), undefined names (F82).
# These are real bugs, never style — and the tree is clean against them, so
# the gate starts green and a new finding means something broke.
BACKEND_LINT_RULES = "E9,F63,F7,F82"
# Relative to backend/. "../scripts" lints this harness with the same rules.
BACKEND_LINT_PATHS = ("app", "tests", "../scripts")

# How much of a failing step's output to show. Enough for a pytest short
# summary or a tsc error list; the full output is one `--verbose` away.
FAILURE_TAIL_LINES = 60

STEP_TIMEOUT_SECONDS = 900

# Default isolated ports for --ui. Never 8000/5173 (Docker's), and away from
# the 81xx/52xx block the build orchestrator hands to parallel agents.
DEFAULT_UI_BE_PORT = 8190
DEFAULT_UI_FE_PORT = 5290


def venv_python() -> str:
    """The backend venv's interpreter (Windows layout first, then POSIX);
    falls back to whatever runs this script."""
    for candidate in (
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
        BACKEND_DIR / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def node_exe() -> str | None:
    return shutil.which("node")


@dataclass
class StepResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    seconds: float = 0.0
    detail: str = ""
    output: str = ""
    extra_lines: list[str] = field(default_factory=list)


def run_cmd(cmd: list[str], cwd: Path, verbose: bool, env: dict | None = None) -> tuple[int, str]:
    """Run one command, capture stdout+stderr together (decoded as UTF-8 with
    replacement — Windows consoles default to cp1252 and pytest/tsc print
    non-ASCII), optionally echoing it live."""
    full_env = dict(os.environ)
    full_env.setdefault("PYTHONIOENCODING", "utf-8")
    full_env["NO_COLOR"] = "1"
    full_env["FORCE_COLOR"] = "0"
    if env:
        full_env.update(env)
    if verbose:
        print(f"    $ {' '.join(cmd)}   (in {cwd})", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            chunks.append(line)
            if verbose:
                sys.stdout.write("    | " + line)
                sys.stdout.flush()
        proc.wait(timeout=STEP_TIMEOUT_SECONDS)
    except BaseException:
        proc.kill()
        raise
    return proc.returncode, "".join(chunks)


def tail(text: str, n: int = FAILURE_TAIL_LINES) -> str:
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-n:])


# --- steps -----------------------------------------------------------------


def step_backend_tests(args) -> StepResult:
    r = StepResult("backend tests", "FAIL")
    cmd = [venv_python(), "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
    if args.pytest_args:
        cmd += args.pytest_args.split()
    code, out = run_cmd(cmd, BACKEND_DIR, args.verbose)
    r.output = out
    summary = next(
        (ln.strip("= \n") for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)),
        "",
    )
    r.detail = summary or f"pytest exit code {code}"
    r.status = "PASS" if code == 0 else "FAIL"
    return r


def step_backend_lint(args) -> StepResult:
    r = StepResult("backend lint", "FAIL")
    py = venv_python()
    probe = subprocess.run([py, "-m", "ruff", "--version"], capture_output=True, text=True, check=False)
    if probe.returncode == 0:
        cmd = [py, "-m", "ruff", "check", "--no-cache", "--select", BACKEND_LINT_RULES, *BACKEND_LINT_PATHS]
        code, out = run_cmd(cmd, BACKEND_DIR, args.verbose)
        r.output = out
        r.status = "PASS" if code == 0 else "FAIL"
        found = re.search(r"Found (\d+) error", out)
        r.detail = (
            f"{probe.stdout.strip()}, rules {BACKEND_LINT_RULES}: "
            + ("clean" if code == 0 else f"{found.group(1) if found else '?'} finding(s)")
        )
        return r
    # No ruff: syntax-only fallback, clearly labelled so nobody mistakes it
    # for the real gate. compile() in memory rather than `compileall`, which
    # would write .pyc files all over the tree.
    code, out = run_cmd([py, "-c", SYNTAX_CHECK_SNIPPET, *BACKEND_LINT_PATHS], BACKEND_DIR, args.verbose)
    r.output = out
    r.status = "PASS" if code == 0 else "FAIL"
    r.detail = (
        "ruff not installed -> syntax-only check "
        "(install: backend/.venv/Scripts/python -m pip install -r backend/requirements-dev.txt)"
    )
    return r


SYNTAX_CHECK_SNIPPET = """
import pathlib, sys
bad = 0
for root in sys.argv[1:]:
    for p in sorted(pathlib.Path(root).rglob("*.py")):
        try:
            compile(p.read_bytes(), str(p), "exec")
        except SyntaxError as exc:
            bad += 1
            print(f"{p}:{exc.lineno}: {exc.msg}")
sys.exit(1 if bad else 0)
"""


def _frontend_ready(r: StepResult) -> str | None:
    """Returns the node executable, or fills `r` with a clear FAIL."""
    node = node_exe()
    if node is None:
        r.detail = "node not found on PATH"
        return None
    if not (FRONTEND_DIR / "node_modules").is_dir():
        r.detail = "frontend/node_modules missing - run `npm install` in frontend/"
        return None
    return node


def step_frontend_types(args) -> StepResult:
    r = StepResult("frontend types", "FAIL")
    node = _frontend_ready(r)
    if node is None:
        return r
    # Call the local TypeScript directly (what `npx tsc` resolves to) — no
    # npx.cmd shim, no chance of npx downloading a different tsc.
    tsc = FRONTEND_DIR / "node_modules" / "typescript" / "bin" / "tsc"
    code, out = run_cmd([node, str(tsc), "-b", "--noEmit"], FRONTEND_DIR, args.verbose)
    r.output = out
    n_errors = len(re.findall(r"error TS\d+", out))
    r.status = "PASS" if code == 0 else "FAIL"
    r.detail = "tsc -b --noEmit: clean" if code == 0 else f"tsc -b --noEmit: {n_errors} error(s)"
    return r


def step_frontend_lint(args) -> StepResult:
    r = StepResult("frontend lint", "FAIL")
    node = _frontend_ready(r)
    if node is None:
        return r
    oxlint = FRONTEND_DIR / "node_modules" / "oxlint" / "bin" / "oxlint"
    cmd = [node, str(oxlint)]
    if args.strict_lint:
        cmd.append("--deny-warnings")
    code, out = run_cmd(cmd, FRONTEND_DIR, args.verbose)
    r.output = out
    # Piped (not a TTY) oxlint prints one `file:line:col: warning|error ...`
    # line per finding and no "Found N" summary, so count those lines.
    warning_lines = [ln.strip() for ln in out.splitlines() if re.search(r":\d+:\d+: warning", ln)]
    error_lines = [ln.strip() for ln in out.splitlines() if re.search(r":\d+:\d+: error", ln)]
    r.status = "PASS" if code == 0 else "FAIL"
    r.detail = f"oxlint: {len(error_lines)} error(s), {len(warning_lines)} warning(s)"
    if code != 0 and not (error_lines or warning_lines):
        r.detail = f"oxlint exit code {code}"
    if warning_lines and r.status == "PASS":
        r.detail += " (warnings don't fail the run; --strict-lint makes them)"
        r.extra_lines = [ln if len(ln) <= 150 else ln[:147] + "..." for ln in warning_lines]
    return r


def step_frontend_unit_tests(args) -> StepResult:
    """`npm test`: node --test over frontend/tests (pure helpers in src/lib; no test framework)."""
    r = StepResult("frontend unit tests", "FAIL")
    node = _frontend_ready(r)
    if node is None:
        return r
    # Call node directly, as the other frontend steps do. Node 22.18+ strips TypeScript types on
    # its own, which is how the tests import the .ts helpers without a build step.
    code, out = run_cmd([node, "--test", "tests/*.test.mjs"], FRONTEND_DIR, args.verbose)
    r.output = out
    r.status = "PASS" if code == 0 else "FAIL"
    passed = re.search(r"^.\s*pass (\d+)", out, re.MULTILINE)
    failed = re.search(r"^.\s*fail (\d+)", out, re.MULTILINE)
    r.detail = f"node --test: {passed.group(1) if passed else '?'} passed, {failed.group(1) if failed else '?'} failed"
    return r


def step_frontend_build(args) -> StepResult:
    r = StepResult("frontend build", "FAIL")
    if _frontend_ready(r) is None:
        return r
    npm = shutil.which("npm")
    if npm is None:
        r.detail = "npm not found on PATH"
        return r
    code, out = run_cmd([npm, "run", "build"], FRONTEND_DIR, args.verbose)
    r.output = out
    r.status = "PASS" if code == 0 else "FAIL"
    built = re.search(r"built in ([\d.]+\s*m?s)", out)
    r.detail = ("npm run build: ok" + (f" (vite {built.group(0)})" if built else "")) if code == 0 else f"npm run build: exit code {code}"
    return r


def step_ui_check(args) -> StepResult:
    r = StepResult("UI check", "FAIL")
    cmd = [
        venv_python(),
        str(SCRIPTS_DIR / "ui_check.py"),
        "--be-port", str(args.be_port),
        "--fe-port", str(args.fe_port),
    ]
    if args.ui_out:
        cmd += ["--out", args.ui_out]
    code, out = run_cmd(cmd, REPO_ROOT, args.verbose)
    r.output = out
    r.status = "PASS" if code == 0 else "FAIL"
    last = next((ln.strip() for ln in reversed(out.splitlines()) if ln.startswith("UI CHECK")), "")
    r.detail = last or f"ui_check.py exit code {code}"
    shots = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("Output:")), "")
    if shots:
        r.extra_lines = [shots]
    return r


# --- main ------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Strategeia verify harness: backend tests + lint, frontend type-check + lint "
        "(optionally build and a live Playwright UI check). Exit code 0 only if every step passed.",
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--backend-only", action="store_true", help="only the backend steps")
    scope.add_argument("--frontend-only", action="store_true", help="only the frontend steps")
    p.add_argument("--build", action="store_true", help="also run `npm run build`")
    p.add_argument("--ui", action="store_true", help="also run scripts/ui_check.py (isolated servers + headless Chrome)")
    p.add_argument("--be-port", type=int, default=DEFAULT_UI_BE_PORT, help=f"--ui backend port (default {DEFAULT_UI_BE_PORT})")
    p.add_argument("--fe-port", type=int, default=DEFAULT_UI_FE_PORT, help=f"--ui frontend port (default {DEFAULT_UI_FE_PORT})")
    p.add_argument("--ui-out", help="--ui screenshot/output dir (default: a fresh temp dir)")
    p.add_argument("--strict-lint", action="store_true", help="frontend lint warnings fail the run too")
    p.add_argument("--fail-fast", action="store_true", help="stop at the first failing step")
    p.add_argument("--pytest-args", default="", help='extra pytest args, e.g. "-k exit_realism"')
    p.add_argument("-v", "--verbose", action="store_true", help="stream every step's full output")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    steps = []
    if not args.frontend_only:
        steps += [step_backend_tests, step_backend_lint]
    if not args.backend_only:
        steps += [step_frontend_types, step_frontend_lint, step_frontend_unit_tests]
        if args.build:
            steps.append(step_frontend_build)
    if args.ui:
        steps.append(step_ui_check)

    print(f"Strategeia verify: {len(steps)} step(s)  [{REPO_ROOT}]", flush=True)
    results: list[StepResult] = []
    total_start = time.monotonic()
    for i, step in enumerate(steps, 1):
        label = step.__name__.removeprefix("step_").replace("_", " ")
        print(f"==> [{i}/{len(steps)}] {label} ...", flush=True)
        start = time.monotonic()
        try:
            res = step(args)
        except FileNotFoundError as exc:
            res = StepResult(label, "FAIL", detail=f"could not start: {exc}")
        except subprocess.TimeoutExpired:
            res = StepResult(label, "FAIL", detail=f"timed out after {STEP_TIMEOUT_SECONDS}s")
        res.seconds = time.monotonic() - start
        results.append(res)
        print(f"    {res.status}  {res.detail}  ({res.seconds:.1f}s)", flush=True)
        for line in res.extra_lines:
            print(f"      {line}")
        if res.status == "FAIL" and res.output and not args.verbose:
            print("    ---- output (tail) ----")
            for line in tail(res.output).splitlines():
                print(f"    | {line}")
            print("    -----------------------", flush=True)
        if res.status == "FAIL" and args.fail_fast:
            break

    skipped = [s for s in steps[len(results):]]
    total = time.monotonic() - total_start
    print()
    print("Summary")
    width = max(len(r.name) for r in results) if results else 10
    for r in results:
        print(f"  {r.status:<4}  {r.name:<{width}}  {r.seconds:6.1f}s  {r.detail}")
    for s in skipped:
        print(f"  SKIP  {s.__name__.removeprefix('step_').replace('_', ' '):<{width}}  (--fail-fast)")
    failed = [r for r in results if r.status == "FAIL"]
    verdict = "FAILED" if failed or skipped else "PASSED"
    print(f"\nVERIFY {verdict}: {len(results) - len(failed)}/{len(steps)} step(s) passed in {total:.1f}s")
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nVERIFY INTERRUPTED", file=sys.stderr)
        sys.exit(130)
