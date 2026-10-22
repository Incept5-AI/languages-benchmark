"""rest_client — OAuth 2.0 client-credentials + cursor pagination against a misbehaving REST API.

Hard parts: RFC 6749 §2.3.1 Basic auth (form-urlencode id and secret BEFORE base64; the id contains `:`),
form-encoded token request (JSON → 415), opaque cursors containing `+ / =` that must be percent-encoded,
short and empty pages that are not the end, 429 + Retry-After (retrying early → another 429, which shows up in
the counters), 503 once, token expiry mid-run (re-authenticate and resume from the same cursor), chunked
transfer encoding on some responses, decimal-string amounts summed as exact integer cents beyond 2^31,
JSON escapes incl. surrogate pairs and `\\/`, and sorting by code point (not UTF-16 units).

The mock keeps all state per issued access token, so any number of clients can run at once.
"""
import base64
import binascii
import json
import random
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = """\
Write a {LANG} program that downloads every order from a paginated REST API protected by OAuth 2.0 client
credentials, and prints a report.

The program is run with two arguments: `<data_dir> <base_url>`.

`<data_dir>/client.json` holds the client settings, for example:
```json
{"client_id": "svc:reporting-0", "client_secret": "q+7/%41 z:k=", "scope": "orders:read", "page_size": 6}
```
`<base_url>` looks like `http://127.0.0.1:50123/c/0` (no trailing slash). The API has two endpoints:
`<base_url>/oauth/token` and `<base_url>/orders`.

## HTTP rules
- Send HTTP/1.1 requests with a `Host` header (an HTTP/1.0 request gets 505; a missing Host gets 400).
- Every response carries `Connection: close`. A response body is delimited either by `Content-Length` or by
  `Transfer-Encoding: chunked` (RFC 9112; chunk sizes in hex, either letter case); either can occur on any
  response. Every response body is a single line of UTF-8 JSON.

## Step 1 — get an access token
`POST <base_url>/oauth/token` with
- header `Content-Type: application/x-www-form-urlencoded` (any other media type, e.g. JSON, gets 415);
- body `grant_type=client_credentials&scope=<scope>` (form-urlencoded; `scope` from client.json);
- header `Authorization: Basic <b64>` built as RFC 6749 §2.3.1 requires: form-urlencode `client_id` and
  `client_secret` separately (UTF-8 bytes; ASCII letters, digits and `-._*` stay as they are, space becomes
  `+`, every other byte becomes `%XX` with uppercase hex), join the two results with `:`, and base64-encode
  that (standard alphabet `+/` with `=` padding). Credentials in the body get 400; wrong credentials get 401.

A 200 response looks like
`{"access_token":"at_…","token_type":"Bearer","expires_in":3600,"scope":"orders:read"}`.

## Step 2 — fetch the pages
`GET <base_url>/orders?limit=<page_size>` for the first page, then
`GET <base_url>/orders?limit=<page_size>&cursor=<next_cursor>` for each following page, with the header
`Authorization: Bearer <access_token>`. `limit` must be exactly `page_size` from client.json (400 otherwise).
The first request has no `cursor` parameter at all. A cursor is an opaque string that contains characters
such as `+`, `/` and `=`; percent-encode it in the query (`+` → `%2B`, `/` → `%2F`, `=` → `%3D`) — an
unencoded `+` reads as a space and the cursor is rejected with 400.

A 200 response is a JSON object with `data` (an array of orders) and `next_cursor` (a string, or `null` on
the last page), plus other fields to ignore; keys may come in any order. A page may contain fewer than
`page_size` orders, or none, even when more pages follow: keep going until `next_cursor` is `null` (process
that last page's `data` too).

During every run the server misbehaves in these ways, which your program must handle:
- **429** with a header `Retry-After: <seconds>` (an integer): wait at least that many seconds, then repeat
  the same request. Repeating it sooner earns another 429 (and shows up in your counters).
- **503**: repeat the same request (you may pause first, at most 1 second).
- **401** from `/orders`: the access token has expired (the response has
  `WWW-Authenticate: Bearer error="invalid_token", ...`). Get a new token (step 1) and repeat the same request
  with it — continue from the same cursor; do not start over. Never request a token at any other time
  (ignore `expires_in`; tokens stay valid until the server says otherwise).

Anything else is fatal: any other non-200 status, a non-200 from the token endpoint, or a 401 on the first
`/orders` request made with a newly obtained token. Then print exactly one line `ERROR <status> <body>` (the response body
exactly as received), print nothing else at all, and exit with status 0.

## Orders
Each order is an object such as
`{"id":"ord_0001","customer":{"id":"cus_3","name":"Zoë"},"amount":"12.30","currency":"EUR","status":"paid",…}`
with further fields (nested objects, arrays, numbers, nulls) to ignore. Strings may use any JSON escape,
including `\\/` and `\\uXXXX` (with surrogate pairs for characters outside the BMP).

`amount` is a decimal string: an optional `-`, one or more digits, then optionally `.` and one or two digits
(`"12.30"`, `"-0.05"`, `"7"`, `"0.5"`). It is an exact number of cents: `"0.5"` is 50 cents, `"0.29"` is 29
cents. Do not use floating point; sums can exceed 2^31 cents (they stay below 2^62).

## Output
1. For every pair (customer `name`, `currency`) that has at least one order whose `status` is not
   `cancelled`, one line `total <currency> <amount> <name>`, where `<amount>` is the sum of that pair's
   non-cancelled orders, formatted with at least one integer digit, exactly two decimals and a `-` only when
   negative (`-5` cents → `-0.05`, zero → `0.00`, `300000001400` cents → `3000000014.00`). Print names as
   UTF-8. Sort the lines by name, comparing Unicode code points in order (equivalently, the bytes of the
   UTF-8 encoding; not UTF-16 code units, not locale order; a proper prefix sorts first), then by currency.
2. For every status that occurs (all orders, cancelled included): `status <status> <count>`, sorted by status
   (statuses are lowercase ASCII).
3. One final line `orders <N> pages <P> retries <R> reauth <A>`: N = orders received, P = number of 200
   responses from `/orders`, R = number of 429 and 503 responses received, A = number of tokens obtained after
   the first one.

Example output (shape only):
```
total EUR 0.00 Ana "Ani" Silva
total USD 3000000014.00 Big Spender Inc
total GBP -0.05 Émile
total EUR 77.20 李雷
total USD 12.30 😀 Smile Co
status cancelled 3
status paid 31
orders 52 pages 10 retries 2 reauth 1
```
"""

