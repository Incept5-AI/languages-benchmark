"""websocket_client — an RFC 6455 client against a strict mock server.

Hard parts: the opening handshake (Host with port, request-target with query, Sec-WebSocket-Key, checking
Sec-WebSocket-Accept, reading frames that arrive in the same TCP read as the 101 headers), client masking
with minimal length encoding (125/126 byte boundary, 16-bit lengths), reassembling fragmented messages
whose fragments split UTF-8 sequences, pings interleaved between fragments (answered at once, payload
echoed byte for byte), unsolicited pongs, 64-bit payload lengths, and the close handshake. The server
rejects every protocol mistake with a close frame whose reason the client prints, so mistakes show up in
the output.
"""
import base64
import hashlib
import random
import socket
import socketserver
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
READ_TIMEOUT = 5.0

PROMPT = """\
Write a {LANG} program that is a WebSocket client (RFC 6455) for a small command protocol.

The program is run with one or more arguments, each a WebSocket URL of the form
`ws://127.0.0.1:<port>/<path>` where `<path>` may include a query string, e.g.
`ws://127.0.0.1:41234/c/0/alpha?token=x7`. Handle the URLs one after another, in argument order, each on its
own TCP connection ("session"). Before each session print a line `session <n>` (n = 1, 2, 3, …).

## Opening handshake

Send an HTTP/1.1 upgrade request. The server checks it strictly and rejects anything else:
- request line `GET <request-target> HTTP/1.1`, where `<request-target>` is the URL's path including its
  query string exactly as given (e.g. `/c/0/alpha?token=x7`);
- `Host: 127.0.0.1:<port>` (host and port exactly as in the URL);
- `Upgrade: websocket`, `Connection: Upgrade`, `Sec-WebSocket-Version: 13`;
- `Sec-WebSocket-Key:` the standard base64 encoding (with padding) of 16 random bytes, fresh for each session;
- lines end with CRLF and the header block ends with an empty line. Send no body.

Read the response header block (up to and including the empty line). Header names are case-insensitive and
the server may send extra headers. **The server writes its first WebSocket frames immediately after the
header block, often in the same TCP segment**, so bytes you have already read past the empty line belong to
the frame stream.

- If the status code is not 101, print `handshake failed: <status code> <reason phrase>` (the rest of the
  status line after the code, as received), close the connection and go on to the next URL.
- If the status is 101 but `Sec-WebSocket-Accept` is missing or is not exactly
  base64(SHA-1(<the key you sent> + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")) (key as the ASCII string you
  sent, digest as 20 raw bytes, standard base64 with padding), print
  `handshake failed: bad Sec-WebSocket-Accept`, close the connection without sending anything and go on to
  the next URL.

## Frames

Use the RFC 6455 base framing. Frames from the server are never masked and never use RSV bits. Every frame
you send must:
- have FIN set and RSV1–3 clear (send each message as one single frame — never fragment);
- be masked with a fresh random 4-byte masking key that is not all zero;
- use the minimal payload length encoding: 0–125 in the 7-bit field, 126–65535 as 126 + 16-bit length,
  larger as 127 + 64-bit length (all big-endian).

From the server you will receive:
- text (opcode 1) and binary (opcode 2) messages, possibly **fragmented**: a first frame with FIN clear,
  then continuation frames (opcode 0, possibly empty), the last one with FIN set. Concatenate the payload
  bytes of all fragments; a UTF-8 character may be split across fragments, so decode text only after the
  whole message has arrived. Payload lengths use all three encodings, including 64-bit.
- ping (opcode 9): reply at once — before processing any later frame — with a pong (opcode 10) carrying
  exactly the same payload bytes (which may be empty or arbitrary binary). Pings may arrive between the
  fragments of a message.
- pong (opcode 10): unsolicited; ignore it.
- close (opcode 8): see below.

## Commands

Each text message from the server is a command. Reply to each with exactly one text message, then print
a line `<command> -> <reply>`:
- `ADD <a> <b>` — a and b are decimal integers (possibly negative, |a|, |b| <= 10^15). Reply with their sum in
  decimal. Print `ADD <a> <b> -> <sum>` (the command text as received).
- `REV <text>` — `<text>` is everything after the first space. Reply with `<text>` reversed by Unicode code
  point. Print `REV <text> -> <reversed>`.
- `SHA1` — the next data message is a binary message (possibly fragmented, possibly empty, possibly larger
  than 64 KiB). Reply with the lowercase hex SHA-1 of its payload. Print `SHA1 <payload length in bytes> -> <hex>`.
- `BYE` — do not send a text reply and print no line for it. Send a close frame with status code 1000 and no reason (payload exactly
  the two bytes 0x03 0xE8), then read frames until the server's close frame arrives and handle it as below.

## Close

When a close frame arrives from the server, print `closed <code> <reason>` (status code from the first two
payload bytes, big-endian; reason = the remaining payload bytes as UTF-8; print `closed <code>` if the reason
is empty). If you had not already sent a close frame in this session, first send one back carrying the same
status code and no reason. Then close the TCP connection and go on to the next URL.

The server checks everything above; when it detects a mistake (unmasked frame, bad length encoding, missing or
wrong pong, wrong reply, …) it sends a close frame with status 1002, 1007 or 1008 and a reason describing the
mistake, which your program then prints like any other close.

## Example

For `ws://127.0.0.1:41234/c/0/alpha?token=x7 ws://127.0.0.1:41234/c/0/beta ws://127.0.0.1:41234/c/0/gamma`
the output could be:
```
session 1
ADD 17 -25 -> -8
REV héllo wörld 😀 -> 😀 dlröw olléh
SHA1 70000 -> 6ac7a2a0c9fc5a5c7ee0e7c9d6b4f0b1d9e1a3f2
SHA1 0 -> da39a3ee5e6b4b0d3255bfef95601890afd80709
closed 4001 fin ✓
session 2
handshake failed: bad Sec-WebSocket-Accept
session 3
handshake failed: 403 Forbidden
```
"""

