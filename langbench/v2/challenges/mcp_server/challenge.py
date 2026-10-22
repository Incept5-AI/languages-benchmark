"""mcp_server — the program IS an MCP server (protocol 2025-06-18) speaking newline-delimited JSON-RPC 2.0
on stdio.

Hard parts: a JSON parser/serialiser (C, Rust, Swift-without-Codable-shortcuts), JSON-RPC edge cases (parse
error with id null, invalid requests, ids that are strings vs numbers, null/boolean/fractional ids, batches
rejected, client responses ignored, notifications never answered and without effect), the initialization
gate and version negotiation, exact tool definitions, argument validation with a pinned rule order, code
point (not UTF-16) counting and sorting, Unicode-whitespace that is NOT a word separator, and structured
tool output.

Grading: each stdout line is parsed as JSON and compared structurally, in order. Key order and whitespace
are free; `true` is not `1`. A tool result's `content[0].text` that the prompt says is JSON is compared after
parsing it.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

LATEST = "2025-06-18"
SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "cb2-kv", "title": "CB2 key-value server", "version": "1.4.2"}

TOOLS = [
    {"name": "kv_set", "title": "Set a value",
     "description": "Store a string value under a key, replacing any previous value.",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string", "minLength": 1}, "value": {"type": "string"}},
                     "required": ["key", "value"], "additionalProperties": False}},
    {"name": "kv_get", "title": "Get a value",
     "description": "Return the value stored under a key.",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string", "minLength": 1}},
                     "required": ["key"], "additionalProperties": False}},
    {"name": "kv_list", "title": "List keys",
     "description": "List the stored keys that start with a prefix, sorted by Unicode code point.",
     "inputSchema": {"type": "object", "properties": {"prefix": {"type": "string"}},
                     "additionalProperties": False},
     "outputSchema": {"type": "object",
                      "properties": {"keys": {"type": "array", "items": {"type": "string"}},
                                     "count": {"type": "integer"}},
                      "required": ["keys", "count"]}},
    {"name": "sum", "title": "Sum integers",
     "description": "Add up a non-empty list of integers.",
     "inputSchema": {"type": "object",
                     "properties": {"numbers": {"type": "array", "items": {"type": "integer"}, "minItems": 1}},
                     "required": ["numbers"], "additionalProperties": False},
     "outputSchema": {"type": "object",
                      "properties": {"sum": {"type": "integer"}, "count": {"type": "integer"}},
                      "required": ["sum", "count"]},
     "annotations": {"readOnlyHint": True, "idempotentHint": True}},
    {"name": "text_stats", "title": "Text statistics",
     "description": "Count the words, Unicode code points and UTF-8 bytes of a text.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"], "additionalProperties": False},
     "outputSchema": {"type": "object",
                      "properties": {"words": {"type": "integer"}, "codepoints": {"type": "integer"},
                                     "utf8_bytes": {"type": "integer"}},
                      "required": ["words", "codepoints", "utf8_bytes"]},
     "annotations": {"readOnlyHint": True}},
]

PROMPT_HEAD = """\
Write a {LANG} program that is a Model Context Protocol (MCP) server, protocol revision 2025-06-18, using the
stdio transport.

The program takes no arguments. It reads standard input line by line until end of input. Each line holds
one JSON-RPC 2.0 message (valid UTF-8 with no unpaired surrogate escapes; a line may end with CRLF; lines that are empty or contain only spaces, tabs
or CR are ignored). For each message that needs an answer, write exactly one JSON object on one line to
standard output, in input order. Messages that need no answer produce no output. Exit with status 0 at end
of input. (The test harness sends the whole input at once and reads all output at the end.)

Your output lines are parsed as JSON and compared structurally with the expected responses: key order,
whitespace and string-escape style are free, but every member must be exactly as specified (no extra
members, `true` is not `1`).

## Processing a line (apply the first rule that matches)

1. Not valid JSON (RFC 8259; e.g. truncated, single quotes, trailing garbage) → answer
   `{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}`.
2. Valid JSON but not an object (an array — batches are not supported in this protocol revision —, a string,
   a number, …) → answer `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Invalid Request"}}`.
3. An object with no `method` member but with a `result` or `error` member is a response from the client:
   ignore it.
