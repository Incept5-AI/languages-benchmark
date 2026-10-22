"""codebench2 harness: languages, prompt contract, build/run, grading, retry feedback.

One task = (challenge, language). The model writes ONE complete program. The harness builds it once,
then runs it against every case of the challenge: case 0 is the worked example the prompt describes,
cases 1+ are hidden cases with different data. A test passes only if every case passes, so a program
that prints the example's answer cannot pass.

Everything a model sees is produced here (build_prompt, feedback), identically for every model.
"""
from __future__ import annotations

import difflib
import importlib.util
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent
CHALLENGES_DIR = ROOT / "challenges"
JAVA_BIN = (os.environ.get("LB_JAVA_BIN") or os.environ.get("CB2_JAVA_BIN")
            or ("/opt/homebrew/opt/openjdk@21/bin" if os.path.isdir("/opt/homebrew/opt/openjdk@21/bin") else ""))
BUILD_TIMEOUT = 180
FEEDBACK_LIMIT = 2500

# ── Languages ────────────────────────────────────────────────────────────────
# `build`/`run` are argv lists run with cwd = the task's work directory. The same strings are shown to the
# model, so what it is told is exactly what happens.
LANGS = {
    "python": dict(name="Python", file="main.py", version="Python 3.13",
                   build=None, run=["python3", "main.py"]),
    "java": dict(name="Java", file="Main.java", version="OpenJDK 21",
                 build=["javac", "Main.java"], run=["java", "-cp", ".", "Main"]),
    "swift": dict(name="Swift", file="main.swift", version="Swift 6.4 (Apple, macOS SDK)",
                  build=["swiftc", "-O", "main.swift", "-o", "main"], run=["./main"]),
    "typescript": dict(name="TypeScript", file="main.ts", version="Node.js 26 via tsx (no type-check step)",
                       build=None, run=["npx", "tsx", "main.ts"]),
    "c": dict(name="C", file="main.c", version="Apple clang 21, C17",
              build=["cc", "-O2", "-std=c17", "main.c", "-o", "main", "-lm"], run=["./main"]),
    "rust": dict(name="Rust", file="main.rs", version="rustc 1.96, edition 2021",
                 build=["rustc", "-O", "--edition", "2021", "-A", "warnings", "main.rs", "-o", "main"],
                 run=["./main"]),
}
LANGS["python"]["version"] = None  # filled lazily by build_prompt
FENCE_ALIASES = {"python": ["python", "py", "python3"], "java": ["java"], "swift": ["swift"],
                 "typescript": ["typescript", "ts", "tsx"], "c": ["c"], "rust": ["rust", "rs"]}


def _detect_python_version() -> str:
    """The prompt must name the interpreter that actually runs (tool_env puts Homebrew first)."""
    try:
        out = subprocess.run(["python3", "--version"], capture_output=True, text=True, env=tool_env(), timeout=10)
        return ".".join(out.stdout.split()[1].split(".")[:2]).join(["Python ", ""])
    except Exception:
        return "Python 3"


def tool_env() -> dict:
    env = os.environ.copy()
    extra = [p for p in (JAVA_BIN, str(Path.home() / ".cargo/bin"), "/opt/homebrew/bin") if p and os.path.isdir(p)]
    env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    return env


# ── Challenge API ────────────────────────────────────────────────────────────
@dataclass
class Case:
    """One execution of the program. `args` are appended to the run command.

    Grading: if `check` is set it is called as check(stdout, workdir) -> (ok, message); otherwise stdout is
    compared with `expected` line by line (trailing whitespace on each line and trailing blank lines ignored).
    `files` are written into the work directory before the run (name -> bytes)."""
    args: list = field(default_factory=list)
    stdin: Optional[bytes] = None
    expected: Optional[str] = None
    check: Optional[Callable] = None
    files: dict = field(default_factory=dict)
    timeout: Optional[int] = None


class Challenge:
    """Subclass in challenges/<name>/challenge.py as `class Challenge(harness.Challenge)`."""
    name = ""
    title = ""
    category = ""        # api | tools | binary | text | lang
    timeout = 20         # seconds per case run
    n_hidden = 2         # hidden cases (seeds 1..n_hidden); seed 0 is the example

    def start(self, ctx: "Context") -> None:
        """Start shared services (servers). Called once per session; store ports on self."""

    def stop(self) -> None:
        pass

    def prompt(self, ctx: "Context") -> str:
        """The language-neutral task text. Must describe the invocation (arguments) precisely."""
        raise NotImplementedError

    def case(self, seed: int, ctx: "Context") -> Case:
        """Build case `seed` (write fixtures under ctx.data_dir(self, seed)) and return it."""
        raise NotImplementedError


