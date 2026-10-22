import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request

data_dir, base = sys.argv[1], sys.argv[2]
cfg = json.load(open(os.path.join(data_dir, "client.json"), encoding="utf-8"))


class Fatal(Exception):
    pass


def http(method, url, headers, body=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def form(s):
    out = []
    for b in s.encode("utf-8"):
        c = chr(b)
        if c.isascii() and (c.isalnum() or c in "-._*"):
            out.append(c)
        elif c == " ":
            out.append("+")
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def get_token():
    basic = base64.b64encode((form(cfg["client_id"]) + ":" + form(cfg["client_secret"])).encode()).decode()
    body = ("grant_type=client_credentials&scope=" + form(cfg["scope"])).encode()
    st, _, rb = http("POST", base + "/oauth/token",
                     {"Content-Type": "application/x-www-form-urlencoded", "Authorization": "Basic " + basic},
                     body)
    if st != 200:
        raise Fatal(st, rb)
    return json.loads(rb)["access_token"]


def main():
    token = get_token()
    fresh = True
    cursor = None
    orders, pages, retries, reauth = [], 0, 0, 0
    while True:
        url = base + "/orders?limit=" + str(cfg["page_size"])
        if cursor is not None:
            url += "&cursor=" + urllib.parse.quote(cursor, safe="")
        st, hd, rb = http("GET", url, {"Authorization": "Bearer " + token, "Accept": "application/json"})
        if st == 429:
            retries += 1
            fresh = False
            time.sleep(int(hd.get("Retry-After", "1")) + 0.05)
            continue
        if st == 503:
            retries += 1
            fresh = False
            continue
        if st == 401 and not fresh:
            token = get_token()
            reauth += 1
            fresh = True
            continue
        if st != 200:
            raise Fatal(st, rb)
        fresh = False
        pages += 1
        page = json.loads(rb)
        orders.extend(page["data"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    def cents(a):
        neg = a.startswith("-")
        a = a.lstrip("-")
        w, _, f = a.partition(".")
        v = int(w) * 100 + int((f + "00")[:2])
        return -v if neg else v

    totals, statuses = {}, {}
    for o in orders:
        statuses[o["status"]] = statuses.get(o["status"], 0) + 1
        if o["status"] != "cancelled":
            k = (o["customer"]["name"], o["currency"])
            totals[k] = totals.get(k, 0) + cents(o["amount"])
    out = []
    for (name, cur), v in sorted(totals.items()):
        out.append(f"total {cur} {'-' if v < 0 else ''}{abs(v) // 100}.{abs(v) % 100:02d} {name}")
    for s, c in sorted(statuses.items()):
        out.append(f"status {s} {c}")
    out.append(f"orders {len(orders)} pages {pages} retries {retries} reauth {reauth}")
    return "\n".join(out) + "\n"


try:
    text = main()
except Fatal as e:
    text = f"ERROR {e.args[0]} {e.args[1].decode('utf-8', 'replace')}\n"
sys.stdout.buffer.write(text.encode("utf-8"))
