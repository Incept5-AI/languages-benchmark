"""dns_resolver — a stub DNS resolver against a strict mock server (UDP + TCP on one port).

Hard parts: building a query the server accepts byte for byte, matching responses by ID (the server sends a
spoofed datagram with a wrong ID first for some names), name decompression including pointers that point at
pointers and pointers into the middle of earlier names, mixed-case names, skipping the question, authority
and additional sections, CNAME chains (also ending in NXDOMAIN), TC=1 → retry over TCP with a 2-byte length
prefix (the TCP answer arrives in several small pieces), TXT presentation escaping, null MX, RFC 3597
generic records, and RFC 5952 IPv6 text form.
"""
import random
import re
import socket
import socketserver
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

T_A, T_CNAME, T_SOA, T_MX, T_TXT, T_AAAA = 1, 5, 6, 15, 16, 28
TYPES = {"A": T_A, "AAAA": T_AAAA, "MX": T_MX, "TXT": T_TXT, "CNAME": T_CNAME}
TNAME = {v: k for k, v in TYPES.items()}
RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}

PROMPT = """\
Write a {LANG} program that is a stub DNS resolver: it sends DNS queries to a server and prints the answers.

The program is run with two arguments: a directory and a port number. The directory contains `queries.txt`,
one query per line: `<name> <TYPE>`, separated by one space, where `<TYPE>` is one of `A`, `AAAA`, `MX`, `TXT`,
`CNAME`. Names consist of labels of letters, digits and hyphens, may use any letter case and may end with a dot
(the root), e.g. `WWW.Example.test.`. Skip empty lines. A DNS server listens on 127.0.0.1 at the given port,
over UDP **and** TCP. Resolve the queries one at a time, in file order.

## Query

For each line send one standard query (RFC 1035) in a UDP datagram to 127.0.0.1:<port>:
- header: a random 16-bit ID (a new one per query); flags exactly 0x0100 (QR=0, OPCODE=0, RD=1, everything
  else 0); QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0 (no EDNS);
- one question: QNAME as a sequence of length-prefixed labels (the name's labels as written, without the
  trailing dot) ending with a zero byte, no compression; QTYPE (A=1, CNAME=5, MX=15, TXT=16, AAAA=28);
  QCLASS=1 (IN). Nothing after the question.

The server rejects anything else with FORMERR.

## Response

Read datagrams until one has QR=1 and the same ID as the query; **ignore datagrams with a different ID**
(the server sometimes sends such a decoy first). The server always answers, so you do not need to retransmit.

If the response has TC=1 (truncated), discard it and repeat the same query over TCP to the same address and
port: the message is prefixed by its length as a 16-bit big-endian integer in both directions, and the
response may arrive split across several reads. Use the TCP response instead.

Parse the header, skip the question section (QDCOUNT entries), and parse the answer section (ANCOUNT
records). Records in the authority and additional sections are never printed. Names anywhere in the message
may use compression (RFC 1035 §4.1.4): a byte with the top two bits set starts a 2-byte pointer to an earlier
offset in the message, where the name continues; the target may itself be or contain a pointer. Names in
responses may be in any letter case.

## Output

For each query print one line `<name> <TYPE> <RCODE>`, where `<name>` is the queried name in lower case
without a trailing dot, and `<RCODE>` is the response code: 0 `NOERROR`, 1 `FORMERR`, 2 `SERVFAIL`,
3 `NXDOMAIN`, 4 `NOTIMP`, 5 `REFUSED`, other values `RCODE<n>`. Then print every record of the answer
section, in the order received (whatever the RCODE, e.g. an NXDOMAIN response can carry CNAME records), as
```
  <owner> <TTL> <TYPE> <data>
```
(two leading spaces). Names (`<owner>` and names inside `<data>`) are printed in lower case without a trailing
dot; the root name itself is printed as `.`. `<TTL>` is the 32-bit TTL as an unsigned decimal. By type:
- A (1): dotted decimal, e.g. `192.0.2.7`.
- AAAA (28): RFC 5952 §4 text form: lower-case hex, leading zeros of each 16-bit group removed, the longest
  run of two or more consecutive all-zero groups replaced by `::` (the first such run if several are equally
  long; a single zero group is never replaced). No special form for IPv4-mapped addresses.
- CNAME (5): the target name.
- MX (15): `<preference> <exchange>` (an exchange of `.` is a "null MX").
- TXT (16): each character-string in the record, in order, separated by one space, each in double quotes;
  inside the quotes print `"` as `\\"`, `\\` as `\\\\`, bytes 0x20–0x7E otherwise as themselves, and every other
  byte as `\\` followed by its value as exactly three decimal digits (e.g. byte 0xC3 → `\\195`). An empty
  character-string prints as `""`.
- any other type: `TYPE<n>` instead of the type name, and as data `\\# <length>` followed, if the length is not
  zero, by a space and the RDATA as lower-case hex (RFC 3597), e.g. `  a.example 60 TYPE99 \\# 3 02686b`.

Example (the server's data differs between test cases):
```
www.zone.test A NOERROR
  www.zone.test 300 CNAME web.zone.test
  web.zone.test 60 A 192.0.2.7
  web.zone.test 60 A 192.0.2.8
v6.zone.test AAAA NOERROR
  v6.zone.test 3600 AAAA 2001:db8::1:0:0:1
nope.zone.test MX NXDOMAIN
txt.zone.test TXT NOERROR
  txt.zone.test 0 TXT "v=x1" "say \\"hi\\"" "caf\\195\\169" ""
```
"""