class Context:
    """Per-session state: a scratch root for fixtures, started services, and built cases."""

    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or tempfile.mkdtemp(prefix="cb2_"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._started: dict = {}
        self._cases: dict = {}

    def data_dir(self, ch: Challenge, seed: int) -> Path:
        d = self.root / "data" / ch.name / str(seed)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ensure(self, ch: Challenge) -> None:
        if ch.name not in self._started:
            ch.start(self)
            self._started[ch.name] = ch

    def cases(self, ch: Challenge) -> list:
        self.ensure(ch)
        if ch.name not in self._cases:
            self._cases[ch.name] = [ch.case(s, self) for s in range(ch.n_hidden + 1)]
        return self._cases[ch.name]

    def close(self) -> None:
        for ch in self._started.values():
            try:
                ch.stop()
            except Exception:
                pass
        self._started.clear()


def load_challenge(name: str) -> Challenge:
    path = CHALLENGES_DIR / name / "challenge.py"
    spec = importlib.util.spec_from_file_location(f"cb2_challenge_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ch = mod.Challenge()
    ch.name = ch.name or name
    return ch


def list_challenges() -> list:
    return sorted(p.parent.name for p in CHALLENGES_DIR.glob("*/challenge.py"))


# ── Prompt ───────────────────────────────────────────────────────────────────
def build_prompt(ch: Challenge, lang: str, ctx: Context) -> str:
    L = LANGS[lang]
    if L["version"] is None:
        L["version"] = _detect_python_version()
    body = ch.prompt(ctx).replace("{LANG}", L["name"]).strip()
    build = " ".join(L["build"]) if L["build"] else "(no build step)"
    contract = [
        f"Write a complete {L['name']} program for the task below.",
        "",
        body,
        "",
        "## How your program is built and run",
        f"- Toolchain: {L['version']} on macOS (arm64).",
        f"- The program is saved as `{L['file']}` in an empty working directory, built with `{build}`, "
        f"and run as `{' '.join(L['run'])} <arguments>` with that directory as the current directory.",
        "- It is run on several test cases with different input data, most of them hidden from you. Every run "
        "must produce exactly the specified output.",
        "- Use only the language's standard library and the frameworks that ship with macOS. "
        "No third-party packages, modules or crates are installed.",
        "- Standard output is compared exactly (trailing spaces at line ends are ignored). Write nothing else "
        "to standard output. Exit with status 0.",
        "",
        f"Reply with the entire program in a single ```{FENCE_ALIASES[lang][0]} code block.",
    ]
    return "\n".join(contract)


def retry_prompt(original: str, code: str, lang: str, feedback: str) -> str:
    return (f"{original}\n\n---\n\nYour previous program:\n```{FENCE_ALIASES[lang][0]}\n{code}\n```\n\n"
            f"It failed:\n{feedback}\n\nThe test cases and expected outputs are correct. Fix the program and "
            f"reply with the entire corrected program in a single code block.")


# ── Extraction ───────────────────────────────────────────────────────────────
def extract_code(text: str, lang: str) -> str:
    """The longest fenced block tagged with the language; else the longest fenced block; else ''.

    The prompt asks for one block. Longest (not first, not concatenated) because models that sketch a
    snippet before the answer would otherwise submit the sketch, or a sketch glued to the program."""
    for alias in FENCE_ALIASES[lang]:
        blocks = re.findall(rf"```{alias}[ \t]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if blocks:
            return max(blocks, key=len).strip("\n")
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    return max(blocks, key=len).strip("\n") if blocks else ""


# ── Build / run / grade ──────────────────────────────────────────────────────
@dataclass
class Outcome:
    passed: bool
    stage: str               # build | run | output | pass | nocode
    feedback: str            # what the model is told on failure
    cases_passed: int = 0
    cases_total: int = 0
    seconds: float = 0.0
    detail: list = field(default_factory=list)


def _clip(s: str, n: int = FEEDBACK_LIMIT) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n // 2] + "\n…[truncated]…\n" + s[-n // 2:]


def compare_lines(actual: str, expected: str) -> bool:
    norm = lambda t: [l.rstrip() for l in t.rstrip().splitlines()]
    return norm(actual) == norm(expected)


def diff_feedback(actual: str, expected: str, show_all: bool) -> str:
    a = [l.rstrip() for l in actual.rstrip().splitlines()]
    e = [l.rstrip() for l in expected.rstrip().splitlines()]
    if show_all:
        d = list(difflib.unified_diff(e, a, "expected", "your output", lineterm="", n=2))
        return _clip("\n".join(d[:80]))
    for i in range(max(len(a), len(e))):
        ea = e[i] if i < len(e) else "<no more lines>"
        aa = a[i] if i < len(a) else "<no more lines>"
        if ea != aa:
            return f"first difference at line {i + 1}:\n  expected: {ea[:300]}\n  got:      {aa[:300]}"
    return "outputs differ"


def _run(cmd, cwd, timeout, input=None, text=False):
    """subprocess.run, but a timeout kills the whole process group. `npx tsx` and `java` start grandchildren;
    plain run() kills only the child, then waits forever on pipes a grandchild still holds (2026-10-14: two
    timed-out tsx programs hung their grader for 3 h)."""
    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=text, env=tool_env(), start_new_session=True)
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def build(code: str, lang: str, workdir: Path) -> tuple:
    L = LANGS[lang]
    (workdir / L["file"]).write_text(code)
    if not L["build"]:
        return True, ""
    try:
        r = _run(L["build"], workdir, BUILD_TIMEOUT, text=True)
    except subprocess.TimeoutExpired:
        return False, f"Build timed out after {BUILD_TIMEOUT}s."
    if r.returncode != 0:
        return False, "Build failed:\n" + _clip(r.stderr + r.stdout)
    return True, ""


def run_case(lang: str, workdir: Path, case: Case, timeout: int) -> tuple:
    """Returns (status, stdout, stderr) with status in ok | crash | timeout."""
    for name, data in case.files.items():
        p = workdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    cmd = LANGS[lang]["run"] + [str(a) for a in case.args]
    try:
        r = _run(cmd, workdir, case.timeout or timeout, input=case.stdin)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace")
        return "timeout", out, (e.stderr or b"").decode(errors="replace")
    out = r.stdout.decode(errors="replace")
    err = r.stderr.decode(errors="replace")
    if r.returncode != 0:
        return "crash", out, err + f"\n(exit status {r.returncode})"
    return "ok", out, err


def grade(ch: Challenge, lang: str, code: str, ctx: Context, keep: Optional[Path] = None) -> Outcome:
    """Build `code` once and run it on every case. Stops at the first failing case."""
    t0 = time.time()
    if not code.strip():
        return Outcome(False, "nocode", "No code block was found in your reply.")
    cases = ctx.cases(ch)
    base = Path(tempfile.mkdtemp(prefix=f"cb2_{ch.name}_{lang}_", dir=ctx.root))
    try:
        ok, msg = build(code, lang, base)
        if not ok:
            return Outcome(False, "build", msg, 0, len(cases), time.time() - t0)
        passed = 0
        for i, case in enumerate(cases):
            wd = base / f"run{i}"
            shutil.copytree(base, wd, ignore=shutil.ignore_patterns("run*"))
            status, out, err = run_case(lang, wd, case, ch.timeout)
            label = "the example case" if i == 0 else f"hidden case {i}"
            if status == "timeout":
                return Outcome(False, "run", f"On {label} the program timed out after "
                               f"{case.timeout or ch.timeout}s.\nstderr:\n{_clip(err, 800)}",
                               passed, len(cases), time.time() - t0)
            if status == "crash":
                return Outcome(False, "run", f"On {label} the program exited with an error.\nstderr:\n"
                               f"{_clip(err)}\nstdout (first lines):\n{_clip(out, 600)}",
                               passed, len(cases), time.time() - t0)
            if case.check:
                good, why = case.check(out, wd)
            else:
                good = compare_lines(out, case.expected)
                why = "" if good else diff_feedback(out, case.expected, show_all=(i == 0))
            if not good:
                head = ("Wrong output on the example case (diff of expected vs your output):" if i == 0 else
                        f"Correct on the example case but wrong on hidden case {i} (different input data), ")
                return Outcome(False, "output", head + "\n" + _clip(why), passed, len(cases),
                               time.time() - t0)
            passed += 1
        return Outcome(True, "pass", "", passed, len(cases), time.time() - t0)
    finally:
        if keep is None:
            shutil.rmtree(base, ignore_errors=True)
        else:
            shutil.copytree(base, keep, dirs_exist_ok=True)
            shutil.rmtree(base, ignore_errors=True)
