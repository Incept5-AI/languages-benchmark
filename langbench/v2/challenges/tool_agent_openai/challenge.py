"""tool_agent_openai — an OpenAI Chat Completions tool-calling loop against a strict scripted mock.

Hard parts: parallel tool calls; `arguments` is a JSON *string* (with \\u escapes and odd whitespace) that must
be parsed for execution but echoed back byte-for-byte in the assistant message; every tool message needs the
matching `tool_call_id`, string `content`, in order; tool results must be serialised exactly as specified
(compact JSON, fixed key order, raw UTF-8, \\n escape); RFC 4180 CSV with BOM, CRLF, quoted commas, doubled
quotes and an embedded newline; currency conversion with integer HALF_UP rounding including an exact tie; the
unknown-tool error contract; summing usage. The mock derives everything from the request (stateless), and
its later turns depend on the tool results it receives, so wrong results change the final answer.
"""
import json
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

MODEL = "gpt-4o-2024-08-06"

PROMPT = """\
Write a {LANG} program that runs an OpenAI Chat Completions tool-calling loop: it sends a conversation to a
chat-completions server, executes the tools the model calls, sends the results back, and repeats until the
model gives a final answer.

The program is run with two arguments: `<data_dir> <base_url>`. `<base_url>` looks like
`http://127.0.0.1:50123/c/0/v1` (no trailing slash); the endpoint is `POST <base_url>/chat/completions`.

## Input files in `<data_dir>`
- `task.json`: `{"api_key": "...", "model": "...", "system": "...", "user": "...", "tools": [...]}` — `tools`
  is the exact array to send in every request (tool definitions in the Chat Completions format).
- `inventory.csv`: RFC 4180 CSV, UTF-8, possibly starting with a UTF-8 byte-order mark (ignore it). Records end
  with CRLF. The first record is the header `sku,name,price_cents,currency,stock`. Any field may be enclosed
  in double quotes; a quoted field may contain commas, CR/LF line breaks (part of the value) and `""` (a
  literal `"`). Fields are never trimmed. `price_cents` and `stock` are non-negative integers. Every sku occurs
  at most once.
- `rates.json`: `{"scale": 1000000, "rates": {"USD": 1000000, "EUR": 921350, ...}}` — `rates[C]` is how many
  millionths of a unit of currency C equal one US dollar (all currencies have 2 decimals; amounts are in cents).

## The requests
Every request: `POST <base_url>/chat/completions` over HTTP/1.1 with headers `Authorization: Bearer <api_key>`
and `Content-Type: application/json`, and a JSON body with exactly these keys:
- `"model"`: from task.json;
- `"messages"`: the conversation so far (below);
- `"tools"`: the `tools` array from task.json, unchanged;
- `"tool_choice": "auto"`.
Any other key is rejected (400). Send HTTP/1.1 requests with a `Host` header and a `Content-Length`.
Every response has a `Content-Length` header and `Connection: close`; its body is UTF-8 JSON, possibly
pretty-printed over several lines, and strings in it may use any JSON escape, including `\\uXXXX` with
surrogate pairs for characters outside the BMP.

The conversation starts as `[{"role": "system", "content": <system>}, {"role": "user", "content": <user>}]`.

A 200 response is a chat completion object; use `choices[0].message` and `choices[0].finish_reason`. When
`finish_reason` is `"tool_calls"`, the message has `"role": "assistant"`, a `content` that is a string or
`null`, and a `tool_calls` array of `{"id": ..., "type": "function", "function": {"name": ..., "arguments":
...}}`, where `arguments` is a string containing JSON. Then:
1. Append that assistant message to the conversation exactly as received: `role`, `content` (keep `null` as
   `null`) and `tool_calls` with every field and every `arguments` string unchanged, character for character
   (do not re-serialise the parsed arguments). You may keep or drop its `refusal` and `annotations` fields.
2. Execute every tool call, in order, and append one message per call, in the same order:
   `{"role": "tool", "tool_call_id": <the call's id>, "content": <result string>}`.
3. Send the next request with the whole conversation.

When `finish_reason` is `"stop"`, the message `content` is the final answer and the loop ends. The server
validates every request strictly; anything wrong gets HTTP 4xx with a body
`{"error": {"message": "...", "type": "...", "param": ..., "code": ...}}`.

## Tools
Parse each call's `arguments` as JSON (it may contain `\\uXXXX` escapes and arbitrary whitespace); the
arguments are always a valid object for the tool, and currency codes are always present in rates.json. Each result is a JSON object written in compact form (no
whitespace), with keys in exactly the order shown; strings escape `"` as `\\"`, `\\` as `\\\\`, the control
characters U+0008, U+000C, U+000A, U+000D, U+0009 as `\\b \\f \\n \\r \\t`, other characters below U+0020 as
`\\u00xx` (lowercase hex), and nothing else (non-ASCII is written as raw UTF-8, `/` is not escaped).
- `inventory_lookup` `{"sku": string}`: find the inventory row whose sku equals the argument exactly
  (case-sensitive). Result `{"sku":<sku>,"name":<name>,"price_cents":<int>,"currency":<currency>,"stock":<int>}`
  (integers as plain numbers), or `{"error":"unknown sku: <sku>"}` if there is no such row.
- `convert_currency` `{"amount_cents": integer, "from": string, "to": string}`: result
  `{"amount_cents":<converted>,"currency":<to>}` where `<converted>` = amount_cents × rates[to] ÷ rates[from],
  rounded to the nearest integer with exact halves rounded up (HALF_UP; amounts are never negative). Compute
  it exactly with integers, e.g. `(2 × amount × rates[to] + rates[from]) div (2 × rates[from])`.
- Any other tool name: result `{"error":"unknown tool: <name>"}`.

## Output
For every response with `finish_reason` `"tool_calls"`: if its `content` is a string, print `SAY <content>`;
then one line `CALL <id> <name>` per tool call, in order; then one line `RESULT <id> <result string>` per tool
call, in order. For the final response print `FINAL <content>`. Then print
`USAGE prompt=<P> completion=<C> total=<T> requests=<N>`: the sums of `usage.prompt_tokens`,
`usage.completion_tokens` and `usage.total_tokens` over all responses, and the number of requests sent. All
`content` strings in this task are single lines.

If any response is not HTTP 200, print the lines produced so far, then `ERROR <status> <error.message>`, and
exit with status 0.

Example output (ids, numbers and text differ per test):
```
SAY I'll look these up.
CALL call_Ab12 inventory_lookup
CALL call_Cd34 inventory_lookup
RESULT call_Ab12 {"sku":"AB-1","name":"Lamp, \\"Arc\\"","price_cents":1299,"currency":"EUR","stock":4}
RESULT call_Cd34 {"error":"unknown sku: XX-9"}
CALL call_Ef56 convert_currency
CALL call_Gh78 get_weather
RESULT call_Ef56 {"amount_cents":1410,"currency":"USD"}
RESULT call_Gh78 {"error":"unknown tool: get_weather"}
FINAL The lamp costs 14.10 USD.
USAGE prompt=1290 completion=211 total=1501 requests=3
```
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "inventory_lookup",
        "description": "Look up one product in the inventory by its exact SKU.",
        "parameters": {"type": "object", "properties": {"sku": {"type": "string", "description": "Exact SKU"}},
                       "required": ["sku"], "additionalProperties": False},
        "strict": True}},
    {"type": "function", "function": {
        "name": "convert_currency",
        "description": "Convert an amount in cents from one currency to another (HALF_UP to whole cents).",
        "parameters": {"type": "object",
                       "properties": {"amount_cents": {"type": "integer"},
                                      "from": {"type": "string", "description": "ISO 4217 code"},
                                      "to": {"type": "string", "description": "ISO 4217 code"}},
                       "required": ["amount_cents", "from", "to"], "additionalProperties": False},
        "strict": True}},
]

PRODUCT_WORDS = ["Lamp", "Kettle", "Chair", "Desk", "Mug", "Cable", "Router", "Pen", "Notebook", "Speaker"]
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def res_json(obj) -> str:
    """The exact result serialisation the prompt specifies (== compact json.dumps, ensure_ascii=False)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def half_up(amount, rf, rt):
    return (2 * amount * rt + rf) // (2 * rf)


