import json, sys

TOOLS = [
    {"name": "kv_set", "title": "Set a value",
     "description": "Store a string value under a key, replacing any previous value.",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string", "minLength": 1}, "value": {"type": "string"}},
                     "required": ["key", "value"], "additionalProperties": False}},
    {"name": "kv_get", "title": "Get a value", "description": "Return the value stored under a key.",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string", "minLength": 1}},
                     "required": ["key"], "additionalProperties": False}},
    {"name": "kv_list", "title": "List keys",
     "description": "List the stored keys that start with a prefix, sorted by Unicode code point.",
     "inputSchema": {"type": "object", "properties": {"prefix": {"type": "string"}}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}},
                                                       "count": {"type": "integer"}}, "required": ["keys", "count"]}},
    {"name": "sum", "title": "Sum integers", "description": "Add up a non-empty list of integers.",
     "inputSchema": {"type": "object",
                     "properties": {"numbers": {"type": "array", "items": {"type": "integer"}, "minItems": 1}},
                     "required": ["numbers"], "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {"sum": {"type": "integer"}, "count": {"type": "integer"}},
                      "required": ["sum", "count"]},
     "annotations": {"readOnlyHint": True, "idempotentHint": True}},
    {"name": "text_stats", "title": "Text statistics",
     "description": "Count the words, Unicode code points and UTF-8 bytes of a text.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                     "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {"words": {"type": "integer"}, "codepoints": {"type": "integer"},
                                                       "utf8_bytes": {"type": "integer"}},
                      "required": ["words", "codepoints", "utf8_bytes"]},
     "annotations": {"readOnlyHint": True}},
]
BYNAME = {t["name"]: t for t in TOOLS}
state = {"init": False}
kv = {}
out = []


def isint(v):
    return type(v) is int


def send(o):
    out.append(json.dumps(o, ensure_ascii=False, separators=(",", ":")))


def error(i, c, m):
    send({"jsonrpc": "2.0", "id": i, "error": {"code": c, "message": m}})


def result(i, r):
    send({"jsonrpc": "2.0", "id": i, "result": r})


def bad(m):
    return {"content": [{"type": "text", "text": m}], "isError": True}


def good(t):
    return {"content": [{"type": "text", "text": t}], "isError": False}


def sgood(s):
    return {"content": [{"type": "text", "text": json.dumps(s, ensure_ascii=False)}], "structuredContent": s,
            "isError": False}


def run_tool(t, p):
    if "arguments" in p and type(p["arguments"]) is not dict:
        return bad("arguments must be an object")
    a = p.get("arguments", {})
    props = t["inputSchema"]["properties"]
    unk = [k for k in a if k not in props]
    if unk:
        return bad("unknown argument: " + min(unk))
    for name, sch in props.items():
        if name not in a:
            if name in t["inputSchema"].get("required", []):
                return bad("missing required argument: " + name)
            continue
        v = a[name]
        if sch["type"] == "string":
            if type(v) is not str:
                return bad(f"argument {name} must be a string")
            if sch.get("minLength") == 1 and v == "":
                return bad(f"argument {name} must not be empty")
        else:
            if type(v) is not list or not all(isint(x) for x in v):
                return bad(f"argument {name} must be an array of integers")
            if sch.get("minItems") == 1 and not v:
                return bad(f"argument {name} must not be empty")
    n = t["name"]
    if n == "kv_set":
        r = ("updated " if a["key"] in kv else "created ") + a["key"]
        kv[a["key"]] = a["value"]
        return good(r)
    if n == "kv_get":
        return good(kv[a["key"]]) if a["key"] in kv else bad("key not found: " + a["key"])
    if n == "kv_list":
        ks = sorted(k for k in kv if k.startswith(a.get("prefix", "")))
        return sgood({"keys": ks, "count": len(ks)})
    if n == "sum":
        return sgood({"sum": sum(a["numbers"]), "count": len(a["numbers"])})
    txt = a["text"]
    words = len([w for w in txt.replace("\t", " ").replace("\n", " ").replace("\r", " ").split(" ") if w])
    return sgood({"words": words, "codepoints": len(txt), "utf8_bytes": len(txt.encode("utf-8"))})


def bad_const(c):
    raise ValueError(c)


def handle(line):
    if line.strip(" \t\r") == "":
        return
    try:
        m = json.loads(line, parse_constant=bad_const)
    except ValueError:
        return error(None, -32700, "Parse error")
    if type(m) is not dict:
        return error(None, -32600, "Invalid Request")
    if "method" not in m and ("result" in m or "error" in m):
        return
    has_id = "id" in m
    idok = has_id and (type(m["id"]) is str or isint(m["id"]))
    if m.get("jsonrpc") != "2.0" or type(m.get("method")) is not str or (has_id and not idok):
        return error(m["id"] if idok else None, -32600, "Invalid Request")
    if not has_id:
        return
    i, meth = m["id"], m["method"]
    if meth not in ("initialize", "ping", "tools/list", "tools/call"):
        return error(i, -32601, "Method not found")
    if "params" in m and type(m["params"]) is not dict:
        return error(i, -32602, "Invalid params")
    p = m.get("params", {})
    if not state["init"] and meth not in ("initialize", "ping"):
        return error(i, -32002, "Server not initialized")
    if meth == "initialize":
        if state["init"]:
            return error(i, -32600, "Already initialized")
        if type(p.get("protocolVersion")) is not str:
            return error(i, -32602, "Invalid params")
        state["init"] = True
        v = p["protocolVersion"]
        if v not in ("2025-06-18", "2025-03-26", "2024-11-05"):
            v = "2025-06-18"
        return result(i, {"protocolVersion": v, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "cb2-kv", "title": "CB2 key-value server", "version": "1.4.2"}})
    if meth == "ping":
        return result(i, {})
    if meth == "tools/list":
        return result(i, {"tools": TOOLS})
    name = p.get("name")
    if type(name) is not str:
        return error(i, -32602, "Invalid params")
    if name not in BYNAME:
        return error(i, -32602, "Unknown tool: " + name)
    return result(i, run_tool(BYNAME[name], p))


data = sys.stdin.buffer.read().decode("utf-8")
for ln in data.split("\n"):
    handle(ln)
sys.stdout.buffer.write(("\n".join(out) + ("\n" if out else "")).encode("utf-8"))
