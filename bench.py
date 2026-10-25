#!/usr/bin/env python3
"""languages-benchmark: coding benchmarks for LLMs in Python, Java, Swift, TypeScript, C and Rust.

  python3 bench.py doctor                                   # check the six toolchains
  python3 bench.py list [--suite v1|v2]                     # challenges and reference solutions
  python3 bench.py prompt <challenge> <lang> [--suite v2]   # the exact prompt a model receives
  python3 bench.py try <challenge> <lang> <file>            # grade one program, print the model's feedback
  python3 bench.py verify [--suite v1,v2]                   # grade every reference solution
  python3 bench.py run --model <id> [--suite v1,v2] [--base-url URL] [--max-tokens 32768|0] [--runs 3] ...
  python3 bench.py report results/*.json [--markdown] [--pair]
  python3 bench.py html results/*.json -o report.html [--svg-dir docs/img]

Everything is Python standard library. See README.md.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langbench import loopguard, report, runner, suites

ROOT = Path(__file__).resolve().parent


def suite_names(s):
    return [x for x in s.split(",") if x]


def find_suite(challenge, wanted):
    for name in suite_names(wanted) or ["v2", "v1"]:
        s = suites.get(name)
        if challenge in s.challenges():
            return s
    raise SystemExit(f"no challenge {challenge!r} in suite(s) {wanted or 'v1,v2'}")


def cmd_doctor(a):
    bad = 0
    for name in suite_names(a.suite):
        s = suites.get(name)
        res = s.toolchains()
        print(f"{name}: " + "  ".join(f"{suites.LANG_NAMES[l]} {'ok' if not e else 'FAIL'}" for l, e in res.items()))
        for l, e in res.items():
            if e:
                bad += 1
                print(f"   {l}: {e.strip()[:300]}")
    sys.exit(1 if bad else 0)


def cmd_list(a):
    for name in suite_names(a.suite):
        s = suites.get(name)
        print(f"\n{s.title}")
        for c in s.challenges():
            refs = " ".join(l for l in suites.LANGS if s.ref_path(c, l).exists())
            print(f"  {c:22s} {s.category(c):7s} refs: {refs}")
        s.close()


def cmd_prompt(a):
    s = find_suite(a.args[0], a.suite)
    try:
        print(s.prompt(a.args[0], a.args[1]))
    finally:
        s.close()


def cmd_try(a):
    name, lang, f = a.args[:3]
    s = find_suite(name, a.suite)
    try:
        t0 = time.time()
        g = s.grade(name, lang, s.ref_code(name, lang, f))
        print("PASS" if g.passed else f"FAIL ({g.stage})", f"{g.cases + ' cases, ' if g.cases else ''}"
              f"{time.time() - t0:.1f}s")
        if not g.passed:
            print(g.feedback)
    finally:
        s.close()
    sys.exit(0 if g.passed else 1)


def cmd_verify(a):
    langs = [l for l in a.langs.split(",") if l]
    failed = 0
    for name in suite_names(a.suite):
        s = suites.get(name)
        names = [c for c in (a.args or s.challenges()) if c in s.challenges()]
        if not names:
            continue
        if hasattr(s, "prepare"):
            s.prepare(names)
        else:
            s.open()
        jobs = [(c, l) for c in names for l in langs]

        def one(job):
            c, l = job
            p = s.ref_path(c, l)
            return c, l, (s.grade(c, l, s.ref_code(c, l)) if p.exists() else None)

        t0 = time.time()
        try:
            with ThreadPoolExecutor(a.workers) as pool:
                res = list(pool.map(one, jobs))
        finally:
            s.close()
        grid = {(c, l): ("—" if g is None else "PASS" if g.passed else g.stage.upper()) for c, l, g in res}
        print(f"\n{s.title}\n{'challenge':22s} " + " ".join(f"{l[:6]:>6s}" for l in langs))
        for c in names:
            print(f"{c:22s} " + " ".join(f"{grid[(c, l)]:>6s}" for l in langs))
        bad = [(c, l, g) for c, l, g in res if g is not None and not g.passed]
        for c, l, g in bad:
            print(f"\n--- {c} / {l}: {g.feedback[:1500]}")
        missing = sum(1 for *_, g in res if g is None)
        print(f"{name}: {sum(1 for *_, g in res if g and g.passed)}/{len(res)} pass, {len(bad)} fail, "
              f"{missing} missing, {time.time() - t0:.0f}s")
        failed += len(bad) + missing
    sys.exit(1 if failed else 0)


def cmd_run(a):
    if not a.model:
        raise SystemExit("--model is required")
    mode = "no-thinking" if a.no_thinking else "thinking"
    sampling = dict(runner.SAMPLING[mode])
    for k in ("temperature", "top_p", "top_k", "presence_penalty"):
        if getattr(a, k) is not None:
            sampling[k] = getattr(a, k)
    base_tag = a.tag or "-".join(x for x in (a.model.rstrip("/").split("/")[-1], a.effort or None,
                                             "nothink" if a.no_thinking else None,
                                             f"{a.max_tokens // 1024}k" if a.max_tokens else "nocap") if x)
    for suite in suite_names(a.suite):
        for i in range(1, a.runs + 1):
            tag = f"{base_tag}-r{i}" if a.runs > 1 else base_tag
            out = Path(a.out) if a.out and a.runs == 1 and len(suite_names(a.suite)) == 1 else \
                ROOT / "results" / f"{suite}-{tag}.json"
            cfg = dict(suite=suite, base_url=a.base_url, model=a.model, api_key=runner.api_key(),
                       mode=mode, effort=a.effort or None, max_tokens=a.max_tokens, sampling=sampling,
                       extra_body=json.loads(a.extra_body) if a.extra_body else None, timeout=a.timeout,
                       workers=a.workers, tag=tag, label=a.label, engine=a.engine, hardware=a.hardware,
                       challenges=[c for c in a.challenges.split(",") if c],
                       langs=[l for l in a.langs.split(",") if l], save_replies=not a.no_replies, fresh=a.fresh,
                       loop_guard=None if a.no_loop_guard else dict(loopguard.DEFAULTS))
            runner.run(cfg, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["doctor", "list", "prompt", "try", "verify", "run", "report", "html"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--suite", default="", help="v1, v2 or v1,v2 (default: both)")
    ap.add_argument("--langs", default=",".join(suites.LANGS), help="comma-separated subset of languages")
    ap.add_argument("--workers", type=int, default=4, help="tests in flight at once")
    g = ap.add_argument_group("run")
    g.add_argument("--base-url", default="http://localhost:8000/v1", help="OpenAI-compatible endpoint")
    g.add_argument("--model", default="", help="model id sent in each request")
    g.add_argument("--max-tokens", type=int, default=runner.DEFAULT_MAX_TOKENS,
                   help="per-request token cap (default 32768); 0 sends none, so the server's limit applies")
    g.add_argument("--effort", default="", help="reasoning_effort to send (low|medium|high|...)")
    g.add_argument("--no-thinking", action="store_true", help="send enable_thinking=false and no-thinking sampling")
    g.add_argument("--temperature", type=float)
    g.add_argument("--top-p", dest="top_p", type=float)
    g.add_argument("--top-k", dest="top_k", type=int)
    g.add_argument("--presence-penalty", dest="presence_penalty", type=float)
    g.add_argument("--extra-body", default="", help="JSON merged into every request body")
    g.add_argument("--timeout", type=int, default=3600, help="seconds per request")
    g.add_argument("--runs", type=int, default=1, help="repeat the whole suite N times (files -r1..-rN)")
    g.add_argument("--challenges", default="", help="comma-separated subset of challenges")
    g.add_argument("--tag", default=None, help="name for the result file (default from model/effort/cap)")
    g.add_argument("--label", default="", help="display name in reports")
    g.add_argument("--engine", default="", help="free text recorded in the result, e.g. 'vLLM 0.32, FP8'")
    g.add_argument("--hardware", default="", help="free text recorded in the result, e.g. 'M5 Max 128 GB'")
    g.add_argument("--out", default="", help="result file (single suite, single run)")
    g.add_argument("--fresh", action="store_true", help="overwrite an existing result file instead of resuming")
    g.add_argument("--no-replies", action="store_true", help="store code only, not the full replies")
    g.add_argument("--no-loop-guard", action="store_true",
                   help="don't stream or cut looping replies (the 1.0.0 behaviour; needed to resume a 1.0.0 file)")
    r = ap.add_argument_group("report / html")
    r.add_argument("--markdown", action="store_true", help="report: print markdown tables")
    r.add_argument("--pair", action="store_true", help="report: paired exact McNemar for two runs")
    r.add_argument("-o", "--output", default="report.html", help="html: output file")
    r.add_argument("--title", default="Languages benchmark", help="html: page title")
    r.add_argument("--svg-dir", default="", help="html: also write each chart as a standalone .svg here")
    a = ap.parse_args()
    if not a.suite:
        a.suite = "v1,v2"
    if a.cmd in ("prompt", "try") and len(a.args) < (2 if a.cmd == "prompt" else 3):
        ap.error(f"{a.cmd} needs <challenge> <lang>{' <file>' if a.cmd == 'try' else ''}")
    if a.cmd in ("prompt", "try") and a.args[1] not in suites.LANGS:
        ap.error(f"language must be one of {', '.join(suites.LANGS)}")
    if a.cmd == "report":
        report.cli_report(a.args, a.markdown, a.pair)
    elif a.cmd == "html":
        report.cli_html(a.args, a.output, a.title, a.svg_dir)
    else:
        globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