4. An object is an **invalid request** if `jsonrpc` is not the string `"2.0"`, or `method` is missing or not a
   string, or it has an `id` member that is not a valid id. A **valid id** is a string (any, including `""`)
   or an integer (a JSON number without fraction; ids in the tests are within ±2^53, and a number id written with `.` or an exponent is not an integer). `null`, booleans,
   numbers with a fractional part (e.g. `1.5`), arrays and objects are not valid ids. Answer
   `{"jsonrpc":"2.0","id":<id>,"error":{"code":-32600,"message":"Invalid Request"}}`, where `<id>` is the
   message's id if it is a valid id, otherwise `null`.
5. A valid object without an `id` member is a **notification**: never answer it, and ignore it completely —
   it has no effect at all, whatever its method (e.g. `notifications/initialized`,
   `notifications/cancelled`, or even `tools/call`).
6. Otherwise it is a **request**. Every answer to a request echoes its id exactly (same type and value):
   `{"jsonrpc":"2.0","id":<id>,"result":<result>}` or `{"jsonrpc":"2.0","id":<id>,"error":{"code":<code>,"message":<message>}}`
   (no `data` member). Check, in this order:
   a. method not one of `initialize`, `ping`, `tools/list`, `tools/call` → error -32601 `"Method not found"`;
   b. `params` present but not an object → error -32602 `"Invalid params"`;
   c. the server is not yet initialized and the method is not `initialize` or `ping` → error -32002
      `"Server not initialized"`;
   d. dispatch by method as below.

## Methods

**initialize** — if the server is already initialized → error -32600 `"Already initialized"`. Else if
`params` is missing or its `protocolVersion` is missing or not a string → error -32602 `"Invalid params"`
(and the server stays uninitialized). Otherwise the server is initialized from now on (do not wait for
`notifications/initialized`) and the result is
```
{"protocolVersion":<version>,"capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"cb2-kv","title":"CB2 key-value server","version":"1.4.2"}}
```
where `<version>` is the client's `protocolVersion` if it is one of `"2025-06-18"`, `"2025-03-26"`,
`"2024-11-05"`, and `"2025-06-18"` otherwise. Any other `params` members (capabilities, clientInfo) are ignored.

**ping** — result `{}`.

**tools/list** — result `{"tools":[…]}` with exactly these five tool definitions, in this order (a `cursor`
param, if given, is ignored; there is no `nextCursor`):
```json
"""

PROMPT_TAIL = """\
```

**tools/call** — `params.name` missing or not a string → error -32602 `"Invalid params"`. A name that is not
one of the five tools → error -32602 with message `"Unknown tool: <name>"`. Otherwise the result is a tool
result object, one of:
- success: `{"content":[{"type":"text","text":<text>}],"isError":false}`
- success of a tool with an `outputSchema`:
  `{"content":[{"type":"text","text":<S as JSON text>}],"structuredContent":<S>,"isError":false}` where `<S>`
  is the structured result object; its text block is the same object serialised as JSON (any valid JSON
  formatting — the checker parses that text and compares the value).
- failure: `{"content":[{"type":"text","text":<message>}],"isError":true}` (a JSON-RPC *result*, not an error).

### Argument validation (before running a tool; report only the first failure)

`params.arguments` may be absent (same as `{}`).
1. `arguments` present but not an object (including `null`) → failure `arguments must be an object`.
2. Arguments whose name is not a property of the tool's `inputSchema` → failure `unknown argument: <name>`;
   if there are several, name the smallest by Unicode code point.
3. Then for each property in the order listed in the tool's `inputSchema.properties`:
   - missing and listed in `required` → failure `missing required argument: <name>`;
   - present with the wrong type → failure `argument <name> must be a string` (type string) or
     `argument <name> must be an array of integers` (type array, also when any element is not an integer).
     An argument whose value is `null` is present and has the wrong type. An integer is a JSON number
     without a fractional part (integral values in the tests are written without `.` or exponent; `2.5`,
     `"3"`, `true` and `null` are not integers);
   - violates `minLength` 1 or `minItems` 1 → failure `argument <name> must not be empty`.

### Tool behaviour

The server keeps one key → value map (strings) for the whole run, initially empty.
- `kv_set` — store `value` under `key`. Text: `created <key>` if the key was not present before, otherwise
  `updated <key>`.