# ── zone data ────────────────────────────────────────────────────────────────


def ip6_text(b: bytes) -> str:
    """RFC 5952 §4 (oracle)."""
    g = [int.from_bytes(b[i:i + 2], "big") for i in range(0, 16, 2)]
    best, bl, i = -1, 0, 0
    while i < 8:
        if g[i] == 0:
            j = i
            while j < 8 and g[j] == 0:
                j += 1
            if j - i > bl and j - i >= 2:
                best, bl = i, j - i
            i = j
        else:
            i += 1
    hx = [format(x, "x") for x in g]
    if best < 0:
        return ":".join(hx)
    return ":".join(hx[:best]) + "::" + ":".join(hx[best + bl:])


def txt_text(strings) -> str:
    out = []
    for s in strings:
        t = ""
        for c in s:
            if c == 0x22:
                t += '\\"'
            elif c == 0x5C:
                t += "\\\\"
            elif 0x20 <= c <= 0x7E:
                t += chr(c)
            else:
                t += "\\%03d" % c
        out.append(f'"{t}"')
    return " ".join(out)


def mixcase(name: str, seed: int) -> str:
    r = random.Random(zlib.crc32(f"{seed}/{name}".encode()))
    return "".join(c.upper() if c.isalpha() and r.random() < 0.5 else c for c in name)


_Z = {}
_ZL = threading.Lock()


def zone(seed):
    with _ZL:
        if seed not in _Z:
            _Z[seed] = make_zone(seed)
        return _Z[seed]