# ── frames ───────────────────────────────────────────────────────────────────


def frame(op, payload=b"", fin=True):
    b0 = (0x80 if fin else 0) | op
    n = len(payload)
    if n < 126:
        h = bytes([b0, n])
    elif n < 65536:
        h = bytes([b0, 126]) + n.to_bytes(2, "big")
    else:
        h = bytes([b0, 127]) + n.to_bytes(8, "big")
    return h + payload


def split_bytes(data, rng, parts):
    """Split into `parts` pieces at random cut points (cuts may land inside UTF-8 sequences)."""
    if len(data) < parts:
        return [data] + [b""] * (parts - 1)
    cuts = sorted(rng.sample(range(1, len(data)), parts - 1))
    out, last = [], 0
    for c in cuts + [len(data)]:
        out.append(data[last:c])
        last = c
    return out


def accept_for(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


# ── case data ────────────────────────────────────────────────────────────────
WORDS = ["héllo", "wörld", "😀", "naïve", "straße", "日本語", "ok", "ÆØÅ", "é", "𝄞", "abc", "Ελλάδα",
         "кошка", "🚀🚀", "x", "fin", "ação", "–", "🙂", "zz"]


def utext(rng, nbytes_min, nbytes_max=None):
    """Random unicode text (words joined by single spaces, no leading/trailing space)."""
    while True:
        words = []
        while len(" ".join(words).encode()) < nbytes_min:
            words.append(rng.choice(WORDS))
        s = " ".join(words)
        if nbytes_max is None or len(s.encode()) <= nbytes_max:
            return s


def text_of_exact_bytes(rng, n):
    """Text whose UTF-8 encoding is exactly n bytes, with multi-byte characters inside."""
    s = utext(rng, max(1, n - 12), n)
    while len(s.encode()) < n:
        s += rng.choice("abcdefghij")
    return s


_SPEC_CACHE = {}
_SPEC_LOCK = threading.Lock()


def spec(seed):
    with _SPEC_LOCK:
        if seed not in _SPEC_CACHE:
            _SPEC_CACHE[seed] = make_spec(seed)
        return _SPEC_CACHE[seed]


def make_spec(seed):
    rng = random.Random(6455 + seed)
    names = rng.sample(["alpha", "beta", "gamma", "delta", "omega", "kappa", "sigma", "zeta"], 3)
    token = "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(6))

    # script items: ("send", bytes) | ("ping", payload) | ("expect", reply) | ("bye",)
    blocks = []
    out = []

    def cmd_block(items, line):
        blocks.append((items, line))

    # 1. ADD (sent together with the handshake response, preceded by a ping) — fixed first
    a, b = rng.randint(-10**15, 10**15), rng.randint(-10**6, 10**6)
    first = ([("ping", b"hb-" + str(seed).encode())], f"ADD {a} {b}", str(a + b))

    # 2. REV fragmented in 4 with a ping inside and an empty continuation, cuts inside UTF-8 sequences
    t = utext(rng, 30, 60)
    data = f"REV {t}".encode()
    p = split_bytes(data, rng, 3)
    pingp = bytes(rng.randrange(256) for _ in range(rng.randint(5, 20)))
    items = [("send", frame(1, p[0], fin=False)), ("ping", pingp), ("send", frame(0, p[1], fin=False)),
             ("send", frame(0, b"", fin=False)), ("send", frame(0, p[2], fin=True)), ("expect", t[::-1])]
    cmd_block(items, f"REV {t} -> {t[::-1]}")

    # 3. SHA1 + big binary (64-bit length), sent in delayed chunks
    big = bytes(rng.randrange(256) for _ in range(rng.randint(65536, 72000)))
    items = [("send", frame(1, b"SHA1")), ("slow", frame(2, big)),
             ("expect", hashlib.sha1(big).hexdigest())]
    cmd_block(items, f"SHA1 {len(big)} -> {hashlib.sha1(big).hexdigest()}")

    # 4. SHA1 (itself fragmented) + fragmented binary with 16-bit fragments and an empty ping between
    mid = bytes(rng.randrange(256) for _ in range(rng.randint(900, 3000)))
    q = split_bytes(mid, rng, 3)
    items = [("send", frame(1, b"SH", fin=False)), ("send", frame(0, b"A1", fin=True)),
             ("send", frame(2, q[0], fin=False)), ("ping", b""), ("send", frame(0, q[1], fin=False)),
             ("send", frame(0, q[2], fin=True)), ("expect", hashlib.sha1(mid).hexdigest())]
    cmd_block(items, f"SHA1 {len(mid)} -> {hashlib.sha1(mid).hexdigest()}")

    # 5/6. REV whose reply is exactly 125 and exactly 126 bytes (7-bit / 16-bit boundary)
    for n in (125, 126):
        t = text_of_exact_bytes(rng, n)
        items = [("send", frame(1, f"REV {t}".encode())), ("expect", t[::-1])]
        cmd_block(items, f"REV {t} -> {t[::-1]}")

    # 7. long REV (16-bit both ways), unsolicited pong first
    t = utext(rng, 150, 260)
    items = [("send", frame(10, b"unsolicited")), ("send", frame(1, f"REV {t}".encode())),
             ("expect", t[::-1])]
    cmd_block(items, f"REV {t} -> {t[::-1]}")

    # 8. ADD negatives, fragmented in 2
    a, b = -rng.randint(1, 10**15), rng.randint(-10**15, -1)
    d = f"ADD {a} {b}".encode()
    c = rng.randint(1, len(d) - 1)
    items = [("send", frame(1, d[:c], fin=False)), ("send", frame(0, d[c:], fin=True)),
             ("expect", str(a + b))]
    cmd_block(items, f"ADD {a} {b} -> {a + b}")

    # 9. SHA1 of an empty binary message
    items = [("send", frame(1, b"SHA1")), ("send", frame(2, b"")),
             ("expect", hashlib.sha1(b"").hexdigest())]
    cmd_block(items, f"SHA1 0 -> {hashlib.sha1(b'').hexdigest()}")

    rng.shuffle(blocks)
    script = list(first[0]) + [("send", frame(1, first[1].encode())), ("expect", first[2])]
    lines = [f"{first[1]} -> {first[2]}"]
    for items, line in blocks:
        script += items
        lines.append(line)
    code = rng.choice([1000, 1001, 4000 + rng.randrange(1000)])
    reason = rng.choice(["fin ✓", "au revoir — à bientôt", "bye 👋", "done", "さようなら"]) + f" {seed}"
    script.append(("bye", code, reason))
    lines.append(f"closed {code} {reason}")

    bad_accept_kind = rng.randrange(3)
    status = rng.choice([(403, "Forbidden"), (404, "Not Found"), (503, "Service Unavailable")])
    sessions = [("main", names[0]), ("badaccept", names[1]), ("status", names[2])]
    rng.shuffle(sessions)
    hdr_style = rng.randrange(3)
    return dict(seed=seed, token=token, sessions=sessions, script=script, main_lines=lines,
                bad_accept_kind=bad_accept_kind, status=status, hdr_style=hdr_style, names=names)


def case_paths(sp):
    paths = []
    for kind, name in sp["sessions"]:
        p = f"/c/{sp['seed']}/{name}"
        if kind == "main":
            p += f"?token={sp['token']}"
        paths.append(p)
    return paths


def expected_output(sp):
    out = []
    for i, (kind, name) in enumerate(sp["sessions"], 1):
        out.append(f"session {i}")
        if kind == "main":
            out += sp["main_lines"]
        elif kind == "badaccept":
            out.append("handshake failed: bad Sec-WebSocket-Accept")
        else:
            out.append(f"handshake failed: {sp['status'][0]} {sp['status'][1]}")
    return "\n".join(out)


# ── server ───────────────────────────────────────────────────────────────────
class ProtoErr(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code, self.reason = code, reason


class Conn:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.waiting = "the client"

    def fill(self):
        try:
            d = self.sock.recv(65536)
        except socket.timeout:
            raise ProtoErr(1002, "timeout waiting for " + self.waiting)
        if not d:
            raise EOFError
        self.buf += d

    def read_exact(self, n):
        while len(self.buf) < n:
            self.fill()
        d, self.buf = self.buf[:n], self.buf[n:]
        return d

    def read_headers(self):
        while b"\r\n\r\n" not in self.buf:
            if len(self.buf) > 16384:
                raise EOFError
            self.fill()
        i = self.buf.index(b"\r\n\r\n")
        h, self.buf = self.buf[:i], self.buf[i + 4:]
        return h

    def read_client_frame(self):
        b0, b1 = self.read_exact(2)
        fin, rsv, op = b0 & 0x80, b0 & 0x70, b0 & 0x0F
        masked, n = b1 & 0x80, b1 & 0x7F
        if rsv:
            raise ProtoErr(1002, "RSV bits set in a client frame")
        if n == 126:
            n = int.from_bytes(self.read_exact(2), "big")
            if n < 126:
                raise ProtoErr(1002, "payload length not minimally encoded (16-bit length used for <126)")
        elif n == 127:
            n = int.from_bytes(self.read_exact(8), "big")
            if n < 65536:
                raise ProtoErr(1002, "payload length not minimally encoded (64-bit length used for <65536)")
            if n > 1 << 20:
                raise ProtoErr(1009, "message too big")
        if not masked:
            raise ProtoErr(1002, "client frame not masked")
        key = self.read_exact(4)
        if key == b"\0\0\0\0":
            raise ProtoErr(1002, "all-zero masking key")
        data = self.read_exact(n)
        payload = bytes(c ^ key[i & 3] for i, c in enumerate(data))
        if op >= 8:
            if not fin or n > 125:
                raise ProtoErr(1002, "control frame fragmented or longer than 125 bytes")
        elif op == 1:
            if not fin:
                raise ProtoErr(1002, "fragmented client message (send each message as one frame)")
        else:
            raise ProtoErr(1002, f"unexpected opcode {op} from client")
        if op not in (1, 8, 10):
            raise ProtoErr(1002, f"unexpected opcode {op} from client")
        return op, payload


def bad_request(sock, why):
    body = (why + "\n").encode()
    sock.sendall(f"HTTP/1.1 400 Bad Request: {why}\r\nContent-Type: text/plain\r\nContent-Length: "
                 f"{len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


def check_request(head: bytes, port: int):
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        return None, "non-ASCII bytes in the request header"
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[0] != "GET" or parts[2] != "HTTP/1.1":
        return None, "request line must be GET <target> HTTP/1.1"
    hdrs = {}
    for l in lines[1:]:
        if ":" not in l:
            return None, "malformed header line"
        k, v = l.split(":", 1)
        hdrs.setdefault(k.strip().lower(), v.strip())
    if hdrs.get("host") != f"127.0.0.1:{port}":
        return None, "Host header must be 127.0.0.1:<port>"
    if hdrs.get("upgrade", "").lower() != "websocket":
        return None, "Upgrade header must be websocket"
    if "upgrade" not in [t.strip().lower() for t in hdrs.get("connection", "").split(",")]:
        return None, "Connection header must contain Upgrade"
    if hdrs.get("sec-websocket-version") != "13":
        return None, "Sec-WebSocket-Version must be 13"
    key = hdrs.get("sec-websocket-key", "")
    try:
        ok = len(key) == 24 and len(base64.b64decode(key, validate=True)) == 16
    except Exception:
        ok = False
    if not ok:
        return None, "Sec-WebSocket-Key must be base64 of 16 bytes"
    if "content-length" in hdrs or "transfer-encoding" in hdrs:
        return None, "the upgrade request must not have a body"
    return (parts[1], key), None


def response_101(sp, accept):
    style = sp["hdr_style"]
    if style == 0:
        h = ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Accept: {accept}"]
    elif style == 1:
        h = ["HTTP/1.1 101 Switching Protocols", "server: cb2-ws/1.0", "connection: upgrade",
             f"sec-websocket-accept: {accept}", "upgrade: WebSocket"]
    else:
        h = ["HTTP/1.1 101 Web Socket Protocol Handshake", "UPGRADE: websocket", "X-Trace: a:b:c",
             f"SEC-WEBSOCKET-ACCEPT: {accept}", "CONNECTION: Upgrade"]
    return ("\r\n".join(h) + "\r\n\r\n").encode()


def wrong_accept(kind, key):
    if kind == 0:
        return base64.b64encode(hashlib.sha1(key.encode()).digest()).decode()
    if kind == 1:
        return base64.b64encode(hashlib.sha1((key + GUID.lower()).encode()).digest()).decode()
    return accept_for(base64.b64encode(b"\x01" * 16).decode())


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(READ_TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        c = Conn(sock)
        try:
            head = c.read_headers()
        except (EOFError, ProtoErr, OSError):
            return
        port = self.server.server_address[1]
        res, why = check_request(head, port)
        if why:
            bad_request(sock, why)
            return
        target, key = res
        path, _, query = target.partition("?")
        seg = path.split("/")
        sp = None
        if len(seg) == 4 and seg[0] == "" and seg[1] == "c" and seg[2].isdigit() and int(seg[2]) < 100:
            sp = spec(int(seg[2]))
        kind = None
        if sp:
            for k, n in sp["sessions"]:
                if n == seg[3]:
                    kind = k
        if kind is None:
            sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return
        if kind == "status":
            code, phrase = sp["status"]
            sock.sendall(f"HTTP/1.1 {code} {phrase}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            return
        if kind == "badaccept":
            resp = response_101(sp, wrong_accept(sp["bad_accept_kind"], key))
            sock.sendall(resp + frame(1, b"ADD 1 2"))
            try:  # a client that ignores the accept check would reply; give it a close it would print
                c.read_client_frame()
                sock.sendall(frame(8, (1002).to_bytes(2, "big") + b"accepted a bad Sec-WebSocket-Accept"))
            except Exception:
                pass
            return
        if query != f"token={sp['token']}":
            bad_request(sock, "wrong request-target (path and query must be sent exactly as in the URL)")
            return
        self.run_main(sock, c, sp, response_101(sp, accept_for(key)))

    def run_main(self, sock, c, sp, prefix):
        pending = deque()
        out = prefix
        n_cmd = 0

        def flush():
            nonlocal out
            if out:
                sock.sendall(out)
                out = b""

        try:
            for item in sp["script"]:
                k = item[0]
                if k == "send":
                    out += item[1]
                elif k == "ping":
                    pending.append(item[1])
                    out += frame(9, item[1])
                elif k == "slow":
                    flush()
                    data = item[1]
                    for i in range(0, len(data), 9000):
                        sock.sendall(data[i:i + 9000])
                        time.sleep(0.003)
                elif k == "expect":
                    flush()
                    n_cmd += 1
                    c.waiting = f"the reply to command {n_cmd}" + (" (and a pong)" if pending else "")
                    while True:
                        op, payload = c.read_client_frame()
                        if op == 10:
                            if not pending:
                                raise ProtoErr(1002, "pong received but no ping is outstanding")
                            if payload != pending[0]:
                                raise ProtoErr(1002, "pong payload does not match the ping payload")
                            pending.popleft()
                            continue
                        if op == 8:
                            raise ProtoErr(1002, "unexpected close frame from client")
                        if pending:
                            raise ProtoErr(1002, "reply sent before answering a ping with a pong")
                        try:
                            txt = payload.decode("utf-8")
                        except UnicodeDecodeError:
                            raise ProtoErr(1007, "invalid UTF-8 in a text frame")
                        if txt != item[1]:
                            raise ProtoErr(1008, f"wrong reply to command {n_cmd}")
                        break
                elif k == "bye":
                    out += frame(1, b"BYE")
                    flush()
                    c.waiting = "the close frame after BYE"
                    op, payload = c.read_client_frame()
                    if op == 1:
                        raise ProtoErr(1002, "text frame received after BYE (expected a close frame)")
                    if op == 10:
                        raise ProtoErr(1002, "pong received but no ping is outstanding")
                    if payload != b"\x03\xe8":
                        raise ProtoErr(1002, "close after BYE must have status 1000 and no reason")
                    sock.sendall(frame(8, item[1].to_bytes(2, "big") + item[2].encode()))
                    time.sleep(0.05)
                    return
        except ProtoErr as e:
            try:
                flush()
            except OSError:
                return
            try:
                sock.sendall(frame(8, e.code.to_bytes(2, "big") + e.reason.encode()[:123]))
                # keep reading (and discarding) until the client closes or 2 s pass, so that a client
                # still writing does not hit a reset before it has read the close frame
                sock.settimeout(0.5)
                end = time.time() + 2
                while time.time() < end:
                    try:
                        if not sock.recv(65536):
                            break
                    except socket.timeout:
                        continue
            except Exception:
                pass
        except (EOFError, OSError):
            return


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class Challenge(H.Challenge):
    name = "websocket_client"
    title = "RFC 6455 WebSocket client: handshake, masking, fragmentation, ping/pong, close"
    category = "api"

    def start(self, ctx):
        self.srv = Server(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        sp = spec(seed)
        urls = [f"ws://127.0.0.1:{self.port}{p}" for p in case_paths(sp)]
        return H.Case(args=urls, expected=expected_output(sp))