def csv_field(s: str, rng, force=False) -> str:
    if force or any(c in s for c in ',"\r\n') or rng.random() < 0.25:
        return '"' + s.replace('"', '""') + '"'
    return s


def make_data(seed: int) -> dict:
    rng = random.Random(52000 + seed)
    cur_a, cur_b = rng.sample(["EUR", "GBP", "CHF", "SEK"], 2)
    target = rng.choice(["USD", "CAD"])
    # find rates and a price for item A such that A's conversion is an exact .5 tie
    while True:
        rates = {"USD": 1000000, "EUR": rng.randrange(880000, 960000, 2), "GBP": rng.randrange(760000, 820000, 2),
                 "CHF": rng.randrange(860000, 920000, 2), "SEK": rng.randrange(9800000, 11000000, 2),
                 "CAD": rng.randrange(1300000, 1400000, 2), "JPY": rng.randrange(140000000, 155000000, 2)}
        qa, qb = rng.randint(2, 5), rng.randint(2, 4)
        # an exact .5 whose floor is even, so round-half-even (e.g. Python round()) gets it wrong
        ties = [p for p in range(800, 30000) if (2 * p * qa * rates[target]) % (2 * rates[cur_a]) == rates[cur_a]
                and (p * qa * rates[target] // rates[cur_a]) % 2 == 0]
        if ties:
            price_a = rng.choice(ties)
            break
    price_b = rng.randint(500, 20000)
    while (2 * price_b * qb * rates[target]) % (2 * rates[cur_b]) == rates[cur_b]:
        price_b += 1
    sku_a = f"{rng.choice('ABCDEFGH')}{rng.choice('KLMNPR')}-{rng.randint(1000, 9999)}"
    sku_b = f"{rng.choice('QRSTUV')}-{rng.randint(10, 99)}"
    sku_u = f"ZZ-{rng.randint(100, 999)}"
    word_a, word_b = rng.sample(PRODUCT_WORDS, 2)
    name_a = f'{word_a}, "Pro" {rng.randint(10, 40)}″ — café edition'
    name_b = f"{word_b}\r\nMk {rng.choice(['II', 'III', 'IV'])}\\{rng.randint(1, 9)}"
    rows = [(sku_a, name_a, price_a, cur_a, rng.randint(0, 50)),
            (sku_b, name_b, price_b, cur_b, rng.randint(0, 50)),
            (sku_u.lower(), "Decoy (lowercase sku)", 100, "USD", 1)]
    for w in rng.sample(PRODUCT_WORDS, 6):
        rows.append((f"{rng.choice('MNOP')}{rng.randint(10, 99)}-{rng.randint(1, 9)}",
                     rng.choice([w, f"{w}, large", f'{w} "mini"', f"{w} – ünï"]),
                     rng.randint(100, 50000), rng.choice(["USD", "EUR", "GBP"]), rng.randint(0, 99)))
    rng.shuffle(rows)
    lines = ["sku,name,price_cents,currency,stock"]
    for r in rows:
        lines.append(",".join([csv_field(r[0], rng), csv_field(r[1], rng), csv_field(str(r[2]), rng),
                               csv_field(r[3], rng), csv_field(str(r[4]), rng)]))
    csv_bytes = ("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-8")
    inventory = {r[0]: r for r in rows}

    ids = ["call_" + "".join(rng.choice(ALNUM) for _ in range(24)) for _ in range(6)]
    esc = "\\u%04x" % ord(sku_a[0]) if rng.random() < 0.5 else "\\u%04X" % ord(sku_a[0])
    args1 = ['{"sku": "' + esc + sku_a[1:] + '"}',
             '{\n  "sku" : "' + sku_b + '"\n}',
             '{"sku":"' + sku_u + '"}']
    user = (f"How much are {qa} × {sku_a} and {qb} × {sku_b} in {target}, all together? "
            f"Also check {sku_u}, and get me a shipping quote to Zürich.")
    system = "You are a careful shopping assistant. Use the tools; never guess prices.\nAnswer in one line."
    task = {"api_key": f"sk-test-{seed}-" + "".join(rng.choice(ALNUM) for _ in range(20)), "model": MODEL,
            "system": system, "user": user, "tools": TOOLS}
    usage = []
    for t in range(3):
        p, c = 180 + 260 * t + rng.randint(0, 90), rng.randint(15, 140)
        usage.append({"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c,
                      "prompt_tokens_details": {"cached_tokens": 128 * t, "audio_tokens": 0},
                      "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0,
                                                    "accepted_prediction_tokens": 0,
                                                    "rejected_prediction_tokens": 0}})
    return dict(seed=seed, task=task, rates=rates, csv=csv_bytes, inventory=inventory, ids=ids, args1=args1,
                skus=(sku_a, sku_b, sku_u), qty=(qa, qb), target=target, usage=usage,
                say1=rng.choice(["I'll look up all three SKUs first.", "Let me check the inventory — one moment."]))


# ── the scripted "model" (shared by the server and the oracle) ──────────────
def assistant1(d):
    return {"role": "assistant", "content": d["say1"],
            "tool_calls": [{"id": d["ids"][i], "type": "function",
                            "function": {"name": "inventory_lookup", "arguments": d["args1"][i]}}
                           for i in range(3)]}


def parse_result(content):
    try:
        v = json.loads(content)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def assistant2(d, results1):
    """Given the three lookup result strings, the turn-2 assistant message, or a final message if unusable."""
    ra, rb = parse_result(results1[0]), parse_result(results1[1])
    for r, cid in ((ra, d["ids"][0]), (rb, d["ids"][1])):
        if not r or not isinstance(r.get("price_cents"), int) or not isinstance(r.get("currency"), str):
            return {"role": "assistant", "content": f"I could not read the inventory result of {cid}.",
                    "refusal": None, "annotations": []}, "stop"
    qa, qb = d["qty"]
    t = d["target"]
    a1 = f'{{"amount_cents": {ra["price_cents"] * qa}, "from": "{ra["currency"]}", "to": "{t}"}}'
    a2 = f'{{"to":"{t}","from":"{rb["currency"]}","amount_cents":{rb["price_cents"] * qb}}}'
    a3 = '{"skus": ["' + d["skus"][0] + '", "' + d["skus"][1] + '"], "city": "Z\\u00fcrich"}'
    msg = {"role": "assistant", "content": None,
           "tool_calls": [{"id": d["ids"][3], "type": "function",
                           "function": {"name": "convert_currency", "arguments": a1}},
                          {"id": d["ids"][4], "type": "function",
                           "function": {"name": "convert_currency", "arguments": a2}},
                          {"id": d["ids"][5], "type": "function",
                           "function": {"name": "get_shipping_quote", "arguments": a3}}],
           "refusal": None, "annotations": []}
    return msg, "tool_calls"


def money(c):
    return f"{c // 100}.{c % 100:02d}" if isinstance(c, int) and c >= 0 else str(c)


def final_text(d, results1, results2):
    qa, qb = d["qty"]
    sa, sb, su = d["skus"]
    t = d["target"]
    ca, cb = parse_result(results2[0]) or {}, parse_result(results2[1]) or {}
    ru = parse_result(results1[2]) or {}
    va, vb = ca.get("amount_cents"), cb.get("amount_cents")
    tot = va + vb if isinstance(va, int) and isinstance(vb, int) else "?"
    return (f"{qa} × {sa} = {money(va)} {ca.get('currency')} and {qb} × {sb} = {money(vb)} {cb.get('currency')}, "
            f"so together {money(tot)} {t} ✅. {su}: \"{ru.get('error')}\". "
            f"Shipping to Zürich 🚚 can't be quoted (no such tool).")


def tool_result(d, name, args):
    if name == "inventory_lookup":
        r = d["inventory"].get(args["sku"])
        if r is None:
            return res_json({"error": f"unknown sku: {args['sku']}"})
        return res_json({"sku": r[0], "name": r[1], "price_cents": r[2], "currency": r[3], "stock": r[4]})
    if name == "convert_currency":
        rf, rt = d["rates"][args["from"]], d["rates"][args["to"]]
        return res_json({"amount_cents": half_up(args["amount_cents"], rf, rt), "currency": args["to"]})
    return res_json({"error": f"unknown tool: {name}"})


def expected_output(d) -> str:
    out = []
    a1 = assistant1(d)
    out.append(f"SAY {a1['content']}")
    out += [f"CALL {c['id']} {c['function']['name']}" for c in a1["tool_calls"]]
    r1 = [tool_result(d, c["function"]["name"], json.loads(c["function"]["arguments"])) for c in a1["tool_calls"]]
    out += [f"RESULT {c['id']} {r}" for c, r in zip(a1["tool_calls"], r1)]
    a2, _ = assistant2(d, r1)
    out += [f"CALL {c['id']} {c['function']['name']}" for c in a2["tool_calls"]]
    r2 = [tool_result(d, c["function"]["name"], json.loads(c["function"]["arguments"])) for c in a2["tool_calls"]]
    out += [f"RESULT {c['id']} {r}" for c, r in zip(a2["tool_calls"], r2)]
    out.append(f"FINAL {final_text(d, r1, r2)}")
    u = d["usage"]
    out.append(f"USAGE prompt={sum(x['prompt_tokens'] for x in u)} completion="
               f"{sum(x['completion_tokens'] for x in u)} total={sum(x['total_tokens'] for x in u)} requests=3")
    return "\n".join(out)


# ── mock server ──────────────────────────────────────────────────────────────
class BadRequest(Exception):
    def __init__(self, message, param=None, status=400, etype="invalid_request_error", code=None):
        super().__init__(message)
        self.message, self.param, self.status, self.etype, self.code = message, param, status, etype, code


def check_assistant(i, got, want):
    if not isinstance(got, dict) or got.get("role") != "assistant":
        raise BadRequest(f"messages[{i}]: expected the assistant message returned by the previous response "
                         f"(role 'assistant').", f"messages.[{i}].role")
    extra = set(got) - {"role", "content", "tool_calls", "refusal", "annotations"}
    if extra:
        raise BadRequest(f"messages[{i}]: unexpected field '{sorted(extra)[0]}' in assistant message.",
                         f"messages.[{i}]")
    if "content" not in got or got["content"] != want["content"]:
        raise BadRequest(f"messages[{i}].content must be echoed unchanged: expected "
                         f"{json.dumps(want['content'], ensure_ascii=False)}, got "
                         f"{json.dumps(got.get('content', '<missing>'), ensure_ascii=False)}.",
                         f"messages.[{i}].content")
    for k in ("refusal", "annotations"):
        if k in got and got[k] != want.get(k, {"refusal": None, "annotations": []}[k]):
            raise BadRequest(f"messages[{i}].{k} must be echoed unchanged.", f"messages.[{i}].{k}")
    tc = got.get("tool_calls")
    if not isinstance(tc, list) or len(tc) != len(want["tool_calls"]):
        raise BadRequest(f"messages[{i}].tool_calls must be echoed unchanged "
                         f"({len(want['tool_calls'])} tool calls).", f"messages.[{i}].tool_calls")
    for j, (g, w) in enumerate(zip(tc, want["tool_calls"])):
        if not isinstance(g, dict) or set(g) != {"id", "type", "function"} or g["id"] != w["id"] or \
                g["type"] != "function" or not isinstance(g["function"], dict) or \
                set(g["function"]) != {"name", "arguments"} or g["function"]["name"] != w["function"]["name"]:
            raise BadRequest(f"messages[{i}].tool_calls[{j}] must be echoed unchanged (id, type, function.name, "
                             f"function.arguments).", f"messages.[{i}].tool_calls.[{j}]")
        if g["function"]["arguments"] != w["function"]["arguments"]:
            raise BadRequest(f"messages[{i}].tool_calls[{j}].function.arguments must be the exact string the "
                             f"model produced: expected {json.dumps(w['function']['arguments'], ensure_ascii=False)}"
                             f", got {json.dumps(g['function']['arguments'], ensure_ascii=False)}.",
                             f"messages.[{i}].tool_calls.[{j}].function.arguments")


def check_tools(msgs, start, calls):
    """Validate the tool messages answering `calls`; returns their content strings."""
    out = []
    for j, c in enumerate(calls):
        i = start + j
        m = msgs[i] if i < len(msgs) else None
        if not isinstance(m, dict) or m.get("role") != "tool":
            missing = ", ".join(x["id"] for x in calls[j:])
            raise BadRequest("An assistant message with 'tool_calls' must be followed by tool messages responding "
                             f"to each 'tool_call_id'. The following tool_call_ids did not have response "
                             f"messages: {missing}", f"messages.[{i}].role")
        if set(m) != {"role", "tool_call_id", "content"}:
            raise BadRequest(f"messages[{i}]: a tool message has exactly the fields role, tool_call_id, content.",
                             f"messages.[{i}]")
        if m["tool_call_id"] != c["id"]:
            raise BadRequest(f"messages[{i}].tool_call_id: expected '{c['id']}' (tool messages must answer the "
                             f"tool calls in order), got {json.dumps(m['tool_call_id'])}.",
                             f"messages.[{i}].tool_call_id")
        if not isinstance(m["content"], str):
            raise BadRequest(f"messages[{i}].content: the tool result must be a string.", f"messages.[{i}].content")
        out.append(m["content"])
    return out


def respond(d, body):
    """Validate a request body (already JSON-decoded); return (status, response object)."""
    if not isinstance(body, dict):
        raise BadRequest("The request body must be a JSON object.")
    for k in body:
        if k not in ("model", "messages", "tools", "tool_choice"):
            raise BadRequest(f"Unrecognized request argument supplied: {k}", k)
    for k in ("model", "messages", "tools", "tool_choice"):
        if k not in body:
            raise BadRequest(f"Missing required parameter: '{k}'.", k)
    if body["model"] != MODEL:
        raise BadRequest(f"The model `{body['model']}` does not exist or you do not have access to it.", None, 404,
                         "invalid_request_error", "model_not_found")
    if body["tools"] != TOOLS:
        raise BadRequest("'tools' must be the tools array from task.json, unchanged.", "tools")
    if body["tool_choice"] != "auto":
        raise BadRequest("'tool_choice' must be \"auto\".", "tool_choice")
    msgs = body["messages"]
    if not isinstance(msgs, list) or len(msgs) < 2:
        raise BadRequest("'messages' must be an array starting with the system and user messages.", "messages")
    task = d["task"]
    if msgs[0] != {"role": "system", "content": task["system"]}:
        raise BadRequest("messages[0] must be {\"role\": \"system\", \"content\": <system from task.json>}.",
                         "messages.[0]")
    if msgs[1] != {"role": "user", "content": task["user"]}:
        raise BadRequest("messages[1] must be {\"role\": \"user\", \"content\": <user from task.json>}.",
                         "messages.[1]")
    a1 = assistant1(d)
    if len(msgs) == 2:
        return 0, dict(a1, refusal=None, annotations=[]), "tool_calls"
    check_assistant(2, msgs[2], dict(a1, refusal=None, annotations=[]))
    r1 = check_tools(msgs, 3, a1["tool_calls"])
    a2, fin = assistant2(d, r1)
    if len(msgs) == 6:
        return 1, a2, fin
    if fin == "stop":
        raise BadRequest(f"messages[6]: the conversation already ended.", "messages.[6]")
    check_assistant(6, msgs[6], a2)
    r2 = check_tools(msgs, 7, a2["tool_calls"])
    unk = parse_result(r2[2])
    if unk != {"error": "unknown tool: get_shipping_quote"}:
        raise BadRequest(f"messages[9].content: the result for unknown tool 'get_shipping_quote' must be "
                         f"{{\"error\":\"unknown tool: get_shipping_quote\"}}, got {json.dumps(r2[2], ensure_ascii=False)}.",
                         "messages.[9].content")
    if len(msgs) != 10:
        raise BadRequest(f"Unexpected messages after messages[9] ({len(msgs)} messages sent).", "messages")
    return 2, {"role": "assistant", "content": final_text(d, r1, r2), "refusal": None, "annotations": []}, "stop"


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):   # client went away: ignore
            super().handle_error(request, client_address)