def make_zone(seed):
    rng = random.Random(1035 + seed)
    apex = f"z{seed}.cb2.test"
    N = lambda s: f"{s}.{apex}" if s else apex  # noqa: E731
    lab = {k: v for k, v in zip(
        ["www", "web", "cdn", "edge", "mail", "mx1", "mx2", "txt", "big", "many", "nope", "dangling",
         "alias", "spoof", "broken", "meta", "nomail", "v6"],
        rng.sample(["www", "web", "cdn", "edge", "mail", "mx1", "mx2", "txt", "big", "many", "nope",
                    "dangling", "alias", "spoof", "broken", "meta", "nomail", "v6", "api", "img", "ftp",
                    "ns", "smtp", "files", "news", "shop", "blog", "dev"], 18))}
    # avoid accidental clashes like "mail" mapping to a label already used
    names = {k: N(v) for k, v in lab.items()}
    ttl = lambda: rng.choice([0, 1, 60, 300, 3600, 86400, 604800, 2147483647, rng.randrange(1, 1 << 31)])  # noqa
    recs = {}      # name -> list of (rtype, ttl, data, answers_for_qtype)
    cname = {}     # name -> (ttl, target)

    def add(name, rtype, data, t=None, qtype=None):
        recs.setdefault(name, []).append((rtype, ttl() if t is None else t, data, qtype or rtype))

    def ip4():
        return bytes([rng.choice([192, 198, 203, 10]), rng.randrange(256), rng.randrange(256), rng.randrange(256)])

    def g():
        return rng.randrange(1, 0x10000)

    def small():
        return rng.choice([rng.randrange(1, 16), rng.randrange(16, 256), rng.randrange(256, 4096)])

    def ip6(groups):
        return b"".join(x.to_bytes(2, "big") for x in groups)

    # CNAME chain: www -> web -> cdn (A x3); dangling -> missing name
    cname[names["www"]] = (ttl(), names["web"])
    cname[names["web"]] = (ttl(), names["cdn"])
    t = ttl()
    for _ in range(3):
        add(names["cdn"], T_A, ip4(), t)
    add(names["cdn"], T_AAAA, ip6([0x2001, 0xdb8, 0, 0, 0, 0, 0, small()]), t)
    cname[names["dangling"]] = (ttl(), N("gone-" + str(rng.randrange(100))))
    cname[names["alias"]] = (ttl(), names["edge"])

    # AAAA variety
    v6 = [
        [0x2001, 0xdb8, 0, 0, g(), 0, 0, g()],                 # tie: first run
        [0x2001, 0xdb8, 0, 0, 0, g(), 0, 0],                   # longer run later? no: first longer
        [g(), 0, g(), g(), g(), g(), g(), g()],                # single zero: no ::
        [0] * 8,                                               # ::
        [0] * 7 + [small()],                                   # ::n
        [0xfe80] + [0] * 7,                                    # fe80::
        [small(), small(), 0, small(), 0, 0, 0, small()],      # longer run wins
        [0, 0, small(), 0, 0, 0, 0, 0],                        # trailing longest
        [g(), small(), small(), small(), small(), small(), small(), 0],
    ]
    rng.shuffle(v6)
    for x in v6:
        add(names["v6"], T_AAAA, ip6(x))
    add(names["edge"], T_AAAA, ip6([0x2001, 0xdb8, small(), 0, 0, 0, 0, 1]))
    add(names["edge"], T_A, ip4(), (1 << 32) - 1 - rng.randrange(1 << 30))   # TTL with the top bit set

    # MX with in-zone exchanges + additional A; null MX
    for pref, ex in sorted([(rng.choice([10, 20]), names["mx1"]), (rng.choice([5, 30]), names["mx2"]),
                            (65535, names["mx1"])], key=lambda _: rng.random()):
        add(names["mail"], T_MX, (pref, ex))
    add(names["mx1"], T_A, ip4())
    add(names["mx2"], T_A, ip4())
    add(names["nomail"], T_MX, (0, ""))

    # TXT: several strings with quotes, backslashes, UTF-8, control bytes, empty string
    add(names["txt"], T_TXT, [b"v=spf1 -all", "say \"hi\" \\ ok".encode(), "café ☕".encode(), b"",
                             bytes([7, 0x7f, 0x20, 0x7e])])
    add(names["txt"], T_TXT, [("k" + str(rng.randrange(10**6))).encode()])
    # meta: TXT plus generic types in a TXT answer
    add(names["meta"], T_TXT, [b"meta=1"], qtype=T_TXT)
    add(names["meta"], 99, b"\x0cv=spf1 ~all" + bytes([rng.randrange(256)]), qtype=T_TXT)
    add(names["meta"], 65280 + rng.randrange(200), b"", qtype=T_TXT)
    add(names["meta"], T_TXT, [b"meta=2", b"x" * rng.randrange(1, 40)], qtype=T_TXT)

    # truncation: big TXT and many AAAA
    for i in range(rng.randint(11, 14)):
        add(names["big"], T_TXT, [f"chunk-{i:02d}-".encode() + bytes(rng.choice(b"abcdefXYZ09 =;")
                                                                   for _ in range(rng.randint(40, 60)))])
    t = ttl()
    for i in range(rng.randint(30, 40)):
        add(names["many"], T_AAAA, ip6([0x2001, 0xdb8, rng.choice([0, 0, g()]), 0, 0, i // 3, 0, i]), t)

    add(names["spoof"], T_A, ip4())
    add(names["spoof"], T_A, ip4())
    add(names["broken"], T_A, ip4())
    add(apex, T_TXT, [b"apex"])

    queries = [("www", "A"), ("www", "AAAA"), ("v6", "AAAA"), ("mail", "MX"), ("nomail", "MX"),
               ("txt", "TXT"), ("big", "TXT"), ("many", "AAAA"), ("nope", "A"), ("dangling", "A"),
               ("alias", "CNAME"), ("alias", "AAAA"), ("web", "MX"), ("cdn", "TXT"), ("spoof", "A"), ("broken", "A"),
               ("meta", "TXT"), ("edge", "A")]
    rng.shuffle(queries)
    qlines = []
    for k, t in queries:
        nm = names.get(k, N(lab.get(k, k)))
        if k == "nope":
            nm = N("nx" + str(rng.randrange(1000)))
        w = mixcase(nm, seed + 7) if rng.random() < .4 else nm
        if rng.random() < .3:
            w += "."
        qlines.append((w, t))
    return dict(seed=seed, apex=apex, recs=recs, cname=cname, servfail={names["broken"]},
                spoof={names["spoof"]}, queries=qlines, big={names["big"], names["many"]})


def resolve(z, qname, qtype):
    """Logical answer: (rcode, answers[(owner, type, ttl, data)], authority, additional)."""
    soa = (z["apex"], T_SOA, 3600, ("ns." + z["apex"], "hostmaster." + z["apex"], 2026101401 + z["seed"]))
    chain, n, seen = [], qname, set()
    while True:
        if n in z["servfail"]:
            return 2, [], [], []
        if n in z["cname"]:
            t, target = z["cname"][n]
            chain.append((n, T_CNAME, t, target))
            if qtype == T_CNAME:
                return 0, chain, [], []
            if n in seen:
                return 2, [], [], []
            seen.add(n)
            n = target
            continue
        if n in z["recs"]:
            rrs = [(n, rt, t, d) for rt, t, d, q in z["recs"].get(n, []) if q == qtype]
            if rrs:
                add = []
                for _, rt, t, d in rrs:
                    if rt == T_MX and d[1]:
                        add += [(d[1], a, b, c) for a, b, c, q in z["recs"].get(d[1], [])
                                if a == T_A and (d[1], a, b, c) not in add]
                return 0, chain + rrs, [], add
            return 0, chain, [soa], []
        return 3, chain, [soa], []


def rdata_text(rt, d):
    if rt == T_A:
        return ".".join(str(x) for x in d)
    if rt == T_AAAA:
        return ip6_text(d)
    if rt == T_CNAME:
        return d
    if rt == T_MX:
        return f"{d[0]} {d[1] or '.'}"
    if rt == T_TXT:
        return txt_text(d)
    return f"\\# {len(d)}" + (f" {d.hex()}" if d else "")


def expected_output(z):
    out = []
    for w, t in z["queries"]:
        q = w.lower().rstrip(".")
        rc, ans, _, _ = resolve(z, q, TYPES[t])
        out.append(f"{q} {t} {RCODES[rc]}")
        for owner, rt, ttl, d in ans:
            out.append(f"  {owner} {ttl} {TNAME.get(rt, f'TYPE{rt}')} {rdata_text(rt, d)}")
    return "\n".join(out)


# ── wire encoding (server) ───────────────────────────────────────────────────
class Enc:
    def __init__(self, seed):
        self.buf = bytearray()
        self.names = {}   # lower-case label tuple -> (offset, pointer depth)
        self.seed = seed

    def u16(self, v):
        self.buf += struct.pack(">H", v)

    def u32(self, v):
        self.buf += struct.pack(">I", v)

    def name(self, name, mix=True):
        labels = [l for l in name.split(".") if l]
        if mix:
            labels = mixcase(".".join(labels), self.seed).split(".") if labels else []
        for i in range(len(labels) + 1):
            if i == len(labels):
                for j in range(i):
                    self._reg(labels[j:], len(self.buf))
                    self.buf += bytes([len(labels[j])]) + labels[j].encode()
                self.buf += b"\0"
                return
            key = tuple(l.lower() for l in labels[i:])
            if key in self.names:
                off, depth = self.names[key]
                start = len(self.buf)
                for j in range(i):
                    self._reg(labels[j:], len(self.buf))
                    self.buf += bytes([len(labels[j])]) + labels[j].encode()
                self.u16(0xC000 | off)
                if i == 0 and depth < 2:   # later uses of this name point at this pointer
                    self.names[key] = (start, depth + 1)
                return

    def _reg(self, labels, off):
        key = tuple(l.lower() for l in labels)
        if key not in self.names and off < 0x3FFF:
            self.names[key] = (off, 0)

    def rr(self, owner, rt, ttl, d):
        self.name(owner)
        self.u16(rt)
        self.u16(1)
        self.u32(ttl)
        at = len(self.buf)
        self.u16(0)
        if rt == T_A or rt == T_AAAA:
            self.buf += d
        elif rt == T_CNAME:
            self.name(d)
        elif rt == T_MX:
            self.u16(d[0])
            self.name(d[1])
        elif rt == T_TXT:
            for s in d:
                self.buf += bytes([len(s)]) + s
        elif rt == T_SOA:
            self.name(d[0])
            self.name(d[1])
            for v in (d[2], 7200, 900, 1209600, 300):
                self.u32(v)
        else:
            self.buf += d
        struct.pack_into(">H", self.buf, at, len(self.buf) - at - 2)


def encode(z, qid, qraw, qlabels, rcode, ans, auth, add, limit=None):
    """qraw: the raw question bytes as received (name+type+class), echoed exactly."""
    e = Enc(z["seed"])
    e.buf += b"\0" * 12
    e.buf += qraw
    for i in range(len(qlabels)):
        e._reg(qlabels[i:], 12 + sum(len(l) + 1 for l in qlabels[:i]))
    tc, n_an = 0, 0
    for r in ans:
        snap = (bytes(e.buf), dict(e.names))
        e.rr(*r)
        if limit and len(e.buf) > limit:
            e.buf, e.names = bytearray(snap[0]), snap[1]
            tc = 1
            break
        n_an += 1
    n_ns = n_ar = 0
    if not tc:
        for r in auth:
            e.rr(*r)
            n_ns += 1
        for r in add:
            e.rr(*r)
            n_ar += 1
        if limit and len(e.buf) > limit:
            raise AssertionError("additional data overflow")
    flags = 0x8000 | 0x0400 | (0x0200 if tc else 0) | 0x0100 | 0x0080 | rcode
    struct.pack_into(">HHHHHH", e.buf, 0, qid, flags, 1, n_an, n_ns, n_ar)
    return bytes(e.buf)


def formerr(qid):
    return struct.pack(">HHHHHH", qid, 0x8000 | 0x0100 | 1, 0, 0, 0, 0)


ZRE = re.compile(r"(?:^|\.)z(\d{1,4})\.cb2\.test$")


def answer(msg: bytes, udp: bool):
    """Validate a query; return a list of datagrams/messages to send (decoys first)."""
    if len(msg) < 12:
        return [formerr(struct.unpack(">H", msg[:2])[0])] if len(msg) >= 2 else []
    qid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", msg[:12])
    if flags != 0x0100 or qd != 1 or an or ns or ar:
        return [formerr(qid)]
    i, labels = 12, []
    while True:
        if i >= len(msg):
            return [formerr(qid)]
        n = msg[i]
        if n == 0:
            i += 1
            break
        if n > 63 or i + 1 + n > len(msg):
            return [formerr(qid)]
        lab = msg[i + 1:i + 1 + n]
        if not re.fullmatch(rb"[A-Za-z0-9-]+", lab):
            return [formerr(qid)]
        labels.append(lab.decode())
        i += 1 + n
    if i + 4 != len(msg) or not labels or i - 12 > 255:
        return [formerr(qid)]
    qtype, qclass = struct.unpack(">HH", msg[i:i + 4])
    if qclass != 1 or qtype not in TNAME:
        return [formerr(qid)]
    qname = ".".join(labels).lower()
    m = ZRE.search(qname)
    if not m:
        return [struct.pack(">HHHHHH", qid, 0x8105, 0, 0, 0, 0)]
    z = zone(int(m.group(1)))
    rc, ans, auth, add = resolve(z, qname, qtype)
    # owner names of answers equal to the question name are written as given in the question
    out = []
    if udp and qname in z["spoof"]:
        bogus = encode(z, qid ^ 0x5A5A, msg[12:], labels, 0, [(qname, T_A, 60, bytes([6, 6, 6, 6]))], [], [])
        out.append(bogus)
    out.append(encode(z, qid, msg[12:], labels, rc, ans, auth, add, limit=512 if udp else None))
    return out


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        for m in answer(data, True):
            sock.sendto(m, self.client_address)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        s.settimeout(5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        buf = b""
        try:
            while True:
                while len(buf) < 2:
                    d = s.recv(4096)
                    if not d:
                        return
                    buf += d
                n = struct.unpack(">H", buf[:2])[0]
                while len(buf) < 2 + n:
                    d = s.recv(4096)
                    if not d:
                        return
                    buf += d
                msg, buf = buf[2:2 + n], buf[2 + n:]
                for m in answer(msg, False):
                    framed = struct.pack(">H", len(m)) + m
                    # dribble: 1 byte, then a few pieces
                    pieces = [framed[:1], framed[1:40], framed[40:len(framed) // 2], framed[len(framed) // 2:]]
                    for p in pieces:
                        if p:
                            s.sendall(p)
                            time.sleep(0.02)
        except OSError:
            return


class UDPServer(socketserver.ThreadingUDPServer):
    daemon_threads = True


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class Challenge(H.Challenge):
    name = "dns_resolver"
    title = "Stub DNS resolver: UDP/TCP, compression, CNAME chains, TC retry, RFC 5952"
    category = "api"

    def start(self, ctx):
        for _ in range(50):
            u = UDPServer(("127.0.0.1", 0), UDPHandler)
            port = u.server_address[1]
            try:
                t = TCPServer(("127.0.0.1", port), TCPHandler)
            except OSError:
                u.server_close()
                continue
            break
        else:
            raise RuntimeError("could not bind UDP+TCP on one port")
        self.udp, self.tcp, self.port = u, t, port
        for srv in (u, t):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def stop(self):
        for srv in (self.udp, self.tcp):
            srv.shutdown()
            srv.server_close()

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        z = zone(seed)
        # sanity: the truncation names really overflow 512 bytes over UDP, nothing else does
        for w, t in z["queries"]:
            q = w.lower().rstrip(".")
            labels = q.split(".")
            raw = b"".join(bytes([len(l)]) + l.encode() for l in labels) + b"\0" + struct.pack(">HH", TYPES[t], 1)
            full = encode(z, 1, raw, labels, *resolve(z, q, TYPES[t]))
            assert (len(full) > 512) == (q in z["big"]), (q, len(full))
        d = ctx.data_dir(self, seed)
        (d / "queries.txt").write_text("".join(f"{w} {t}\n" + ("\n" if i == 4 else "")
                                               for i, (w, t) in enumerate(z["queries"])))
        return H.Case(args=[str(d), str(self.port)], expected=expected_output(z))