NAMES = ['Ana "Ani" Silva', "Back\\slash Ltd", "A/B Testing Ltd", "Ünïcödé GmbH", "李雷", "😀 Smile Co",
         "Ｚeta Corp", "émile", "Émile", "O'Brien & Sons", "Zoë Ångström", "bob", "Bob", "𝔘nicode Math"]
STATUSES = ["paid", "pending", "refunded", "disputed"]
CURRENCIES = ["EUR", "USD", "GBP"]
FLOAT_TRAPS = [29, 57, 113, 435, 1015, 123456789, 1999, 7, -29]
K_EXPIRE_DEFAULT = None


def fmt_amount(cents: int, rng: random.Random) -> str:
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    w, f = divmod(a, 100)
    if f == 0:
        return sign + rng.choice([f"{w}", f"{w}.0", f"{w}.00"])
    if f % 10 == 0:
        return sign + rng.choice([f"{w}.{f // 10}", f"{w}.{f:02d}"])
    return f"{sign}{w}.{f:02d}"


def cents_str(c: int) -> str:
    return ("-" if c < 0 else "") + f"{abs(c) // 100}.{abs(c) % 100:02d}"


def make_cursor(rng: random.Random) -> str:
    while True:
        s = base64.b64encode(bytes(rng.randrange(256) for _ in range(rng.choice([13, 14, 16, 17])))).decode()
        if "+" in s and "/" in s and s.endswith("="):
            return s


def simulate(P, a, b, K):
    """A correct client's request sequence: returns (retries, reauth)."""
    cnt, f429, f503, retries, reauth, i = 0, False, False, 0, 0, 0
    while i < P:
        if cnt >= K:
            reauth += 1
            cnt, f429, f503 = 0, False, False
            continue
        cnt += 1
        if i == a and not f429:
            f429, retries = True, retries + 1
            continue
        if i == b and not f503:
            f503, retries = True, retries + 1
            continue
        i += 1
    return retries, reauth


