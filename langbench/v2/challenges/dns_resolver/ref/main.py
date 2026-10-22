import os, random, socket, struct, sys

TYPES = {"A": 1, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28}
TN = {v: k for k, v in TYPES.items()}
RC = ["NOERROR", "FORMERR", "SERVFAIL", "NXDOMAIN", "NOTIMP", "REFUSED"]

d, port = sys.argv[1], int(sys.argv[2])
addr = ("127.0.0.1", port)
out = []


def read_name(m, i):
    labels, end, hops = [], None, 0
    while True:
        n = m[i]
        if n & 0xC0 == 0xC0:
            if end is None:
                end = i + 2
            i = ((n & 0x3F) << 8) | m[i + 1]
            hops += 1
            if hops > 100:
                raise ValueError("loop")
        elif n == 0:
            if end is None:
                end = i + 1
            return (".".join(labels).lower() or "."), end
        else:
            labels.append(m[i + 1:i + 1 + n].decode("latin-1"))
            i += 1 + n


def v6(b):
    g = [int.from_bytes(b[i:i + 2], "big") for i in range(0, 16, 2)]
    bs, bl = -1, 1
    i = 0
    while i < 8:
        j = i
        while j < 8 and g[j] == 0:
            j += 1
        if j - i > bl:
            bs, bl = i, j - i
        i = j + 1 if j == i else j
    h = ["%x" % x for x in g]
    if bs < 0:
        return ":".join(h)
    return ":".join(h[:bs]) + "::" + ":".join(h[bs + bl:])


def txt(r):
    parts, i = [], 0
    while i < len(r):
        n = r[i]
        s = r[i + 1:i + 1 + n]
        i += 1 + n
        t = ""
        for c in s:
            if c == 34:
                t += '\\"'
            elif c == 92:
                t += "\\\\"
            elif 32 <= c <= 126:
                t += chr(c)
            else:
                t += "\\%03d" % c
        parts.append('"' + t + '"')
    return " ".join(parts)


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        x = s.recv(n - len(b))
        if not x:
            raise EOFError
        b += x
    return b


def query(name, qt):
    qid = random.randrange(65536)
    labels = [l for l in name.rstrip(".").split(".")]
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(l)]) + l.encode() for l in labels) + b"\0" + struct.pack(">HH", qt, 1)
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.settimeout(5)
    u.sendto(q, addr)
    while True:
        m, _ = u.recvfrom(65535)
        if len(m) >= 12 and struct.unpack(">H", m[:2])[0] == qid and m[2] & 0x80:
            break
    u.close()
    if m[2] & 0x02:
        t = socket.create_connection(addr)
        t.sendall(struct.pack(">H", len(q)) + q)
        n = struct.unpack(">H", recv_exact(t, 2))[0]
        m = recv_exact(t, n)
        t.close()
    return m


for line in open(os.path.join(d, "queries.txt"), encoding="utf-8").read().split("\n"):
    line = line.strip()
    if not line:
        continue
    name, t = line.split(" ")
    m = query(name, TYPES[t])
    _, fl, qd, an, ns, ar = struct.unpack(">HHHHHH", m[:12])
    rc = fl & 0xF
    out.append(f"{name.lower().rstrip('.')} {t} {RC[rc] if rc < len(RC) else f'RCODE{rc}'}")
    i = 12
    for _ in range(qd):
        _, i = read_name(m, i)
        i += 4
    for _ in range(an):
        owner, i = read_name(m, i)
        rt, cl, ttl, rl = struct.unpack(">HHIH", m[i:i + 10])
        i += 10
        r = m[i:i + rl]
        if rt == 1:
            data = ".".join(str(x) for x in r)
        elif rt == 28:
            data = v6(r)
        elif rt == 5:
            data = read_name(m, i)[0]
        elif rt == 15:
            data = f"{struct.unpack('>H', r[:2])[0]} {read_name(m, i + 2)[0]}"
        elif rt == 16:
            data = txt(r)
        else:
            data = f"\\# {rl}" + (" " + r.hex() if rl else "")
        out.append(f"  {owner} {ttl} {TN.get(rt, f'TYPE{rt}')} {data}")
        i += rl
sys.stdout.buffer.write(("\n".join(out) + "\n").encode())
