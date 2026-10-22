import csv, io, json, os, sys, urllib.error, urllib.request

data_dir, base = sys.argv[1], sys.argv[2]
task = json.load(open(os.path.join(data_dir, "task.json"), encoding="utf-8"))
rates = json.load(open(os.path.join(data_dir, "rates.json"), encoding="utf-8"))["rates"]
with open(os.path.join(data_dir, "inventory.csv"), encoding="utf-8-sig", newline="") as f:
    rows = list(csv.reader(f))
inv = {r[0]: r for r in rows[1:]}


def enc(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def run_tool(name, args):
    if name == "inventory_lookup":
        r = inv.get(args["sku"])
        if r is None:
            return enc({"error": "unknown sku: " + args["sku"]})
        return enc({"sku": r[0], "name": r[1], "price_cents": int(r[2]), "currency": r[3], "stock": int(r[4])})
    if name == "convert_currency":
        a, rf, rt = args["amount_cents"], rates[args["from"]], rates[args["to"]]
        return enc({"amount_cents": (2 * a * rt + rf) // (2 * rf), "currency": args["to"]})
    return enc({"error": "unknown tool: " + name})


out = []
msgs = [{"role": "system", "content": task["system"]}, {"role": "user", "content": task["user"]}]
usage = [0, 0, 0]
n = 0
while True:
    body = json.dumps({"model": task["model"], "messages": msgs, "tools": task["tools"],
                       "tool_choice": "auto"}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + task["api_key"],
                                          "Content-Type": "application/json"})
    n += 1
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = json.loads(e.read())["error"]["message"]
        out.append(f"ERROR {e.code} {msg}")
        break
    u = resp["usage"]
    usage[0] += u["prompt_tokens"]; usage[1] += u["completion_tokens"]; usage[2] += u["total_tokens"]
    ch = resp["choices"][0]
    m = ch["message"]
    if ch["finish_reason"] == "tool_calls":
        if isinstance(m.get("content"), str):
            out.append("SAY " + m["content"])
        calls = m["tool_calls"]
        msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": calls})
        for c in calls:
            out.append(f"CALL {c['id']} {c['function']['name']}")
        for c in calls:
            res = run_tool(c["function"]["name"], json.loads(c["function"]["arguments"]))
            out.append(f"RESULT {c['id']} {res}")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": res})
        continue
    out.append("FINAL " + m["content"])
    out.append(f"USAGE prompt={usage[0]} completion={usage[1]} total={usage[2]} requests={n}")
    break
sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
