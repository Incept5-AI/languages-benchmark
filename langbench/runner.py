"""Run a suite against any OpenAI-compatible chat endpoint and write one JSON result file per run.

Request shape (identical for every model, so results compare): one user message holding the prompt, no system
prompt, no tools. Only wire-level knobs differ: `reasoning_effort`, `chat_template_kwargs.enable_thinking`,
`max_tokens` and sampling. On failure the model gets one fresh single-turn retry carrying its program and the
grader's feedback, so no reasoning ever has to be replayed.

Scoring: 1.0 pass on the first try, 0.5 pass after the retry, 0.0 otherwise. Request errors and timeouts score
0 and stay in the denominator. The file is rewritten after every test, so a partial file is readable, and a run
that stops can be resumed with the same command.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import suites

VERSION = "1.0.0"
DEFAULT_MAX_TOKENS = 32768

# Qwen-family coding presets. They are what every published result in this repo used.
SAMPLING = {
    "thinking": dict(temperature=0.6, top_p=0.95, top_k=20, presence_penalty=0.0),
    "no-thinking": dict(temperature=0.7, top_p=0.80, top_k=20, presence_penalty=1.5),
}


def api_key():
    return next((os.environ[n] for n in ("LB_API_KEY", "OPENAI_API_KEY") if os.environ.get(n)), "")


def chat(cfg, prompt):
    """One chat completion. Returns a dict with content, reasoning and usage, or with `error` set."""
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}], **cfg["sampling"]}
    if cfg["max_tokens"]:
        body["max_tokens"] = cfg["max_tokens"]
    if cfg["effort"]:
        body["reasoning_effort"] = cfg["effort"]
    if cfg["mode"] == "no-thinking":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    body.update(cfg.get("extra_body") or {})
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    req = urllib.request.Request(f"{cfg['base_url'].rstrip('/')}/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}",
                "seconds": round(time.time() - t0, 1)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "seconds": round(time.time() - t0, 1)}
    secs = time.time() - t0
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError):
        return {"error": f"bad response: {json.dumps(payload)[:300]}", "seconds": round(secs, 1)}
    msg = choice.get("message") or {}
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    usage = payload.get("usage") or {}
    completion = usage.get("completion_tokens") or 0
    return {"content": msg.get("content") or "", "reasoning": reasoning, "seconds": round(secs, 1),
            "finish_reason": choice.get("finish_reason"), "completion_tokens": completion,
            "prompt_tokens": usage.get("prompt_tokens"),
            "tok_per_s": round(completion / secs, 1) if secs and completion else 0, "error": ""}


def run_test(suite, cfg, name, lang):
    prompt = suite.prompt(name, lang)
    attempts, cur = [], prompt
    for n in (1, 2):
        r = chat(cfg, cur)
        text = suite.reply_text(r.get("content"), r.get("reasoning")) if not r["error"] else ""
        code = suite.extract(text, lang) if text else ""
        g = (suites.Grade(False, "error", f"request failed: {r['error']}") if r["error"]
             else suite.grade(name, lang, code))
        att = {"n": n, "seconds": r.get("seconds"), "finish_reason": r.get("finish_reason"),
               "completion_tokens": r.get("completion_tokens"), "prompt_tokens": r.get("prompt_tokens"),
               "tok_per_s": r.get("tok_per_s"), "reasoning_chars": len(r.get("reasoning") or ""),
               "error": r["error"], "stage": g.stage, "passed": g.passed, "cases": g.cases,
               "feedback": g.feedback[:4000], "code": code}
        if cfg["save_replies"]:
            att["content"] = r.get("content")
        attempts.append(att)
        if g.passed:
            return {"challenge": name, "category": suite.category(name), "language": lang,
                    "score": 1.0 if n == 1 else 0.5, "attempts": attempts}
        if r["error"] or n == 2:
            break
        cur = suite.retry_prompt(prompt, lang, code, g)
    return {"challenge": name, "category": suite.category(name), "language": lang, "score": 0.0,
            "attempts": attempts}


def summarise(state):
    recs = state["records"]
    att = [a for r in recs for a in r["attempts"]]
    state["total_score"] = sum(r["score"] for r in recs)
    state["max_score"] = len(recs)
    state["pass_first"] = sum(1 for r in recs if r["score"] == 1.0)
    state["pass_any"] = sum(1 for r in recs if r["score"] > 0)
    state["well_formed_first"] = sum(1 for r in recs if r["attempts"][0].get("code"))
    state["hit_cap"] = sum(1 for a in att if a.get("finish_reason") == "length")
    state["request_errors"] = sum(1 for a in att if a.get("error"))
    state["completion_tokens"] = sum(a.get("completion_tokens") or 0 for a in att)
    rates = [a["tok_per_s"] for a in att if a.get("tok_per_s")]
    state["median_tok_per_s"] = round(statistics.median(rates), 1) if rates else 0
    by, byc = defaultdict(float), defaultdict(float)
    for r in recs:
        by[r["language"]] += r["score"]
        byc[r["challenge"]] += r["score"]
    state["by_language"] = dict(by)
    state["by_challenge"] = dict(byc)


# Settings that must match for --resume to continue an existing file rather than refuse.
RESUME_KEYS = ("suite", "model", "mode", "effort", "max_tokens", "sampling")


def run(cfg, out: Path, log=print):
    suite = suites.get(cfg["suite"])
    names = cfg["challenges"] or suite.challenges()
    unknown = set(names) - set(suite.challenges())
    if unknown:
        raise SystemExit(f"unknown {cfg['suite']} challenges: {', '.join(sorted(unknown))}")
    langs = cfg["langs"] or suites.LANGS
    jobs = [(n, l) for n in names for l in langs]

    state = {"benchmark": "languages-benchmark", "version": VERSION, "suite": cfg["suite"], "tag": cfg["tag"],
             "label": cfg["label"] or cfg["tag"], "model": cfg["model"], "engine": cfg["engine"],
             "hardware": cfg["hardware"], "mode": cfg["mode"], "effort": cfg["effort"],
             "max_tokens": cfg["max_tokens"], "sampling": cfg["sampling"],
             "extra_body": cfg.get("extra_body") or None, "workers": cfg["workers"],
             "scoring": "1.0 first try, 0.5 after one feedback round, 0.0 fail; errors count",
             "started": time.strftime("%Y-%m-%d %H:%M:%S"), "elapsed_s": 0.0, "records": []}
    if out.exists() and not cfg["fresh"]:
        old = json.loads(out.read_text())
        diff = [k for k in RESUME_KEYS if old.get(k) != state[k]]
        if diff:
            raise SystemExit(f"{out} exists with different settings ({', '.join(diff)}); "
                             f"use --fresh to overwrite or a different --out/--tag")
        state.update({k: old[k] for k in ("started", "elapsed_s", "records") if k in old})
        # Re-run records whose only failure was a request error (server down, tunnel dropped).
        state["records"] = [r for r in state["records"]
                            if not (r["score"] == 0 and all(a.get("error") for a in r["attempts"]))]
        state["resumed"] = state.get("resumed", 0) + 1
    done = {(r["challenge"], r["language"]) for r in state["records"]}
    todo = [j for j in jobs if j not in done]
    out.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(suite, "prepare"):
        suite.prepare(names)
    else:
        suite.open()
    cap = f"max_tokens {cfg['max_tokens']}" if cfg["max_tokens"] else "no max_tokens (server default)"
    log(f"{cfg['suite']}: {len(todo)} of {len(jobs)} tests to run · {cfg['workers']} workers · {cfg['model']} · "
        f"{cfg['mode']}{' · effort ' + cfg['effort'] if cfg['effort'] else ''} · {cap}")
    lock = threading.Lock()
    t0 = time.time() - state["elapsed_s"]

    def work(job):
        name, lang = job
        try:
            rec = run_test(suite, cfg, name, lang)
        except Exception as e:
            rec = {"challenge": name, "category": suite.category(name), "language": lang, "score": 0.0,
                   "attempts": [{"n": 1, "error": f"harness: {e!r}"[:300], "stage": "error"}]}
        with lock:
            state["records"].append(rec)
            summarise(state)
            state["elapsed_s"] = round(time.time() - t0, 1)
            out.write_text(json.dumps(state, indent=1, ensure_ascii=False))
            last = rec["attempts"][-1]
            note = last.get("error") or ("hit cap" if last.get("finish_reason") == "length" else last.get("stage"))
            log(f"  [{len(state['records']):3d}/{len(jobs)}] {name:22s} {lang:10s} {rec['score']:.1f}  "
                f"{(note or '')[:60]}")

    try:
        with ThreadPoolExecutor(cfg["workers"]) as pool:
            list(pool.map(work, todo))
    finally:
        suite.close()
    summarise(state)
    out.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    s = state
    log(f"\n{s['tag']}: {s['total_score']:.1f}/{s['max_score']}  first-try {s['pass_first']}  any {s['pass_any']}  "
        f"hit-cap {s['hit_cap']}  errors {s['request_errors']}  {s['elapsed_s'] / 60:.0f} min")
    for l in langs:
        log(f"  {suites.LANG_NAMES[l]:11s} {s['by_language'].get(l, 0):5.1f}/{len(names)}")
    log(f"→ {out}")
    return state