class Challenge(H.Challenge):
    name = "tool_agent_openai"
    title = "OpenAI Chat Completions tool-calling loop (parallel calls, exact echo, tool results)"
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

            def send(self, status, obj, indent=2):
                body = json.dumps(obj, indent=indent, ensure_ascii=True).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.send_header("openai-processing-ms", "42")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def error(self, e: BadRequest):
                self.send(e.status, {"error": {"message": e.message, "type": e.etype, "param": e.param,
                                               "code": e.code}})

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
                self.error(BadRequest(f"Invalid URL (GET {self.path})", None, 404))

            def do_POST(self):
                try:
                    parts = self.path.split("?")[0].split("/")
                    if len(parts) != 6 or parts[1] != "c" or not parts[2].isdigit() or \
                            parts[3:] != ["v1", "chat", "completions"]:
                        raise BadRequest(f"Invalid URL (POST {self.path})", None, 404)
                    d = ch.data(int(parts[2]))
                    body = self.read_body()
                    if body is None:
                        raise BadRequest("Content-Length required.", None, 411)
                    auth = self.headers.get("Authorization", "")
                    if auth != "Bearer " + d["task"]["api_key"]:
                        shown = auth[7:] if auth.startswith("Bearer ") else auth
                        raise BadRequest(f"Incorrect API key provided: {shown[:12]}****. Expected the api_key from "
                                         f"task.json as 'Authorization: Bearer <api_key>'.", None, 401,
                                         "invalid_request_error", "invalid_api_key")
                    ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    if ctype != "application/json":
                        raise BadRequest("Content-Type must be application/json.")
                    try:
                        req = json.loads(body.decode("utf-8"))
                    except Exception:
                        raise BadRequest("We could not parse the JSON body of your request. (HINT: This likely "
                                         "means you aren't using your HTTP library correctly.)")
                    turn, msg, fin = respond(d, req)
                    seed = d["seed"]
                    self.send(200, {"id": f"chatcmpl-cb2{seed}x{turn}Q7vZk", "object": "chat.completion",
                                    "created": 1767225600 + 60 * seed + turn, "model": MODEL,
                                    "choices": [{"index": 0, "message": msg, "logprobs": None,
                                                 "finish_reason": fin}],
                                    "usage": d["usage"][turn], "service_tier": "default",
                                    "system_fingerprint": "fp_cb2mock01"})
                except BadRequest as e:
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
        (dd / "task.json").write_text(json.dumps(d["task"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (dd / "inventory.csv").write_bytes(d["csv"])
        (dd / "rates.json").write_text(json.dumps({"scale": 1000000, "rates": d["rates"]}, indent=1) + "\n")
        return H.Case(args=[str(dd), f"http://127.0.0.1:{self.port}/c/{seed}/v1"], expected=expected_output(d))
