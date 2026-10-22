import os
import struct
import sys
import zlib
from decimal import Decimal

DEBUG = os.environ.get("SQDEBUG")
path = os.path.join(sys.argv[1], "db.sqlite")
db = open(path, "rb").read()
PS = struct.unpack_from(">H", db, 16)[0]
if PS == 1:
    PS = 65536
U = PS - db[20]
stats = {"maxdepth": 0, "payloads": []}


def page(n):
    return db[(n - 1) * PS:n * PS]


def varint(b, p):
    v = 0
    for i in range(8):
        c = b[p + i]
        v = (v << 7) | (c & 0x7F)
        if c < 0x80:
            return v, p + i + 1
    v = (v << 8) | b[p + 8]
    return v, p + 9


def s64(v):
    return v - (1 << 64) if v >= 1 << 63 else v


def payload(pg, p, size):
    X = U - 35
    if size <= X:
        return pg[p:p + size]
    M = ((U - 12) * 32 // 255) - 23
    K = M + (size - M) % (U - 4)
    local = K if K <= X else M
    out = bytearray(pg[p:p + local])
    nxt = struct.unpack_from(">I", pg, p + local)[0]
    while len(out) < size:
        op = page(nxt)
        nxt = struct.unpack_from(">I", op, 0)[0]
        out += op[4:4 + min(U - 4, size - len(out))]
    return bytes(out)


def walk(n, out, depth=1):
    pg = page(n)
    h = 100 if n == 1 else 0
    typ = pg[h]
    ncell = struct.unpack_from(">H", pg, h + 3)[0]
    stats["maxdepth"] = max(stats["maxdepth"], depth)
    if typ == 0x05:
        ptrs = [struct.unpack_from(">H", pg, h + 12 + 2 * i)[0] for i in range(ncell)]
        for cp in ptrs:
            walk(struct.unpack_from(">I", pg, cp)[0], out, depth + 1)
        walk(struct.unpack_from(">I", pg, h + 8)[0], out, depth + 1)
    elif typ == 0x0D:
        for i in range(ncell):
            cp = struct.unpack_from(">H", pg, h + 8 + 2 * i)[0]
            size, p = varint(pg, cp)
            rowid, p = varint(pg, p)
            stats["payloads"].append(size)
            out.append((s64(rowid), record(payload(pg, p, size))))
    else:
        raise ValueError(f"page {n}: unexpected type {typ}")


def record(b):
    hs, p = varint(b, 0)
    types = []
    while p < hs:
        t, p = varint(b, p)
        types.append(t)
    vals, q = [], hs
    for t in types:
        if t == 0:
            vals.append(None)
        elif 1 <= t <= 6:
            n = [0, 1, 2, 3, 4, 6, 8][t]
            vals.append(int.from_bytes(b[q:q + n], "big", signed=True))
            q += n
        elif t == 7:
            vals.append(struct.unpack_from(">d", b, q)[0])
            q += 8
        elif t == 8:
            vals.append(0)
        elif t == 9:
            vals.append(1)
        elif t >= 12 and t % 2 == 0:
            n = (t - 12) // 2
            vals.append(bytes(b[q:q + n]))
            q += n
        elif t >= 13:
            n = (t - 13) // 2
            vals.append(b[q:q + n].decode("utf-8"))
            q += n
        else:
            raise ValueError(f"serial type {t}")
    return vals


def split_defs(sql):
    i = sql.index("(")
    depth, cur, parts = 0, "", []
    for ch in sql[i + 1:]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts]


def columns(sql):
    cols = []
    for d in split_defs(sql):
        if d.startswith('"'):
            j = d.index('"', 1)
            name, rest = d[1:j], d[j + 1:]
        else:
            toks = d.split()
            name, rest = toks[0], d[len(toks[0]):]
        words = rest.upper().split()
        alias = bool(words) and words[0] == "INTEGER" and "PRIMARY KEY" in " ".join(words)
        cols.append((name, alias))
    return cols


def real(x):
    s = format(Decimal(x), "f")
    if "." in s:
        s = s.rstrip("0")
    else:
        s += "."
    return s + "0" if s.endswith(".") else s


def text(s):
    b = s.encode("utf-8")
    if len(b) > 100:
        return f"<TEXT len={len(b)} crc32={zlib.crc32(b):08x}>"
    r = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            r.append("\\\\")
        elif ch == '"':
            r.append('\\"')
        elif ch == "\n":
            r.append("\\n")
        elif ch == "\r":
            r.append("\\r")
        elif ch == "\t":
            r.append("\\t")
        elif o < 0x20 or o == 0x7F:
            r.append("\\x%02X" % o)
        else:
            r.append(ch)
    return '"' + "".join(r) + '"'


def fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        raise TypeError
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return real(v)
    if isinstance(v, str):
        return text(v)
    if len(v) > 100:
        return f"<BLOB len={len(v)} crc32={zlib.crc32(v):08x}>"
    return "X'" + v.hex().upper() + "'"


schema = []
walk(1, schema)
if DEBUG:
    print("schema depth", stats["maxdepth"], file=sys.stderr)
tables = []
for _, r in schema:
    r = r + [None] * (5 - len(r))
    if r[0] == "table" and not r[1].startswith("sqlite_"):
        tables.append((r[1], r[3], r[4]))
tables.sort(key=lambda t: t[0].encode())

blocks = []
for name, root, sql in tables:
    cols = columns(sql)
    rows = []
    stats["maxdepth"] = 0
    stats["payloads"] = []
    walk(root, rows)
    if DEBUG:
        print(name, "depth", stats["maxdepth"], "payloads", sorted(set(stats["payloads"]))[-20:], file=sys.stderr)
    rows.sort(key=lambda r: r[0])
    lines = [f"TABLE {name} rows={len(rows)}", "COLUMNS " + ", ".join(c for c, _ in cols)]
    for rowid, vals in rows:
        vals = vals + [None] * (len(cols) - len(vals))
        cells = [str(rowid) if alias else fmt(v) for (c, alias), v in zip(cols, vals)]
        lines.append(f"{rowid}: " + ", ".join(cells))
    blocks.append("\n".join(lines))
sys.stdout.buffer.write(("\n\n".join(blocks) + "\n").encode("utf-8"))
