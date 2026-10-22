import base64
import os
import struct
import sys

d = sys.argv[1]
src = open(os.path.join(d, "schema.proto"), encoding="utf-8").read()
data = open(os.path.join(d, "messages.bin"), "rb").read()


# ── tokenizer ────────────────────────────────────────────────────────────────
def tokenize(s):
    toks, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
        elif s.startswith("/*", i):
            i = s.index("*/", i + 2) + 2
        elif c in "\"'":
            j = s.index(c, i + 1)
            toks.append(s[i:j + 1])
            i = j + 1
        elif c.isalnum() or c in "_.-+":
            j = i
            while j < n and (s[j].isalnum() or s[j] in "_.-+"):
                j += 1
            toks.append(s[i:j])
            i = j
        else:
            toks.append(c)
            i += 1
    return toks


class Field:
    def __init__(self, name, typ, num, label="", oneof=None, key=None):
        self.name, self.typ, self.num, self.label, self.oneof, self.key = name, typ, num, label, oneof, key


messages, enums, top = {}, {}, []
T = tokenize(src)
pos = 0


def peek():
    return T[pos] if pos < len(T) else None


def take(expect=None):
    global pos
    t = T[pos]
    pos += 1
    if expect is not None and t != expect:
        raise SyntaxError(f"expected {expect} got {t}")
    return t


def skip_statement():
    # skip to ';' at depth 0 (option/reserved/syntax/package)
    depth = 0
    while True:
        t = take()
        if t in "{[(" and len(t) == 1:
            depth += 1
        elif t in "}])" and len(t) == 1:
            depth -= 1
        elif t == ";" and depth == 0:
            return


def skip_field_options():
    if peek() == "[":
        depth = 0
        while True:
            t = take()
            if t == "[":
                depth += 1
            elif t == "]":
                depth -= 1
                if depth == 0:
                    return


def parse_enum():
    take("enum")
    name = take()
    take("{")
    vals = {}
    while peek() != "}":
        t = peek()
        if t in ("option", "reserved"):
            skip_statement()
            continue
        if t == ";":
            take()
            continue
        vname = take()
        take("=")
        v = take()
        if v == "-":
            v = "-" + take()
        skip_field_options()
        take(";")
        vals.setdefault(int(v, 0), vname)
    take("}")
    enums[name] = vals


def parse_field(fields, oneof=None):
    label = ""
    if peek() in ("repeated", "optional"):
        label = take()
    if peek() == "map":
        take()
        take("<")
        kt = take()
        take(",")
        vt = take()
        take(">")
        name = take()
        take("=")
        num = int(take(), 0)
        skip_field_options()
        take(";")
        fields.append(Field(name, vt, num, key=kt))
        return
    typ = take()
    name = take()
    take("=")
    num = int(take(), 0)
    skip_field_options()
    take(";")
    fields.append(Field(name, typ, num, label, oneof))


def parse_message():
    take("message")
    name = take()
    take("{")
    fields = []
    messages[name] = fields
    while peek() != "}":
        t = peek()
        if t == "message":
            parse_message()
        elif t == "enum":
            parse_enum()
        elif t in ("option", "reserved"):
            skip_statement()
        elif t == ";":
            take()
        elif t == "oneof":
            take()
            oname = take()
            take("{")
            while peek() != "}":
                if peek() == "option":
                    skip_statement()
                elif peek() == ";":
                    take()
                else:
                    parse_field(fields, oneof=(name, oname))
            take("}")
        else:
            parse_field(fields)
    take("}")
    return name


while pos < len(T):
    t = peek()
    if t == "message":
        top.append(parse_message())
    elif t == "enum":
        parse_enum()
    elif t == ";":
        take()
    else:
        skip_statement()
ROOT = top[0]

# ── wire decoding ────────────────────────────────────────────────────────────
M64 = (1 << 64) - 1
VARINT = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool"}
I64 = {"int64", "uint64", "sint64", "fixed64", "sfixed64"}


def kind_of(typ):
    if typ in messages:
        return "message"
    if typ in enums:
        return "enum"
    return typ


def varint(b, p):
    r = sh = 0
    while True:
        c = b[p]
        p += 1
        r |= (c & 0x7F) << sh
        sh += 7
        if c < 0x80:
            return r & M64, p


