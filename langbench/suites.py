"""One interface over the two suites, so the runner, the CLI and the reports treat them alike.

  v1: 8 challenges x 6 languages = 48 tests. One run per program against fixed data. The original battery.
  v2: 20 challenges x 6 languages = 120 tests. Each program runs on the worked example and 2 hidden cases.

A suite knows how to build the prompt for a (challenge, language), pull the program out of a reply, grade it, and
build the single retry prompt. Each suite keeps its own rules for all four, unchanged from where it came from, so
scores stay comparable with the results published for it.
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PKG = Path(__file__).resolve().parent
LANGS = ["python", "java", "swift", "typescript", "c", "rust"]
LANG_NAMES = {"python": "Python", "java": "Java", "swift": "Swift", "typescript": "TypeScript", "c": "C",
              "rust": "Rust"}
REF_FILE = {"python": "main.py", "java": "Main.java", "swift": "main.swift", "typescript": "main.ts", "c": "main.c",
            "rust": "main.rs"}


@dataclass
class Grade:
    passed: bool
    stage: str           # pass | nocode | build | run | output | error
    feedback: str        # what the model is told on the retry
    cases: str = ""      # "passed/total" (v2 only)


class V1:
    name = "v1"
    title = "v1 · 48 tests (8 challenges × 6 languages)"
    CATEGORY = {"json_deep_merge": "data", "csv_analysis": "data", "log_parser": "text", "http_client": "api",
                "dijkstra_graph": "algo", "file_tree_analysis": "files", "system_info": "os",
                "matrix_ops": "algo"}

    def __init__(self):
        sys.path.insert(0, str(PKG / "v1"))
        import suite as S  # noqa: E402
        self.S = S
        self._open = False

    def open(self):
        if self._open:
            return
        S = self.S
        self.dir = tempfile.mkdtemp(prefix="langbench_v1_")
        S.create_fixtures(self.dir)
        S.start_test_server()
        self.port = S._test_server_port
        self.ch = {c["name"]: c for c in S.get_challenges(self.dir, self.port)}
        self._open = True

    def close(self):
        if self._open:
            self.S.stop_test_server()
            self._open = False

    def challenges(self):
        return list(self.CATEGORY)

    def category(self, name):
        return self.CATEGORY[name]

    def ref_path(self, name, lang):
        return PKG / "v1" / "ref" / name / REF_FILE[lang]

    def prompt(self, name, lang):
        self.open()
        return self.S.build_prompt(self.ch[name], lang)

    def ref_code(self, name, lang, path=None):
        """A reference program. v1 prompts name an absolute fixture directory and a port chosen at start-up, so
        references (and hand-written attempts) may use the placeholders __FIXTURE_DIR__ and __PORT__."""
        self.open()
        code = Path(path or self.ref_path(name, lang)).read_text()
        return code.replace("__FIXTURE_DIR__", self.dir).replace("__PORT__", str(self.port))

    def expected(self, name):
        self.open()
        # system_info's answer includes the hostname, which macOS can change mid-run (DHCP/Bonjour). Compute it
        # when grading instead of once at start-up, so a correct program is never failed by the flip.
        if name == "system_info":
            return self.S.compute_sysinfo_expected()
        return self.ch[name]["expected_output"]

    def reply_text(self, content, reasoning):
        # The original grader falls back to the reasoning text when the answer is empty.
        return self.S.strip_thinking((content or "").strip() or (reasoning or "").strip())

    def extract(self, text, lang):
        return self.S.extract_code(text, lang)

    def grade(self, name, lang, code):
        if not code:
            return Grade(False, "nocode", "no code produced")
        ok, out = self.S.run_code(code, lang)
        expected = self.expected(name)
        if ok and self.S.check_output(out, expected):
            return Grade(True, "pass", "")
        if not ok:
            stage = "build" if out.startswith("COMPILATION") else "run"
            return Grade(False, stage, out)
        return Grade(False, "output", f"Output mismatch.\nExpected:\n{expected}\nGot:\n{out}")

    def retry_prompt(self, prompt, lang, code, grade):
        err = grade.feedback
        if len(err) > 2000:
            err = err[:2000] + "\n... [truncated]"
        return (f"The following {LANG_NAMES[lang]} code failed:\n```\n{code}\n```\n"
                f"Error:\n{err}\n\nPlease fix the code. Original task: {prompt}\n"
                f"Output ONLY the corrected code inside a single code block.")

    def toolchains(self):
        return self.S.verify_languages()


class V2:
    name = "v2"
    title = "v2 · 120 tests (20 challenges × 6 languages)"

    def __init__(self):
        sys.path.insert(0, str(PKG / "v2"))
        import harness as H  # noqa: E402
        self.H = H
        self.ctx = None
        self._ch = {}

    def open(self):
        if self.ctx is None:
            self.ctx = self.H.Context()

    def close(self):
        if self.ctx is not None:
            self.ctx.close()
            self.ctx = None

    def challenges(self):
        return self.H.list_challenges()

    def load(self, name):
        if name not in self._ch:
            self._ch[name] = self.H.load_challenge(name)
        return self._ch[name]

    def category(self, name):
        return self.load(name).category

    def ref_path(self, name, lang):
        return PKG / "v2" / "challenges" / name / "ref" / REF_FILE[lang]

    def prepare(self, names):
        """Build every case (and start the mock servers) up front, before workers start."""
        self.open()
        for n in names:
            self.ctx.cases(self.load(n))

    def prompt(self, name, lang):
        self.open()
        return self.H.build_prompt(self.load(name), lang, self.ctx)

    def ref_code(self, name, lang, path=None):
        return Path(path or self.ref_path(name, lang)).read_text()

    def reply_text(self, content, reasoning):
        return self.H.re.sub(r"<think>.*?</think>", "", content or "", flags=self.H.re.DOTALL)

    def extract(self, text, lang):
        return self.H.extract_code(text, lang)

    def grade(self, name, lang, code, keep=None):
        self.open()
        o = self.H.grade(self.load(name), lang, code, self.ctx, keep=keep)
        return Grade(o.passed, o.stage, o.feedback, f"{o.cases_passed}/{o.cases_total}")

    def retry_prompt(self, prompt, lang, code, grade):
        return self.H.retry_prompt(prompt, code, lang, grade.feedback)

    def toolchains(self):
        """Smoke-test every toolchain the way v2 builds and runs programs."""
        hello = {"python": 'print("LANG_OK")',
                 "java": 'public class Main { public static void main(String[] a) { System.out.println("LANG_OK"); } }',
                 "swift": 'print("LANG_OK")', "typescript": 'console.log("LANG_OK");',
                 "c": '#include <stdio.h>\nint main(void) { printf("LANG_OK\\n"); return 0; }',
                 "rust": 'fn main() { println!("LANG_OK"); }'}
        out = {}
        with tempfile.TemporaryDirectory() as d:
            for lang, code in hello.items():
                wd = Path(d) / lang
                wd.mkdir()
                ok, msg = self.H.build(code, lang, wd)
                if not ok:
                    out[lang] = msg[:300]
                    continue
                status, so, se = self.H.run_case(lang, wd, self.H.Case(), 60)
                out[lang] = "" if status == "ok" and "LANG_OK" in so else (se or so or status)[:300]
        return out


SUITES = {"v1": V1, "v2": V2}


def get(name):
    if name not in SUITES:
        raise SystemExit(f"unknown suite {name!r} (choose from {', '.join(SUITES)})")
    return SUITES[name]()