def make_data(seed: int) -> dict:
    rng = random.Random(41000 + seed)
    page_size = rng.choice([5, 6, 7])
    must = ["😀 Smile Co", "Ｚeta Corp"]          # code point order != UTF-16 order for these two
    names = rng.sample([n for n in NAMES if n not in must and n != "Émile"], 5) + must
    big, neg, cancelled_only = "Big Spender Inc", "Émile", "Ghost Buyer"
    rows = []  # (name, currency, cents, status)
    rows += [(big, "USD", 1_500_000_007, "paid"), (big, "USD", 1_500_000_007, "pending")]
    zero, zc = rng.choice(names), rng.choice(CURRENCIES)
    rows += [(zero, zc, 1230, "paid"), (zero, zc, -1230, "refunded")]
    rows += [(neg, "GBP", -5, "refunded")]          # sole order of its pair: total -0.05
    rows += [(cancelled_only, "EUR", 999, "cancelled"), (cancelled_only, "USD", 12345, "cancelled")]
    for c in FLOAT_TRAPS:
        rows.append((rng.choice(names), rng.choice(CURRENCIES), c, rng.choice(STATUSES)))
    n_pages_data = rng.randint(8, 10)
    total = page_size * n_pages_data - rng.randint(2, page_size)
    while len(rows) < total:
        c = rng.choice([rng.randint(1, 9999), rng.randint(1, 500) * 10, rng.randint(1, 90) * 100,
                        -rng.randint(1, 3000), rng.randint(10000, 999999)])
        st = rng.choice(STATUSES + ["paid", "paid", "cancelled"])
        rows.append((rng.choice(names), rng.choice(CURRENCIES), c, st))
    rng.shuffle(rows)
    # 'zero' pair must not get extra rows in the same currency? Extra rows are fine; total just isn't zero.
    # Guarantee a true zero total with a dedicated name.
    rows.append(("Net Zero Ltd", "EUR", 4321, "paid"))
    rows.append(("Net Zero Ltd", "EUR", -4321, "refunded"))
    rng.shuffle(rows)

    orders = []
    for n, (name, cur, cents, st) in enumerate(rows, 1):
        o = {"id": f"ord_{seed}_{n:04d}",
             "created_at": f"2026-0{1 + n % 9}-{1 + n % 28:02d}T{n % 24:02d}:{(7 * n) % 60:02d}:00Z",
             "customer": {"id": f"cus_{abs(hash_name(name)) % 997}", "name": name,
                          "tags": rng.sample(["vip", "new", "b2b", "eu"], rng.randrange(3))},
             "amount": fmt_amount(cents, rng), "currency": cur, "status": st,
             "lines": [{"sku": f"SKU-{rng.randrange(100, 999)}", "qty": rng.randint(1, 4),
                        "unit": {"amount": "1.00", "currency": cur}} for _ in range(rng.randint(0, 2))],
             "note": rng.choice([None, "rush\norder", "gift \"wrap\"", "", "tab\there"])}
        items = list(o.items())
        rng.shuffle(items)
        orders.append((dict(items), name, cur, cents, st))

    # split into pages: full pages, one short page in the middle, then an empty last page
    pages, i = [], 0
    short_at = rng.randint(2, n_pages_data - 3)
    while i < len(orders):
        size = page_size if len(pages) != short_at else rng.randint(1, 2)
        pages.append(orders[i:i + size])
        i += size
    pages.append([])
    P = len(pages)
    cursors = [None] + [make_cursor(rng) for _ in range(P - 1)]

    a, b = rng.sample(range(1, P - 1), 2)
    ks = list(range(3, P))
    rng.shuffle(ks)
    K = next(k for k in ks if simulate(P, a, b, k) == (2, 1))

    alpha = "abcdefghijklmnopqrstuvwxyz0123456789"
    secret = "".join(rng.choice(alpha) for _ in range(6)) + rng.choice(["+", "/"]) + "%41 " + \
        "".join(rng.choice(alpha) for _ in range(4)) + ":=&~é"
    client = {"client_id": f"svc:reporting-{seed}", "client_secret": secret, "scope": "orders:read",
              "page_size": page_size}

    bodies = []
    for idx, pg in enumerate(pages):
        obj_items = [("object", "list"), ("data", [o for o, *_ in pg]),
                     ("next_cursor", cursors[idx + 1] if idx + 1 < P else None),
                     ("has_more", idx + 1 < P), ("page", {"index": idx, "limit": page_size})]
        rng.shuffle(obj_items)
        txt = json.dumps(dict(obj_items), ensure_ascii=rng.random() < 0.6, separators=(",", ":"))
        if rng.random() < 0.5:
            txt = txt.replace("/", "\\/")
        bodies.append(txt.encode())
    chunked = [rng.random() < 0.5 for _ in range(P)]
    chunked[rng.randrange(P)] = True
    return dict(client=client, pages=pages, cursors=cursors, bodies=bodies, chunked=chunked, P=P, a=a, b=b,
                K=K, token_chunked=rng.random() < 0.5, seed=seed)


