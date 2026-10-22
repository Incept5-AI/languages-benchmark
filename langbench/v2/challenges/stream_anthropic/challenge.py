"""stream_anthropic — an Anthropic Messages API streaming client with extended thinking and tool use.

Hard parts: HTTP/1.1 chunked transfer encoding whose chunk boundaries split SSE lines, CRLF pairs and UTF-8
sequences; SSE framing (CRLF or LF, `data:` without a space, comment lines, a data field spread over two
`data:` lines, unknown event types, pings); reassembling text/thinking deltas; the signature arriving in a
signature_delta; tool inputs arriving as input_json_delta fragments split mid-escape (incl. surrogate-pair
escapes) that may only be parsed once complete; usage taken from the right events; the second request
echoing the assistant content exactly (thinking block + signature first) with tool_result blocks in order
(is_error on the failed one); and tool results that count UTF-16 units / UTF-8 bytes / reverse by code point.
The mock is stateless: request 2 is validated against what the seed's first stream contained.
"""
import base64
import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

MODEL = "claude-sonnet-4-5-20250929"
VERSION = "2023-06-01"

PROMPT = """\
Write a {LANG} program that is a streaming client for the Anthropic Messages API with extended thinking and
tool use. It sends one request, reads the streamed reply, runs the tools the model asked for, sends a second
request with the tool results, reads that streamed reply, and prints a transcript.

The program is run with two arguments: `<data_dir> <base_url>`. `<base_url>` looks like
`http://127.0.0.1:50123/c/0` (no trailing slash); the endpoint is `POST <base_url>/v1/messages`.

`<data_dir>/request.json`:
```json
{"api_key": "sk-ant-…", "model": "claude-…", "max_tokens": 8000, "thinking_budget": 2048,
 "system": "…", "prompt": "…", "tools": [ … ]}
```

## Requests
Both requests are HTTP/1.1 `POST <base_url>/v1/messages` with headers `Host`, `Content-Length`,
`x-api-key: <api_key>`, `anthropic-version: 2023-06-01` and `content-type: application/json`, and a JSON body with exactly these keys
(any other key is rejected with 400):
```
{"model": <model>, "max_tokens": <max_tokens>, "stream": true,
 "thinking": {"type": "enabled", "budget_tokens": <thinking_budget>},
 "system": <system>, "tools": <tools, unchanged>, "messages": [...]}
```
Request 1 has `"messages": [{"role": "user", "content": <prompt>}]`.

Request 2 has three messages: the same user message; then `{"role": "assistant", "content": [...]}` holding
every content block of the first reply in index order, rebuilt exactly — `{"type": "thinking", "thinking":
<full text>, "signature": <signature>}`, `{"type": "text", "text": <full text>}`,
`{"type": "tool_use", "id": ..., "name": ..., "input": <the parsed input object>}` (no other fields); then
`{"role": "user", "content": [...]}` with one block per tool_use block, in the same order:
`{"type": "tool_result", "tool_use_id": <id>, "content": <result string>}`, plus `"is_error": true` when the
tool failed.

## Responses
A 200 response has `content-type: text/event-stream` and a body in chunked transfer encoding (RFC 9112; chunk
sizes in hex, either letter case; the connection closes after the last chunk). Chunk boundaries fall
anywhere — inside lines, between CR and LF, inside multi-byte UTF-8 characters. The body is a Server-Sent
Events stream: lines end in LF or CRLF; a line starting with `:` is a comment; `event: <name>` names the
event and `data: <text>` adds a data line (the single space after the colon is optional; if an event has
several `data:` lines, join them with LF); a blank line ends the event. Every event's data is one JSON
object; its strings may use any JSON escape, including `\\uXXXX` with surrogate pairs. Events, in order:
- `message_start` — `message.usage.input_tokens` and `message.usage.cache_read_input_tokens` count toward the
  totals (ignore its `output_tokens`).
- `content_block_start` with `index` and `content_block`: `{"type":"thinking","thinking":"","signature":""}`,
  `{"type":"text","text":""}` or `{"type":"tool_use","id":…,"name":…,"input":{}}` (that `input` is a
  placeholder — ignore it).
- `content_block_delta` with `index` and `delta`, one of: `{"type":"thinking_delta","thinking":…}`,
  `{"type":"signature_delta","signature":…}`, `{"type":"text_delta","text":…}`,
  `{"type":"input_json_delta","partial_json":…}`. Concatenate each kind per block. A tool_use block's input is
  the JSON text formed by concatenating all its `partial_json` strings — the pieces split JSON tokens and
  escape sequences anywhere, so parse only the complete text.
- `content_block_stop` with `index`.
- `message_delta` — `delta.stop_reason` (`tool_use` or `end_turn`) and `usage.output_tokens`, which counts
  toward the totals.
- `message_stop` — the end of the reply.
- `ping` events and events of any other type: ignore them.

A non-200 response has a `Content-Length` and a JSON body `{"type":"error","error":{"type":…,"message":…}}`. Then print the lines
produced so far, then `ERROR <status> <error.type>: <error.message>`, and exit with status 0. (The server
validates both requests strictly.)

## Tools
`tools` in request.json declares two tools; tool_use inputs are always JSON objects with string values.
- `measure` `{"text": …, "unit": …}`: the length of `text` as a decimal integer string, counted in `unit`:
  `code_points` (Unicode scalar values), `utf8_bytes` (bytes of its UTF-8 encoding) or `utf16_units` (UTF-16
  code units: 2 for characters above U+FFFF, else 1). Any other unit fails with the result
  `unsupported unit: <unit>`.
- `reverse` `{"text": …}`: the text with its Unicode code points in reverse order.
- Any other tool name fails with the result `unknown tool: <name>`.

## Output
Strings are printed escaped: `\\` as `\\\\`, LF as `\\n`, CR as `\\r`, TAB as `\\t`, everything else as is (UTF-8).

Print `request 1`, then one line per content block of the first reply in index order — for a thinking block
`thinking <thinking text>` and then `signature <signature>`; for a text block `text <text>`; for a tool_use
block `tool_use <id> <name> <input JSON>`, where `<input JSON>` is the concatenated `partial_json` text exactly
as received (escaped as above) — then `stop_reason <stop_reason>`. Next, one line per tool_use block in block
order: `result <tool_use_id> <result>` for a successful tool or `error <tool_use_id> <result>` for a failed
one. Then `request 2`, its block lines and its `stop_reason` line the same way, and finally
`usage input=<I> output=<O> cache_read=<C>` with the sums over both replies described above.

Example (one reply's data shortened; real ids and texts differ per test):
```
request 1
thinking Two tools are needed.\\nFirst measure.
signature EqQBCkYIBhgCKkD+abc/xyz09==
text I'll use the tools.
tool_use toolu_01AbC measure {"text": "h\\\\u00e9llo 😀", "unit": "utf16_units"}
tool_use toolu_01XyZ reverse {"text":"ab\\\\\\\\c\\\\n"}
tool_use toolu_01Qrs measure {"unit":"words","text":"x"}
stop_reason tool_use
result toolu_01AbC 8
result toolu_01XyZ \\nc\\\\ba
error toolu_01Qrs unsupported unit: words
request 2
text "héllo 😀" is 8 UTF-16 code units long.
stop_reason end_turn
usage input=2400 output=310 cache_read=512
```
"""