def signed(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def skip(b, p, wt, fn):
    if wt == 0:
        return varint(b, p)[1]
    if wt == 1:
        return p + 8
    if wt == 5:
        return p + 4
    if wt == 2:
        n, p = varint(b, p)
        return p + n
    if wt == 3:
        while True:
            k, p = varint(b, p)
            if k & 7 == 4 and k >> 3 == fn:
                return p
            p = skip(b, p, k & 7, k >> 3)
    raise ValueError("bad wire type %d" % wt)


def scalar(kind, b, p):
    if kind in VARINT or kind == "enum":
        v, p = varint(b, p)
        if kind in ("int32", "enum"):
            v = signed(v, 32)
        elif kind == "uint32":
            v &= 0xFFFFFFFF
        elif kind == "int64":
            v = signed(v, 64)
        elif kind == "sint32":
            v = signed((v >> 1) ^ -(v & 1), 32)
        elif kind == "sint64":
            v = signed((v >> 1) ^ -(v & 1), 64)
        elif kind == "bool":
            v = v != 0
        return v, p
    if kind in ("fixed32", "sfixed32"):
        v = struct.unpack_from("<I" if kind == "fixed32" else "<i", b, p)[0]
        return v, p + 4
    if kind in ("fixed64", "sfixed64"):
        v = struct.unpack_from("<Q" if kind == "fixed64" else "<q", b, p)[0]
        return v, p + 8
    n, p = varint(b, p)
    raw = bytes(b[p:p + n])
    return (raw.decode("utf-8") if kind == "string" else raw), p + n


def default(kind):
    if kind == "string":
        return ""
    if kind == "bytes":
        return b""
    if kind == "bool":
        return False
    return 0


def decode(mname, b, msg=None):
    msg = {} if msg is None else msg
    fields = messages[mname]
    bynum = {f.num: f for f in fields}
    p = 0
    while p < len(b):
        k, p = varint(b, p)
        fn, wt = k >> 3, k & 7
        f = bynum.get(fn)
        if f is None:
            p = skip(b, p, wt, fn)
            continue
        kind = kind_of(f.typ)
        if f.key is not None:
            n, p = varint(b, p)
            entry, p = b[p:p + n], p + n
            kv, vv, q = None, None, 0
            while q < len(entry):
                ek, q = varint(entry, q)
                if ek >> 3 == 1:
                    kv, q = scalar(f.key, entry, q)
                elif ek >> 3 == 2:
                    if kind == "message":
                        n2, q = varint(entry, q)
                        vv = decode(f.typ, entry[q:q + n2], vv)
                        q += n2
                    else:
                        vv, q = scalar(kind, entry, q)
                else:
                    q = skip(entry, q, ek & 7, ek >> 3)
            if kv is None:
                kv = default(f.key)
            if vv is None:
                vv = {} if kind == "message" else default(kind)
            msg.setdefault(fn, {})[kv] = vv
        elif f.label == "repeated":
            lst = msg.setdefault(fn, [])
            if kind == "message":
                n, p = varint(b, p)
                lst.append(decode(f.typ, b[p:p + n]))
                p += n
            elif kind in ("string", "bytes"):
                v, p = scalar(kind, b, p)
                lst.append(v)
            elif wt == 2:
                n, p = varint(b, p)
                end = p + n
                while p < end:
                    v, p = scalar(kind, b, p)
                    lst.append(v)
            else:
                v, p = scalar(kind, b, p)
                lst.append(v)
        else:
            if f.oneof is not None:
                for g in fields:
                    if g.oneof == f.oneof and g.num != fn:
                        msg.pop(g.num, None)
            if kind == "message":
                n, p = varint(b, p)
                msg[fn] = decode(f.typ, b[p:p + n], msg.get(fn))
                p += n
            else:
                msg[fn], p = scalar(kind, b, p)
    return msg


# ── JSON output ──────────────────────────────────────────────────────────────
ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def js(s):
    out = []
    for ch in s:
        if ch in ESC:
            out.append(ESC[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def camel(n):
    r, up = "", False
    for ch in n:
        if ch == "_":
            up = True
        else:
            r += ch.upper() if up and "a" <= ch <= "z" else ch
            up = False
    return r


def value(typ, v):
    kind = kind_of(typ)
    if kind == "message":
        return obj(typ, v)
    if kind == "enum":
        return js(enums[typ][v]) if v in enums[typ] else str(v)
    if kind == "string":
        return js(v)
    if kind == "bytes":
        return '"' + base64.b64encode(v).decode() + '"'
    if kind == "bool":
        return "true" if v else "false"
    if kind in I64:
        return '"' + str(v) + '"'
    return str(v)


def obj(mname, msg):
    parts = []
    for f in sorted(messages[mname], key=lambda f: f.num):
        if f.num not in msg:
            continue
        v = msg[f.num]
        kind = kind_of(f.typ)
        if f.key is not None:
            if not v:
                continue
            keys = sorted(v) if f.key == "string" else sorted(v, key=int)
            s = "{" + ",".join(js(str(k)) + ":" + value(f.typ, v[k]) for k in keys) + "}"
        elif f.label == "repeated":
            if not v:
                continue
            s = "[" + ",".join(value(f.typ, x) for x in v) + "]"
        else:
            if (kind != "message" and f.oneof is None and f.label != "optional" and v == default(kind)
                    and type(v) is type(default(kind))):
                continue
            s = value(f.typ, v)
        parts.append(js(camel(f.name)) + ":" + s)
    return "{" + ",".join(parts) + "}"


out = []
p = 0
while p < len(data):
    n, p = varint(data, p)
    out.append(obj(ROOT, decode(ROOT, data[p:p + n])))
    p += n
sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