def hash_name(name: str) -> int:
    h = 0
    for ch in name.encode():
        h = (h * 131 + ch) % 1_000_003
    return h


def expected_output(d: dict) -> str:
    totals, statuses, n = {}, {}, 0
    for pg in d["pages"]:
        for _o, name, cur, cents, st in pg:
            n += 1
            statuses[st] = statuses.get(st, 0) + 1
            if st != "cancelled":
                totals[(name, cur)] = totals.get((name, cur), 0) + cents
    out = [f"total {cur} {cents_str(v)} {name}" for (name, cur), v in sorted(totals.items())]
    out += [f"status {s} {c}" for s, c in sorted(statuses.items())]
    retries, reauth = simulate(d["P"], d["a"], d["b"], d["K"])
    out.append(f"orders {n} pages {d['P']} retries {retries} reauth {reauth}")
    return "\n".join(out)


# ── mock server ──────────────────────────────────────────────────────────────
class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):   # client went away: ignore
            super().handle_error(request, client_address)


def form_encode(s: str) -> str:
    out = []
    for b in s.encode():
        c = chr(b)
        if c.isascii() and (c.isalnum() or c in "-._*"):
            out.append(c)
        elif c == " ":
            out.append("+")
        else:
            out.append(f"%{b:02X}")
    return "".join(out)