TOOLS = [
    {"name": "measure",
     "description": "Length of a text in the given unit: code_points, utf8_bytes or utf16_units.",
     "input_schema": {"type": "object",
                      "properties": {"text": {"type": "string"},
                                     "unit": {"type": "string", "description": "code_points | utf8_bytes | "
                                                                               "utf16_units"}},
                      "required": ["text", "unit"]}},
    {"name": "reverse",
     "description": "Reverse a text by Unicode code point.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
PIECES = ["naïve", "café", "e\u0301", "😀", "🎉", "𝄞", "日本語", "\"quoted\"", "back\\slash", "a/b", "tab\there",
          "line\nbreak", "Ω", "ß", "👍🏽", "\u200d", "x", "→", "€5", "  "]


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def tool_run(name, inp):
    if name == "measure":
        t, u = inp["text"], inp["unit"]
        if u == "code_points":
            return str(len(t)), False
        if u == "utf8_bytes":
            return str(len(t.encode("utf-8"))), False
        if u == "utf16_units":
            return str(len(t.encode("utf-16-le")) // 2), False
        return f"unsupported unit: {u}", True
    if name == "reverse":
        return inp["text"][::-1], False
    return f"unknown tool: {name}", True


def json_str_mixed(s: str, rng) -> str:
    """A JSON string literal with a random mix of raw and escaped characters."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n" if rng.random() < .7 else "\\u000a")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "/" and rng.random() < .5:
            out.append("\\/")
        elif o > 0xFFFF and rng.random() < .7:
            v = o - 0x10000
            hi, lo = 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF)
            f = "\\u%04x\\u%04x" if rng.random() < .5 else "\\u%04X\\u%04X"
            out.append(f % (hi, lo))
        elif 0x7F < o <= 0xFFFF and rng.random() < .4:
            out.append("\\u%04x" % o)
        elif ch == "e" and rng.random() < .1:
            out.append("\\u0065")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def input_json(inp: dict, rng) -> str:
    items = list(inp.items())
    if rng.random() < .5:
        items.reverse()
    sep, colon = rng.choice([(",", ":"), (", ", ": "), (" ,\n ", " : ")])
    return "{" + sep.join(json_str_mixed(k, rng) + colon + json_str_mixed(v, rng) for k, v in items) + "}"


def split_cp(s: str, rng, lo=1, hi=12):
    """Split a string into non-empty pieces at code point boundaries."""
    out, i = [], 0
    while i < len(s):
        n = rng.randint(lo, hi)
        out.append(s[i:i + n])
        i += n
    return out


def make_text(rng, n):
    return "".join(rng.choice(PIECES) + rng.choice([" ", "", "-"]) for _ in range(n)).strip()


def make_data(seed: int) -> dict:
    rng = random.Random(63000 + seed)
    t1 = make_text(rng, 7) + " 😀"
    t2 = "𝄞 " + make_text(rng, 6) + " e\u0301\n"
    ids = ["toolu_01" + "".join(rng.choice(ALNUM) for _ in range(22)) for _ in range(4)]
    tools = [(ids[0], "measure", {"text": t1, "unit": "utf16_units"}),
             (ids[1], "reverse", {"text": t2}),
             (ids[2], "measure", {"unit": "utf8_bytes", "text": t2}),
             (ids[3], "measure", {"text": t1, "unit": "graphemes"})]
    thinking = (f"The user wants four things about two strings.\nFirst string has emoji like 😀 — UTF-16 needs "
                f"surrogate pairs, so count carefully.\nSecond: reverse by code point (not grapheme) → "
                f"“{t2[:6]}…”.\n\tAlso try graphemes; the tool may reject that unit. Seed #{seed}.")
    sig = base64.b64encode(bytes(rng.randrange(256) for _ in range(rng.choice([241, 244, 250])))).decode()
    text1 = f"I'll measure and reverse those strings now — four tool calls 🔧 (seed {seed})."
    prompt = (f"Text A is {json.dumps(t1, ensure_ascii=False)} and text B is {json.dumps(t2, ensure_ascii=False)}."
              f" How long is A in UTF-16 units and in graphemes, what is B reversed, and how many UTF-8 bytes"
              f" is B?")
    system = "You are a precise text assistant. Always use the tools for measuring and reversing."
    req = {"api_key": f"sk-ant-api03-cb2{seed}-" + "".join(rng.choice(ALNUM) for _ in range(32)),
           "model": MODEL, "max_tokens": 8000 + 64 * seed, "thinking_budget": 2048 + seed, "system": system,
           "prompt": prompt, "tools": TOOLS}
    inputs_json = [input_json(inp, rng) for _, _, inp in tools]
    results = [tool_run(n, inp) for _, n, inp in tools]
    usage = [dict(inp=rng.randint(900, 1500), cache=rng.randint(0, 600), out=rng.randint(180, 400)),
             dict(inp=rng.randint(1600, 2500), cache=rng.randint(0, 600), out=rng.randint(40, 120))]
    return dict(seed=seed, req=req, thinking=thinking, sig=sig, text1=text1, tools=tools, ids=ids,
                inputs_json=inputs_json, results=results, usage=usage,
                msg_ids=["msg_01" + "".join(rng.choice(ALNUM) for _ in range(22)) for _ in range(2)])


def final_text(d, got_results):
    """The second reply quotes the tool results it received (so wrong results change the output)."""
    r = got_results
    return (f"Text A is {r[0]} UTF-16 code units long. Text B reversed is “{r[1]}”, and B takes {r[2]} bytes in "
            f"UTF-8. Grapheme counting failed: {r[3]} 🙂")


def assistant_content(d):
    blocks = [{"type": "thinking", "thinking": d["thinking"], "signature": d["sig"]},
              {"type": "text", "text": d["text1"]}]
    blocks += [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in d["tools"]]
    return blocks


# ── SSE generation ───────────────────────────────────────────────────────────
def sse_bytes(events, rng) -> bytes:
    """events: list of (name, obj). Varied but valid SSE framing; two delta events get two data lines."""
    out = []
    deltas = [k for k, (name, _) in enumerate(events) if name == "content_block_delta"]
    split_at = set(rng.sample(deltas, 2))
    for k, (name, obj) in enumerate(events):
        eol = "\r\n" if rng.random() < .25 else "\n"
        if rng.random() < .08:
            out.append(": keep-alive" + eol)
        if name == "ping":
            data = '{"type": "ping"}'
        else:
            data = json.dumps(obj, ensure_ascii=rng.random() < .4, separators=(",", ":"))
        ev = ("event:" if rng.random() < .1 else "event: ") + name
        dp = "data:" if rng.random() < .15 else "data: "
        if k in split_at:
            head, tail = data.split(",", 1)          # the first comma is between top-level members
            lines = [ev, dp + head + ",", "data: " + tail]
        else:
            lines = [ev, dp + data]
        out.append(eol.join(lines) + eol + eol)
    return "".join(out).encode("utf-8")


def stream1_events(d, rng):
    u = d["usage"][0]
    ev = [("message_start", {"type": "message_start", "message": {
        "id": d["msg_ids"][0], "type": "message", "role": "assistant", "model": MODEL, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": u["inp"], "cache_creation_input_tokens": 0, "cache_read_input_tokens": u["cache"],
                  "output_tokens": 4}}}),
          ("ping", None)]
    ev.append(("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
    for p in split_cp(d["thinking"], rng, 1, 14):
        ev.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "thinking_delta", "thinking": p}}))
    ev.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "signature_delta", "signature": d["sig"]}}))
    ev.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    ev.append(("content_block_start", {"type": "content_block_start", "index": 1,
                                       "content_block": {"type": "text", "text": ""}}))
    for j, p in enumerate(split_cp(d["text1"], rng, 1, 10)):
        ev.append(("content_block_delta", {"type": "content_block_delta", "index": 1,
                                           "delta": {"type": "text_delta", "text": p}}))
        if j == 2:
            ev.append(("ping", None))
            ev.append(("message_annotation", {"type": "message_annotation", "index": 1, "note": "ignore me"}))
    ev.append(("content_block_stop", {"type": "content_block_stop", "index": 1}))
    for k, (tid, name, _inp) in enumerate(d["tools"]):
        idx = 2 + k
        ev.append(("content_block_start", {"type": "content_block_start", "index": idx,
                                           "content_block": {"type": "tool_use", "id": tid, "name": name,
                                                             "input": {}}}))
        frags = [""] + split_cp(d["inputs_json"][k], rng, 1, 9)
        for f in frags:
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": idx,
                                               "delta": {"type": "input_json_delta", "partial_json": f}}))
        ev.append(("content_block_stop", {"type": "content_block_stop", "index": idx}))
    ev.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                                 "usage": {"output_tokens": u["out"]}}))
    ev.append(("message_stop", {"type": "message_stop"}))
    return ev


def stream2_events(d, text, rng):
    u = d["usage"][1]
    ev = [("message_start", {"type": "message_start", "message": {
        "id": d["msg_ids"][1], "type": "message", "role": "assistant", "model": MODEL, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": u["inp"], "cache_creation_input_tokens": 0, "cache_read_input_tokens": u["cache"],
                  "output_tokens": 2}}}),
          ("content_block_start", {"type": "content_block_start", "index": 0,
                                   "content_block": {"type": "text", "text": ""}}),
          ("ping", None)]
    for p in split_cp(text, rng, 1, 8):
        ev.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": p}}))
    ev.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    ev.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                 "usage": {"output_tokens": u["out"]}}))
    ev.append(("message_stop", {"type": "message_stop"}))
    return ev


def stream1_bytes(d):
    rng = random.Random(f"s1/{d['seed']}")
    return sse_bytes(stream1_events(d, rng), rng)


def stream2_bytes(d, results):
    rng = random.Random(f"s2/{d['seed']}")
    return sse_bytes(stream2_events(d, final_text(d, results), rng), rng)


def expected_output(d) -> str:
    out = ["request 1", f"thinking {esc(d['thinking'])}", f"signature {d['sig']}", f"text {esc(d['text1'])}"]
    for (tid, name, _), js in zip(d["tools"], d["inputs_json"]):
        out.append(f"tool_use {tid} {name} {esc(js)}")
    out.append("stop_reason tool_use")
    for (tid, _, _), (res, is_err) in zip(d["tools"], d["results"]):
        out.append(f"{'error' if is_err else 'result'} {tid} {esc(res)}")
    out += ["request 2", f"text {esc(final_text(d, [r for r, _ in d['results']]))}", "stop_reason end_turn"]
    u = d["usage"]
    out.append(f"usage input={u[0]['inp'] + u[1]['inp']} output={u[0]['out'] + u[1]['out']} "
               f"cache_read={u[0]['cache'] + u[1]['cache']}")
    return "\n".join(out)


# ── request validation ───────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, status, etype, message):
        super().__init__(message)
        self.status, self.etype, self.message = status, etype, message


def bad(msg):
    return ApiError(400, "invalid_request_error", msg)


def exact_keys(obj, keys, path):
    if not isinstance(obj, dict):
        raise bad(f"{path}: Input should be an object")
    for k in obj:
        if k not in keys:
            raise bad(f"{path}.{k}: Extra inputs are not permitted")
    for k in keys:
        if k not in obj:
            raise bad(f"{path}.{k}: Field required")


def validate(d, body):
    """Returns the list of tool result strings for request 2, or None for request 1."""
    r = d["req"]
    exact_keys(body, ["model", "max_tokens", "stream", "thinking", "system", "tools", "messages"], "body")
    if body["model"] != r["model"]:
        raise ApiError(404, "not_found_error", f"model: {body['model']}")
    if body["max_tokens"] != r["max_tokens"]:
        raise bad(f"max_tokens: expected {r['max_tokens']} (from request.json)")
    if body["stream"] is not True:
        raise bad("stream: this endpoint only serves streaming requests; set \"stream\": true")
    if body["thinking"] != {"type": "enabled", "budget_tokens": r["thinking_budget"]}:
        raise bad(f"thinking: expected {{\"type\": \"enabled\", \"budget_tokens\": {r['thinking_budget']}}}")
    if body["system"] != r["system"]:
        raise bad("system: must be the system string from request.json")
    if body["tools"] != r["tools"]:
        raise bad("tools: must be the tools array from request.json, unchanged")
    msgs = body["messages"]
    if not isinstance(msgs, list) or not msgs:
        raise bad("messages: Input should be a non-empty list")
    if msgs[0] != {"role": "user", "content": r["prompt"]}:
        raise bad("messages.0: expected {\"role\": \"user\", \"content\": <prompt from request.json>}")
    if len(msgs) == 1:
        return None
    if len(msgs) != 3:
        raise bad(f"messages: expected 1 or 3 messages for this conversation, got {len(msgs)}")
    a = msgs[1]
    exact_keys(a, ["role", "content"], "messages.1")
    if a["role"] != "assistant":
        raise bad("messages.1.role: expected \"assistant\"")
    want = assistant_content(d)
    blocks = a["content"]
    if not isinstance(blocks, list):
        raise bad("messages.1.content: Input should be a list of content blocks")
    if not blocks or not isinstance(blocks[0], dict) or blocks[0].get("type") not in ("thinking", "redacted_thinking"):
        found = blocks[0].get("type") if blocks and isinstance(blocks[0], dict) else None
        raise bad(f"messages.1.content.0.type: Expected `thinking` or `redacted_thinking`, but found `{found}`. When "
                  f"`thinking` is enabled, a final `assistant` message must start with a thinking block (preceeding "
                  f"the lastmost set of `tool_use` and `tool_result` blocks).")
    if len(blocks) != len(want):
        raise bad(f"messages.1.content: expected the {len(want)} content blocks of the previous reply, got "
                  f"{len(blocks)}")
    for i, (g, w) in enumerate(zip(blocks, want)):
        p = f"messages.1.content.{i}"
        if not isinstance(g, dict) or g.get("type") != w["type"]:
            raise bad(f"{p}.type: expected `{w['type']}` (blocks in the order they were streamed)")
        exact_keys(g, list(w), p)
        if w["type"] == "thinking":
            if g["thinking"] != w["thinking"] or g["signature"] != w["signature"]:
                raise bad(f"{p}: Invalid `signature` in `thinking` block")
        elif w["type"] == "text":
            if g["text"] != w["text"]:
                raise bad(f"{p}.text: does not match the text that was streamed: got "
                          f"{json.dumps(g['text'], ensure_ascii=False)[:120]}")
        else:
            if g["id"] != w["id"] or g["name"] != w["name"]:
                raise bad(f"{p}: tool_use id/name must match the streamed block ({w['id']} {w['name']})")
            if g["input"] != w["input"]:
                raise bad(f"{p}.input: does not match the input that was streamed: got "
                          f"{json.dumps(g['input'], ensure_ascii=False)[:160]}")
    u = msgs[2]
    exact_keys(u, ["role", "content"], "messages.2")
    if u["role"] != "user":
        raise bad("messages.2.role: expected \"user\"")
    blocks = u["content"]
    ids = [t[0] for t in d["tools"]]
    if not isinstance(blocks, list) or len(blocks) != len(ids) or \
            any(not isinstance(b, dict) or b.get("type") != "tool_result" for b in blocks):
        raise bad(f"messages.2: `tool_use` ids were found without `tool_result` blocks immediately after: "
                  f"{', '.join(ids)}. Each `tool_use` block must have a corresponding `tool_result` block in the "
                  f"next message.")
    got = []
    for i, (b, tid, (_res, is_err)) in enumerate(zip(blocks, ids, d["results"])):
        p = f"messages.2.content.{i}"
        exact_keys(b, ["type", "tool_use_id", "content"] + (["is_error"] if "is_error" in b else []), p)
        if b["tool_use_id"] != tid:
            raise bad(f"{p}.tool_use_id: expected {tid} (tool_result blocks in tool_use order)")
        if not isinstance(b["content"], str):
            raise bad(f"{p}.content: expected a string")
        if is_err and b.get("is_error") is not True:
            raise bad(f"{p}.is_error: this tool call failed; send \"is_error\": true")
        if not is_err and b.get("is_error", False) is not False:
            raise bad(f"{p}.is_error: this tool call succeeded; omit is_error (or send false)")
        got.append(b["content"])
    return got


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):   # client went away: ignore
            super().handle_error(request, client_address)


class Challenge(H.Challenge):
    name = "stream_anthropic"
    title = "Anthropic Messages SSE client: thinking + signature, input_json_delta, tool_result round trip"
    category = "tools"
    timeout = 20

    def __init__(self):
        self._data, self._lock, self.server = {}, threading.Lock(), None

    def data(self, seed):
        with self._lock:
            if seed not in self._data:
                self._data[seed] = make_data(seed)
            return self._data[seed]

    def start(self, ctx):
        ch = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def log_message(self, *a):
                pass

            def error(self, e: ApiError):
                body = json.dumps({"type": "error", "error": {"type": e.etype, "message": e.message}},
                                  ensure_ascii=False).encode()
                self.send_response(e.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def stream(self, data: bytes, seed: int, n: int):
                rng = random.Random(f"chunks/{seed}/{n}")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("request-id", f"req_cb2{seed}{n}")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                i = 0
                k = 0
                while i < len(data):
                    m = rng.randint(1, 23)
                    part = data[i:i + m]
                    size = f"{len(part):x}" if rng.random() < .6 else f"{len(part):X}"
                    self.wfile.write(size.encode() + b"\r\n" + part + b"\r\n")
                    i += m
                    k += 1
                    if k % 6 == 0:
                        time.sleep(0.001)
                self.wfile.write(b"0\r\n\r\n")
                self.close_connection = True

            def read_body(self):
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    data = b""
                    while True:
                        n = int(self.rfile.readline().split(b";")[0].strip(), 16)
                        if n == 0:
                            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                                pass
                            return data
                        data += self.rfile.read(n)
                        self.rfile.readline()
                n = self.headers.get("Content-Length")
                return None if n is None else self.rfile.read(int(n))

            def do_GET(self):
                self.error(ApiError(404, "not_found_error", f"Not found: GET {self.path}"))

            def do_POST(self):
                try:
                    parts = self.path.split("?")[0].split("/")
                    if len(parts) != 5 or parts[1] != "c" or not parts[2].isdigit() or parts[3:] != ["v1", "messages"]:
                        raise ApiError(404, "not_found_error", f"Not found: POST {self.path}")
                    d = ch.data(int(parts[2]))
                    body = self.read_body()
                    if body is None:
                        raise ApiError(411, "invalid_request_error", "Content-Length required")
                    key = self.headers.get("x-api-key")
                    if key is None:
                        raise ApiError(401, "authentication_error", "x-api-key header is required")
                    if key != d["req"]["api_key"]:
                        raise ApiError(401, "authentication_error", "invalid x-api-key")
                    ver = self.headers.get("anthropic-version")
                    if ver is None:
                        raise bad("anthropic-version: header is required")
                    if ver != VERSION:
                        raise bad(f"anthropic-version: invalid version {ver!r}; this server supports {VERSION}")
                    ctype = self.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype != "application/json":
                        raise bad("content-type: must be application/json")
                    try:
                        req = json.loads(body.decode("utf-8"))
                    except Exception:
                        raise bad("The request body is not valid JSON.")
                    got = validate(d, req)
                    if got is None:
                        self.stream(stream1_bytes(d), d["seed"], 1)
                    else:
                        self.stream(stream2_bytes(d, got), d["seed"], 2)
                except ApiError as e:
                    self.error(e)

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        d = self.data(seed)
        dd = ctx.data_dir(self, seed)
        (dd / "request.json").write_text(json.dumps(d["req"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return H.Case(args=[str(dd), f"http://127.0.0.1:{self.port}/c/{seed}"], expected=expected_output(d))