- `kv_get` — text: the stored value exactly. If the key is not present: failure `key not found: <key>`.
- `kv_list` — `S = {"keys":[…],"count":<n>}`: all stored keys that start with `prefix` (all keys when `prefix` is
  absent or empty), sorted ascending by Unicode **code point** (not UTF-16 code unit), and their number.
- `sum` — `S = {"sum":<sum>,"count":<number of elements>}` (all values and sums fit easily in a signed 64-bit
  integer and are within ±2^53).
- `text_stats` — `S = {"words":<w>,"codepoints":<c>,"utf8_bytes":<b>}`: `<w>` is the number of maximal
  non-empty runs of characters other than space (U+0020), tab (U+0009), LF (U+000A) and CR (U+000D) — no other
  character separates words; `<c>` counts Unicode code points (a character outside the BMP counts once);
  `<b>` is the length of the text in UTF-8.

## Example

Input:
```
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":"a","method":"tools/call","params":{"name":"kv_set","arguments":{"key":"k","value":"v"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"sum","arguments":{"numbers":[1,"2"]}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"sum","arguments":{"numbers":[5,-7]}}}
{"jsonrpc":"2.0","id":4,"method":"resources/list"}
{"jsonrpc":"2.0","id":5,"method":"ping"
```
Output:
```
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"cb2-kv","title":"CB2 key-value server","version":"1.4.2"}}}
{"jsonrpc":"2.0","id":"a","result":{"content":[{"type":"text","text":"created k"}],"isError":false}}
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"argument numbers must be an array of integers"}],"isError":true}}
{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\\"sum\\":-2,\\"count\\":2}"}],"structuredContent":{"sum":-2,"count":2},"isError":false}}
{"jsonrpc":"2.0","id":4,"error":{"code":-32601,"message":"Method not found"}}
{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}
```
"""


def prompt_text():
    return PROMPT_HEAD + json.dumps(TOOLS, indent=1, ensure_ascii=False) + "\n" + PROMPT_TAIL


# ── oracle ───────────────────────────────────────────────────────────────────
class JText:
    """Expected text content that must parse as JSON equal to `value`."""

    def __init__(self, value):
        self.value = value


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def valid_id(v):
    return isinstance(v, str) or is_int(v)


def ok(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def err(id_, code, msg):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


def fail(msg):
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def text_ok(t):
    return {"content": [{"type": "text", "text": t}], "isError": False}


def struct_ok(s):
    return {"content": [{"type": "text", "text": JText(s)}], "structuredContent": s, "isError": False}


class Oracle:
    def __init__(self):
        self.inited = False
        self.kv = {}

    def line(self, raw: str):
        if raw.strip(" \t\r") == "":
            return None
        try:
            msg = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        except ValueError:
            return err(None, -32700, "Parse error")
        if not isinstance(msg, dict):
            return err(None, -32600, "Invalid Request")
        if "method" not in msg and ("result" in msg or "error" in msg):
            return None
        has_id = "id" in msg
        if msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str) or (has_id and not valid_id(msg["id"])):
            return err(msg["id"] if has_id and valid_id(msg["id"]) else None, -32600, "Invalid Request")
        if not has_id:
            return None
        id_, m = msg["id"], msg["method"]
        if m not in ("initialize", "ping", "tools/list", "tools/call"):
            return err(id_, -32601, "Method not found")
        if "params" in msg and not isinstance(msg["params"], dict):
            return err(id_, -32602, "Invalid params")
        p = msg.get("params", {})
        if not self.inited and m not in ("initialize", "ping"):
            return err(id_, -32002, "Server not initialized")
        if m == "initialize":
            if self.inited:
                return err(id_, -32600, "Already initialized")
            if "params" not in msg or not isinstance(p.get("protocolVersion"), str):
                return err(id_, -32602, "Invalid params")
            self.inited = True
            v = p["protocolVersion"] if p["protocolVersion"] in SUPPORTED else LATEST
            return ok(id_, {"protocolVersion": v, "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": dict(SERVER_INFO)})
        if m == "ping":
            return ok(id_, {})
        if m == "tools/list":
            return ok(id_, {"tools": json.loads(json.dumps(TOOLS))})
        name = p.get("name")
        if not isinstance(name, str):
            return err(id_, -32602, "Invalid params")
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            return err(id_, -32602, f"Unknown tool: {name}")
        return ok(id_, self.call(tool, p))

    def call(self, tool, p):
        if "arguments" in p and not isinstance(p["arguments"], dict):
            return fail("arguments must be an object")
        args = p.get("arguments", {})
        props = tool["inputSchema"]["properties"]
        unknown = sorted(k for k in args if k not in props)
        if unknown:
            return fail(f"unknown argument: {unknown[0]}")
        for pn, ps in props.items():
            if pn not in args:
                if pn in tool["inputSchema"].get("required", []):
                    return fail(f"missing required argument: {pn}")
                continue
            v = args[pn]
            if ps["type"] == "string":
                if not isinstance(v, str):
                    return fail(f"argument {pn} must be a string")
                if ps.get("minLength") and len(v) < ps["minLength"]:
                    return fail(f"argument {pn} must not be empty")
            else:
                if not isinstance(v, list) or not all(is_int(x) for x in v):
                    return fail(f"argument {pn} must be an array of integers")
                if ps.get("minItems") and len(v) < ps["minItems"]:
                    return fail(f"argument {pn} must not be empty")
        n = tool["name"]
        if n == "kv_set":
            t = ("updated " if args["key"] in self.kv else "created ") + args["key"]
            self.kv[args["key"]] = args["value"]
            return text_ok(t)
        if n == "kv_get":
            if args["key"] not in self.kv:
                return fail(f"key not found: {args['key']}")
            return text_ok(self.kv[args["key"]])
        if n == "kv_list":
            pre = args.get("prefix", "")
            keys = sorted(k for k in self.kv if k.startswith(pre))   # Python str order = code point order
            return struct_ok({"keys": keys, "count": len(keys)})
        if n == "sum":
            return struct_ok({"sum": sum(args["numbers"]), "count": len(args["numbers"])})
        t = args["text"]
        words = 0
        inword = False
        for ch in t:
            if ch in " \t\n\r":
                inword = False
            elif not inword:
                inword = True
                words += 1
        return struct_ok({"words": words, "codepoints": len(t), "utf8_bytes": len(t.encode("utf-8"))})


# ── input generation ─────────────────────────────────────────────────────────
def make_lines(seed):
    rng = random.Random(2025_06_18 + seed)
    ids = iter(rng.sample(range(1, 900), 200))
    L = []

    def dump(obj):
        style = rng.randrange(3)
        if style == 0:
            return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        if style == 1:
            return json.dumps(obj, ensure_ascii=True)
        return json.dumps(obj, separators=(", ", ": "), ensure_ascii=False)

    def req(method, params=None, id_=None, raw_extra=None):
        m = {"jsonrpc": "2.0", "id": next(ids) if id_ is None else id_, "method": method}
        if params is not None:
            m["params"] = params
        items = list(m.items())
        rng.shuffle(items)
        L.append(dump(dict(items)))

    def note(method, params=None):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            m["params"] = params
        L.append(dump(m))

    def call(name, args=None, id_=None, omit=False):
        p = {"name": name}
        if not omit:
            p["arguments"] = args
        req("tools/call", p, id_)

    sid = lambda: rng.choice(["req-", "Ω-", "", "7", "id \"q\" "]) + str(rng.randrange(100))  # noqa: E731
    keys = ["alpha", "Alpha", "aé", "～wave", "\U0001F600smile", "beta", "a", "ab", "été",
            "zeta/1", "zeta/2", "zeta\U0001F600", "zeta\uffe5"]
    rng.shuffle(keys)

    # before initialization
    req("ping", id_=sid())
    req("tools/list")
    L.append(rng.choice(['{"jsonrpc":"2.0","id":1,"method":"ping"', "{'jsonrpc':'2.0','id':1,'method':'ping'}",
                         '{"jsonrpc":"2.0","id":1,"method":"ping"} x']))
    req("initialize", {"capabilities": {}, "clientInfo": {"name": "cb2", "version": "1"}})
    call("kv_set", {"key": "x", "value": "y"})                 # still not initialized
    note("notifications/initialized")
    ver = ["2025-03-26", "2099-01-01", "2024-11-05"][seed % 3]   # echo an older one / fall back to latest
    req("initialize", {"protocolVersion": ver, "capabilities": {"roots": {}},
                       "clientInfo": {"name": "cb2-client", "version": "0.1"}})
    note("notifications/initialized")
    L.append("")
    req("initialize", {"protocolVersion": LATEST})               # already initialized
    req("tools/list", {"cursor": "abc"} if rng.random() < .5 else None)

    # key-value
    for k in keys[:9]:
        call("kv_set", {"value": rng.choice(["v", "", "multi\nline \"quoted\" \\ back", "\U0001F680 ok",
                                             "tab\there"]) + str(rng.randrange(10)), "key": k})
    call("kv_set", {"key": keys[2], "value": "second"})        # updated
    note("tools/call", {"name": "kv_set", "arguments": {"key": "ghost", "value": "boo"}})  # no effect
    call("kv_get", {"key": "ghost"})
    call("kv_get", {"key": keys[2]})
    call("kv_get", {"key": keys[0]}, id_=sid())
    call("kv_list", {"prefix": "zeta"})
    call("kv_list", omit=True)
    call("kv_list", {"prefix": ""})
    call("kv_list", {"prefix": "a"})
    call("kv_get", {"key": ""})                                 # minLength
    call("kv_get", {"key": keys[1], "zz": 1, "extra": True})    # unknown -> extra
    call("kv_set", {"key": "k"})                                # missing value
    call("kv_set", {"key": 5, "value": "v"})                    # wrong type
    call("kv_set", {"key": "k", "value": None})                 # null -> wrong type
    call("kv_get", [keys[0]])                                   # arguments not an object
    call("kv_get", None)                                        # arguments null
    call("kv_get", {})                                          # missing key

    # sum
    nums = [rng.randint(-10**12, 10**12) for _ in range(rng.randint(3, 8))]
    call("sum", {"numbers": nums})
    call("sum", {"numbers": [rng.randint(-50, 50)]}, id_=0)
    call("sum", {"numbers": []})
    call("sum", {"numbers": [1, 2.5]})
    call("sum", {"numbers": [3, True]})
    call("sum", {"numbers": str(rng.randrange(100))})
    call("sum", {"numbers": None, "Numbers": [1]})              # unknown wins over type error

    # text_stats
    texts = ["  héllo   wörld\t\tfoo\nbar\r\n", "a　b c d", "😀😀 é 🇬🇧", "", "   ",
             "x" * rng.randint(1, 5) + " " + "\U0001F600" * rng.randint(1, 4)]
    rng.shuffle(texts)
    for t in texts:
        call("text_stats", {"text": t})
    call("text_stats", {"text": ["no"]})

    # protocol errors
    call("kv_delete", {"key": "a"})
    req("tools/call", {"arguments": {"key": "a"}})               # name missing
    req("tools/call", {"name": 42})
    req("resources/list")
    note("resources/list")
    note("notifications/cancelled", {"requestId": 3, "reason": "x"})
    req("ping", [1])                                             # params not an object
    req("tools/call", "kv_get")                                  # params not an object
    req("ping", id_=-rng.randint(1, 10**6))
    req("ping", id_=9007199254740991 - rng.randrange(1000))
    req("ping", id_="")
    req("ping", id_=str(rng.randrange(10)))
    L.append(json.dumps({"jsonrpc": "2.0", "id": 5, "result": {}}))             # client response: ignore
    L.append(json.dumps({"jsonrpc": "2.0", "id": 6, "error": {"code": -1, "message": "no"}}))
    L.append(json.dumps({"jsonrpc": "1.0", "id": "old", "method": "ping"}))     # invalid, id echoed
    L.append(json.dumps({"jsonrpc": "2.0", "id": None, "method": "ping"}))      # null id
    L.append(json.dumps({"jsonrpc": "2.0", "id": True, "method": "ping"}))
    L.append(json.dumps({"jsonrpc": "2.0", "id": 1.5, "method": "ping"}))
    L.append(json.dumps({"jsonrpc": "2.0", "id": [1], "method": "ping"}))
    L.append(json.dumps({"jsonrpc": "2.0", "id": 77, "method": 5}))
    L.append(json.dumps({"jsonrpc": "2.0", "method": 5}))                       # invalid, no id -> null
    L.append(json.dumps({"id": 78, "method": "ping"}))                          # jsonrpc missing
    L.append(json.dumps([{"jsonrpc": "2.0", "id": 79, "method": "ping"}]))      # batch
    L.append(json.dumps("ping"))
    L.append("   \t")
    L.append(json.dumps({"jsonrpc": "2.0", "id": 80, "method": "ping", "params": {}}) + "\r")
    call("kv_get", {"key": keys[3]})
    call("kv_list", {"prefix": keys[4][:1]})

    # shuffle the middle a little: keep init block first, then mix two halves deterministically
    return L


def expected_for(lines):
    o = Oracle()
    exp = []
    for n, raw in enumerate(lines, 1):
        r = o.line(raw)
        if r is not None:
            exp.append((n, r))
    return exp


def to_plain(v):
    if isinstance(v, JText):
        return json.dumps(v.value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(v, dict):
        return {k: to_plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [to_plain(x) for x in v]
    return v


# ── checker ──────────────────────────────────────────────────────────────────
def kind(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if v is None:
        return "null"
    return {str: "string", list: "array", dict: "object"}[type(v)]


def short(v, n=160):
    s = json.dumps(to_plain(v), ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def diff(exp, got, path):
    """First structural difference or None."""
    if isinstance(exp, JText):
        if not isinstance(got, str):
            return f"at {path}: expected a string holding JSON, got {kind(got)} {short(got)}"
        try:
            val = json.loads(got)
        except ValueError:
            return f"at {path}: the text is not valid JSON: {got[:160]!r}"
        d = diff(exp.value, val, path + " (parsed)")
        return d
    if kind(exp) != kind(got):
        return f"at {path}: expected {kind(exp)} {short(exp)}, got {kind(got)} {short(got)}"
    if isinstance(exp, dict):
        missing = [k for k in exp if k not in got]
        extra = [k for k in got if k not in exp]
        if missing:
            return f"at {path}: member {json.dumps(missing[0])} is missing"
        if extra:
            return f"at {path}: unexpected member {json.dumps(extra[0])}"
        for k in exp:
            d = diff(exp[k], got[k], f"{path}.{k}" if k.isidentifier() else f"{path}[{json.dumps(k)}]")
            if d:
                return d
        return None
    if isinstance(exp, list):
        if len(exp) != len(got):
            return f"at {path}: expected an array of {len(exp)} elements, got {len(got)}: {short(got)}"
        for i, (a, b) in enumerate(zip(exp, got)):
            d = diff(a, b, f"{path}[{i}]")
            if d:
                return d
        return None
    if exp != got:
        return f"at {path}: expected {short(exp)}, got {short(got)}"
    return None


def make_checker(lines, exp):
    def check(stdout, workdir):
        got = [l for l in stdout.split("\n") if l.strip(" \t\r")]
        for i, (n, e) in enumerate(exp):
            req = lines[n - 1].strip()
            req = req if len(req) <= 200 else req[:200] + "…"
            where = f"response #{i + 1} (answering input line {n}: {req})"
            if i >= len(got):
                return False, (f"{where}: missing — your program wrote only {len(got)} response line(s); "
                               f"expected {len(exp)}.")
            try:
                g = json.loads(got[i])
            except ValueError:
                return False, f"{where}: output line {i + 1} is not valid JSON: {got[i][:200]!r}"
            d = diff(e, g, "$")
            if d:
                return False, f"{where}:\n  {d}\n  your line: {got[i][:300]}"
        if len(got) > len(exp):
            return False, (f"your program wrote {len(got)} response lines, expected {len(exp)}; the first extra "
                           f"line is: {got[len(exp)][:300]} (notifications and client responses must not be "
                           f"answered)")
        return True, ""
    return check


class Challenge(H.Challenge):
    name = "mcp_server"
    title = "MCP server over stdio: JSON-RPC 2.0, initialize, tools/list, tools/call, errors"
    category = "tools"

    def prompt(self, ctx):
        return prompt_text()

    def case(self, seed, ctx):
        lines = make_lines(seed)
        exp = expected_for(lines)
        stdin = ("\n".join(lines) + "\n").encode("utf-8")
        d = ctx.data_dir(self, seed)
        (d / "input.jsonl").write_bytes(stdin)
        (d / "expected.jsonl").write_text("\n".join(json.dumps(to_plain(e), ensure_ascii=False) for _, e in exp)
                                          + "\n")
        return H.Case(stdin=stdin, check=make_checker(lines, exp))
