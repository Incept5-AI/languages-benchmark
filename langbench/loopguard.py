"""Detect a reply that has fallen into a repetition loop while it is still streaming.

A reasoning model that loops keeps going until `max_tokens`. On a 262k budget at 14 tok/s that is 8 to 10 hours of
generation that cannot pass, because the text never recovers. LoopGuard watches the stream and says when to stop.

Signal, measured on the last WINDOW characters of one channel (reasoning and content are watched separately):
  * zlib ratio: compressed size / raw size. Prose and code sit near 0.25-0.45; a loop compresses to about 0.01.
  * the shortest unit the text ends with that repeats back to back (exact repetition).
A check is "suspect" when the window compresses below MAX_ZLIB and ends in a unit repeated at least MIN_REPS times,
or compresses below HARD_ZLIB with no exact unit (loops whose counters change). A loop is CONFIRMED after CONFIRM
suspect checks in a row, CHECK_EVERY characters apart, so a long run of repeated text has to persist for
WINDOW + (CONFIRM - 1) * CHECK_EVERY characters (32k by default, roughly 8-10k tokens) before anything is cut.

Calibrated on 2026-10-25 on 1,861 stored coding replies (478 long enough to check) from Incept5's runs: it fires on
three, all failed attempts and all degenerate: two Flint replies that looped to the 262k cap (609k and 1.13M
characters, zlib 0.007 and 0.019) and one Flash-Next reply holding a 65,566-character run of "0". It never fires on
a reply that passed. The closest normal texts compress to 0.085 (dense C) and 0.10 (a reasoning trace).
This file is identical in Incept5-AI/languages-benchmark and Incept5-AI/hle-lite; change both together.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import zlib

VERSION = "1"
DEFAULTS = {"window": 24000, "check_every": 2000, "confirm": 5, "max_zlib": 0.05, "min_reps": 8, "hard_zlib": 0.02,
            "max_unit": 6000}


def zlib_ratio(s: str) -> float:
    b = s.encode("utf-8", "replace")
    return len(zlib.compress(b, 6)) / len(b) if b else 1.0


def tail_unit(s: str, max_unit: int = 6000) -> tuple[int, int]:
    """(length, count) of the unit that `s` ends with, repeated back to back, covering the most of its tail.
    (0, 0) if nothing repeats. Coverage, not shortness, picks the unit: a reply that ends in a blank line would
    otherwise report a one-character unit instead of the 700-character block that has been looping."""
    best = (0, 0)
    for n in range(1, min(max_unit, len(s) // 2) + 1):
        unit = s[-n:]
        if s[-2 * n:-n] != unit:
            continue
        k = 2
        while (k + 1) * n <= len(s) and s[-(k + 1) * n:-k * n] == unit:
            k += 1
        if n * k > best[0] * best[1]:
            best = (n, k)
            if n * k * 2 >= len(s):
                break
    return best


def check(text: str, p: dict | None = None) -> dict | None:
    """One-shot test of a finished text: the loop verdict for its last window, or None."""
    p = {**DEFAULTS, **(p or {})}
    w = text[-p["window"]:]
    if len(w) < p["window"]:
        return None
    z = zlib_ratio(w)
    if z >= p["max_zlib"]:
        return None
    n, k = tail_unit(w, p["max_unit"])
    if k >= p["min_reps"] or z < p["hard_zlib"]:
        return {"zlib": round(z, 4), "unit_chars": n, "repeats": k, "sample": w[-n:][:200] if n else w[-200:]}
    return None


class LoopGuard:
    """Feed streamed text with feed(channel, text); it returns the loop verdict once a loop is confirmed."""

    def __init__(self, params: dict | None = None):
        self.p = {**DEFAULTS, **(params or {})}
        self.buf: dict[str, str] = {}
        self.total: dict[str, int] = {}
        self.next_check: dict[str, int] = {}
        self.streak: dict[str, int] = {}
        self.first_suspect: dict[str, int] = {}
        self.verdict: dict | None = None

    def feed(self, channel: str, text: str, tokens: int = 0) -> dict | None:
        if self.verdict or not text:
            return self.verdict
        p = self.p
        b = self.buf.get(channel, "") + text
        if len(b) > 2 * p["window"]:
            b = b[-p["window"]:]
        self.buf[channel] = b
        t = self.total[channel] = self.total.get(channel, 0) + len(text)
        if t < p["window"] or t < self.next_check.get(channel, 0):
            return None
        self.next_check[channel] = t + p["check_every"]
        hit = check(b, p)
        if not hit:
            self.streak[channel] = 0
            return None
        if not self.streak.get(channel):
            self.first_suspect[channel] = t
        self.streak[channel] = self.streak.get(channel, 0) + 1
        if self.streak[channel] >= p["confirm"]:
            self.verdict = {"channel": channel, "at_chars": t, "at_tokens": tokens,
                            "suspect_from_chars": self.first_suspect[channel], **hit}
        return self.verdict


def stream_chat(url: str, body: dict, headers: dict, timeout: float, guard: LoopGuard | None = None) -> dict:
    """POST an OpenAI chat completion as a stream, assemble it, and stop early if `guard` confirms a loop.

    Returns {content, reasoning, finish_reason, usage, loop, error, seconds}. A confirmed loop closes the
    connection, which makes vLLM, llama.cpp and mlx-vlm cancel the generation, and sets finish_reason "loop".
    `timeout` bounds the whole request, as a non-streamed call would. A server that ignores "stream" and answers
    with one JSON body is read as such.
    """
    body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    out = {"content": "", "reasoning": "", "finish_reason": None, "usage": {}, "loop": None, "error": ""}
    content, reasoning, chunks = [], [], 0
    try:
        # The socket timeout is the whole budget too: a long prompt can take many minutes to reach the first token.
        with urllib.request.urlopen(req, timeout=timeout or None) as r:
            ctype = r.headers.get("Content-Type") or ""
            if "event-stream" not in ctype and "json" in ctype:
                d = json.load(r)
                ch = d["choices"][0]
                msg = ch.get("message") or {}
                out.update(content=msg.get("content") or "",
                           reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
                           finish_reason=ch.get("finish_reason"), usage=d.get("usage") or {})
                # Nothing to cut short, but judge the finished text the same way so the verdict does not depend
                # on whether the server streams.
                for name in ("reasoning", "content") if guard else ():
                    hit = check(out[name], guard.p)
                    if hit:
                        out["loop"], out["finish_reason"] = {"channel": name, **hit}, "loop"
                        break
                out["seconds"] = round(time.time() - t0, 1)
                return out
            for raw in r:
                if timeout and time.time() - t0 > timeout:
                    out["error"] = f"timeout after {timeout} s"
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                except ValueError:
                    continue
                if d.get("error"):
                    out["error"] = f"stream error: {json.dumps(d['error'])[:300]}"
                    break
                if d.get("usage"):
                    out["usage"] = d["usage"]
                for ch in d.get("choices") or []:
                    delta = ch.get("delta") or {}
                    rs = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    cs = delta.get("content") or ""
                    if rs or cs:
                        chunks += 1
                    if rs:
                        reasoning.append(rs)
                    if cs:
                        content.append(cs)
                    if ch.get("finish_reason"):
                        out["finish_reason"] = ch["finish_reason"]
                    if guard and ((rs and guard.feed("reasoning", rs, chunks)) or (cs and guard.feed("content", cs, chunks))):
                        out["loop"], out["finish_reason"] = guard.verdict, "loop"
                        break
                if out["loop"]:
                    break
    except urllib.error.HTTPError as e:
        out["error"] = f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    out["content"], out["reasoning"] = "".join(content), "".join(reasoning)
    if out["loop"] and not out["usage"].get("completion_tokens"):
        out["usage"] = {**out["usage"], "completion_tokens": chunks, "estimated": True}
    out["seconds"] = round(time.time() - t0, 1)
    return out