class Challenge(H.Challenge):
    name = "rest_client"
    title = "OAuth client-credentials REST client: pagination, 429/503 retries, token expiry"
    category = "api"
    timeout = 20

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()
        self._tokens = {}
        self.server = None

    def data(self, seed: int) -> dict:
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

            def send(self, status, body: bytes, headers=(), chunked=False, rng=None):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if chunked:
                    i = 0
                    while i < len(body):
                        n = rng.randint(1, 300)
                        part = body[i:i + n]
                        size = f"{len(part):x}" if rng.random() < 0.5 else f"{len(part):X}"
                        self.wfile.write(size.encode() + b"\r\n" + part + b"\r\n")
                        i += n
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(body)
                self.close_connection = True

            def err(self, status, error, message, headers=()):
                self.send(status, json.dumps({"error": error, "message": message},
                                             separators=(",", ":")).encode(), headers)

            def read_body(self):
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    data = b""
                    while True:
                        line = self.rfile.readline()
                        n = int(line.split(b";")[0].strip(), 16)
                        if n == 0:
                            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                                pass
                            return data
                        data += self.rfile.read(n)
                        self.rfile.readline()
                n = self.headers.get("Content-Length")
                if n is None:
                    return None
                return self.rfile.read(int(n))

            def route(self, method):
                if self.request_version != "HTTP/1.1":
                    self.close_connection = True
                    return self.err(505, "http_version_not_supported", "send HTTP/1.1 requests")
                if not self.headers.get("Host"):
                    return self.err(400, "invalid_request", "missing Host header")
                u = urlsplit(self.path)
                m = re.fullmatch(r"/c/(\d+)/(oauth/token|orders)", u.path)
                if not m or int(m.group(1)) > 50:
                    return self.err(404, "not_found", f"no such endpoint: {u.path}")
                d = ch.data(int(m.group(1)))
                if m.group(2) == "oauth/token":
                    if method != "POST":
                        return self.err(405, "method_not_allowed", "use POST for /oauth/token")
                    return self.token(d)
                if method != "GET":
                    return self.err(405, "method_not_allowed", "use GET for /orders")
                return self.orders(d, u.query)

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

            def do_PUT(self):
                self.route("PUT")

            def do_DELETE(self):
                self.route("DELETE")

            def token(self, d):
                body = self.read_body()
                if body is None:
                    return self.err(411, "length_required", "POST body needs Content-Length")
                ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if ctype != "application/x-www-form-urlencoded":
                    return self.err(415, "unsupported_media_type",
                                    "token requests must be application/x-www-form-urlencoded")
                try:
                    text = body.decode("ascii")
                    params = {}
                    for part in text.split("&"):
                        if not part:
                            continue
                        k, _, v = part.partition("=")
                        k, v = unquote_plus(k, errors="strict"), unquote_plus(v, errors="strict")
                        if k in params:
                            raise ValueError
                        params[k] = v
                except Exception:
                    return self.err(400, "invalid_request", "body is not valid form-urlencoded data")
                if "client_id" in params or "client_secret" in params:
                    return self.err(400, "invalid_request",
                                    "client credentials must be sent with HTTP Basic authentication")
                auth = self.headers.get("Authorization", "")
                ok = False
                scheme, _, b64 = auth.partition(" ")
                if scheme.lower() == "basic":
                    try:
                        raw = base64.b64decode(b64.strip(), validate=True).decode("utf-8")
                        uid, sep, pw = raw.partition(":")
                        ok = bool(sep) and unquote_plus(uid, errors="strict") == d["client"]["client_id"] and \
                            unquote_plus(pw, errors="strict") == d["client"]["client_secret"]
                    except (binascii.Error, UnicodeDecodeError, ValueError):
                        ok = False
                if not ok:
                    return self.err(401, "invalid_client",
                                    "client authentication failed (RFC 6749 section 2.3.1 Basic credentials)",
                                    [("WWW-Authenticate", 'Basic realm="orders"')])
                if params.get("grant_type") != "client_credentials":
                    return self.err(400, "unsupported_grant_type", "grant_type must be client_credentials")
                if params.get("scope") != d["client"]["scope"]:
                    return self.err(400, "invalid_scope", f"scope must be {d['client']['scope']}")
                tok = "at_" + secrets.token_urlsafe(18)
                with ch._lock:
                    ch._tokens[tok] = {"seed": d["seed"], "count": 0, "t429": None, "did503": False}
                body = json.dumps({"access_token": tok, "token_type": "Bearer", "expires_in": 3600,
                                   "scope": d["client"]["scope"]}, separators=(",", ":")).encode()
                self.send(200, body, [("Cache-Control", "no-store")], chunked=d["token_chunked"],
                          rng=random.Random(tok))

            def orders(self, d, query):
                auth = self.headers.get("Authorization", "")
                scheme, _, tok = auth.partition(" ")
                tok = tok.strip()
                if scheme.lower() != "bearer" or not tok:
                    return self.err(401, "missing_token", "send Authorization: Bearer <access_token>",
                                    [("WWW-Authenticate", 'Bearer realm="orders"')])
                with ch._lock:
                    st = ch._tokens.get(tok)
                    if st is None or st["seed"] != d["seed"]:
                        res = "unknown"
                    elif st["count"] >= d["K"]:
                        res = "expired"
                    else:
                        st["count"] += 1
                        res = "ok"
                if res != "ok":
                    return self.err(401, "invalid_token", f"the access token is {res}",
                                    [("WWW-Authenticate",
                                      f'Bearer realm="orders", error="invalid_token", '
                                      f'error_description="The access token is {res}"')])
                # strict query parsing: '+' means space (form encoding), so a raw '+' in a cursor breaks it
                params = {}
                try:
                    for part in query.split("&") if query else []:
                        k, sep, v = part.partition("=")
                        k, v = unquote_plus(k, errors="strict"), unquote_plus(v, errors="strict")
                        if k in params:
                            return self.err(400, "invalid_request", f"duplicate query parameter {k}")
                        params[k] = v
                except Exception:
                    return self.err(400, "invalid_request", "malformed query string")
                for k in params:
                    if k not in ("limit", "cursor"):
                        return self.err(400, "invalid_request", f"unknown query parameter {k}")
                if params.get("limit") != str(d["client"]["page_size"]):
                    return self.err(400, "invalid_limit", f"limit must be {d['client']['page_size']}")
                if "cursor" in params:
                    try:
                        idx = d["cursors"].index(params["cursor"], 1)
                    except ValueError:
                        return self.err(400, "invalid_cursor",
                                        f"unknown cursor {params['cursor']!r} (cursors must be percent-encoded)")
                else:
                    idx = 0
                now = time.monotonic()
                with ch._lock:
                    if idx == d["a"]:
                        if st["t429"] is None:
                            st["t429"] = now
                            r = 429
                        elif now - st["t429"] < 0.9:      # too early: another 429 (window not extended)
                            r = 429
                        else:
                            r = 0
                    else:
                        r = 0
                    if not r and idx == d["b"] and not st["did503"]:
                        st["did503"] = True
                        r = 503
                if r == 429:
                    return self.err(429, "rate_limited", "too many requests", [("Retry-After", "1")])
                if r == 503:
                    return self.err(503, "unavailable", "temporarily unavailable, retry")
                self.send(200, d["bodies"][idx], chunked=d["chunked"][idx],
                          rng=random.Random(f"{d['seed']}/{idx}"))

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
        (dd / "client.json").write_text(json.dumps(d["client"], ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
        return H.Case(args=[str(dd), f"http://127.0.0.1:{self.port}/c/{seed}"], expected=expected_output(d))
