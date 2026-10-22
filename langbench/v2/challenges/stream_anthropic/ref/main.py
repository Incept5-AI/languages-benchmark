import json, os, sys, urllib.error, urllib.request

data_dir, base = sys.argv[1], sys.argv[2]
cfg = json.load(open(os.path.join(data_dir, "request.json"), encoding="utf-8"))
out = []
tot = {"input": 0, "output": 0, "cache": 0}


def esc(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


class ApiErr(Exception):
    pass


def events(resp):
    """Yield (event, data) from an SSE byte stream read in arbitrary pieces."""
    buf = b""
    name, data = None, []
    while True:
        piece = resp.read1(64) if hasattr(resp, "read1") else resp.read(64)
        if not piece:
            break
        buf += piece
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw[:-1] if raw.endswith(b"\r") else raw
            line = line.decode("utf-8")
            if line == "":
                if data:
                    yield name, "\n".join(data)
                name, data = None, []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)


def call(messages):
    body = {"model": cfg["model"], "max_tokens": cfg["max_tokens"], "stream": True,
            "thinking": {"type": "enabled", "budget_tokens": cfg["thinking_budget"]},
            "system": cfg["system"], "tools": cfg["tools"], "messages": messages}
    req = urllib.request.Request(base + "/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())["error"]
        raise ApiErr(f"ERROR {e.code} {err['type']}: {err['message']}")
    blocks, raw_json, stop = {}, {}, None
    with resp:
        for _name, data in events(resp):
            ev = json.loads(data)
            t = ev.get("type")
            if t == "message_start":
                u = ev["message"]["usage"]
                tot["input"] += u["input_tokens"]
                tot["cache"] += u.get("cache_read_input_tokens", 0) or 0
            elif t == "content_block_start":
                cb = dict(ev["content_block"])
                if cb["type"] == "tool_use":
                    cb["input"] = None
                    raw_json[ev["index"]] = ""
                blocks[ev["index"]] = cb
            elif t == "content_block_delta":
                b, dl = blocks[ev["index"]], ev["delta"]
                if dl["type"] == "text_delta":
                    b["text"] += dl["text"]
                elif dl["type"] == "thinking_delta":
                    b["thinking"] += dl["thinking"]
                elif dl["type"] == "signature_delta":
                    b["signature"] += dl["signature"]
                elif dl["type"] == "input_json_delta":
                    raw_json[ev["index"]] += dl["partial_json"]
            elif t == "content_block_stop":
                i = ev["index"]
                if blocks[i]["type"] == "tool_use":
                    blocks[i]["input"] = json.loads(raw_json[i]) if raw_json[i] else {}
            elif t == "message_delta":
                stop = ev["delta"].get("stop_reason")
                tot["output"] += ev["usage"]["output_tokens"]
            elif t == "message_stop":
                break
    ordered = [blocks[i] for i in sorted(blocks)]
    for i, b in zip(sorted(blocks), ordered):
        if b["type"] == "thinking":
            out.append("thinking " + esc(b["thinking"]))
            out.append("signature " + b["signature"])
        elif b["type"] == "text":
            out.append("text " + esc(b["text"]))
        elif b["type"] == "tool_use":
            out.append(f"tool_use {b['id']} {b['name']} {esc(raw_json[i])}")
    out.append(f"stop_reason {stop}")
    return ordered, stop


def run_tool(name, inp):
    if name == "measure":
        t, u = inp["text"], inp["unit"]
        if u == "code_points":
            return str(len(t)), False
        if u == "utf8_bytes":
            return str(len(t.encode("utf-8"))), False
        if u == "utf16_units":
            return str(sum(2 if ord(c) > 0xFFFF else 1 for c in t)), False
        return "unsupported unit: " + u, True
    if name == "reverse":
        return inp["text"][::-1], False
    return "unknown tool: " + name, True


try:
    msgs = [{"role": "user", "content": cfg["prompt"]}]
    out.append("request 1")
    blocks, stop = call(msgs)
    content, results = [], []
    for b in blocks:
        if b["type"] == "thinking":
            content.append({"type": "thinking", "thinking": b["thinking"], "signature": b["signature"]})
        elif b["type"] == "text":
            content.append({"type": "text", "text": b["text"]})
        elif b["type"] == "tool_use":
            content.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})
            res, is_err = run_tool(b["name"], b["input"])
            out.append(f"{'error' if is_err else 'result'} {b['id']} {esc(res)}")
            r = {"type": "tool_result", "tool_use_id": b["id"], "content": res}
            if is_err:
                r["is_error"] = True
            results.append(r)
    msgs += [{"role": "assistant", "content": content}, {"role": "user", "content": results}]
    out.append("request 2")
    call(msgs)
    out.append(f"usage input={tot['input']} output={tot['output']} cache_read={tot['cache']}")
except ApiErr as e:
    out.append(str(e))
sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
