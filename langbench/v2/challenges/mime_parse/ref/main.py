import base64
import os
import re
import sys
import zlib

W1252 = [0x20AC, 0x81, 0x201A, 0x192, 0x201E, 0x2026, 0x2020, 0x2021, 0x2C6, 0x2030, 0x160, 0x2039, 0x152, 0x8D,
         0x17D, 0x8F, 0x90, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x2DC, 0x2122, 0x161, 0x203A,
         0x153, 0x9D, 0x17E, 0x178]


def dec_cs(b, cs):
    cs = cs.lower()
    if cs == "iso-8859-1":
        return "".join(chr(x) for x in b)
    if cs == "windows-1252":
        return "".join(chr(W1252[x - 0x80]) if 0x80 <= x < 0xA0 else chr(x) for x in b)
    return b.decode("utf-8")


def quote(s):
    r = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            r.append("\\\\")
        elif ch == '"':
            r.append('\\"')
        elif ch == "\n":
            r.append("\\n")
        elif ch == "\t":
            r.append("\\t")
        elif o < 0x20 or 0x7F <= o <= 0x9F:
            r.append("\\u%04x" % o)
        else:
            r.append(ch)
    r.append('"')
    return "".join(r)


HEX = set("0123456789abcdefABCDEF")


def unhex(s, i):
    return len(s) >= i + 2 and s[i] in HEX and s[i + 1] in HEX


def rfc2047(s):
    pat = re.compile(r"=\?([^?\s]+)\?([BbQq])\?([^?\s]*)\?=")
    out = []
    groups = []           # pending run of adjacent words: [charset, bytearray]
    pos = 0
    last_end = None

    def flush():
        for cs, b in groups:
            out.append(dec_cs(bytes(b), cs))
        groups.clear()

    for m in pat.finditer(s):
        gap = s[pos:m.start()]
        if not (last_end is not None and last_end == pos and gap.strip(" \t") == ""):
            flush()
            out.append(gap)
        cs = m.group(1).split("*")[0].lower()
        t = m.group(3)
        if m.group(2) in "Bb":
            b = base64.b64decode(t)
        else:
            b = bytearray()
            i = 0
            while i < len(t):
                if t[i] == "_":
                    b.append(32)
                    i += 1
                elif t[i] == "=" and unhex(t, i + 1):
                    b.append(int(t[i + 1:i + 3], 16))
                    i += 3
                else:
                    b.append(ord(t[i]))
                    i += 1
        if groups and groups[-1][0] == cs:
            groups[-1][1].extend(b)
        else:
            groups.append([cs, bytearray(b)])
        pos = last_end = m.end()
    flush()
    out.append(s[pos:])
    return "".join(out)


def lines_of(b):
    res, i = [], 0
    while i < len(b):
        j = b.find(b"\n", i)
        if j < 0:
            res.append(b[i:])
            break
        res.append(b[i:j + 1])
        i = j + 1
    return res


def strip_eol(line):
    if line.endswith(b"\r\n"):
        return line[:-2], line[-2:]
    if line.endswith(b"\n"):
        return line[:-1], line[-1:]
    return line, b""


def split_entity(b):
    ls = lines_of(b)
    hdr = []
    i = 0
    while i < len(ls) and strip_eol(ls[i])[0] != b"":
        hdr.append(ls[i])
        i += 1
    body = b"".join(ls[i + 1:])
    raw = b"".join(hdr).decode("ascii")
    raw = re.sub(r"\r?\n([ \t])", r"\1", raw)
    h = {}
    for line in re.split(r"\r?\n", raw):
        if ":" in line:
            k, v = line.split(":", 1)
            h[k.strip().lower()] = v.strip(" \t")
    return h, body


def params(v):
    i = 0
    n = len(v)
    while i < n and v[i] not in "; \t":
        i += 1
    main = v[:i].lower()
    ps = []
    while i < n:
        while i < n and v[i] in "; \t":
            i += 1
        if i >= n:
            break
        j = i
        while j < n and v[j] not in "= \t;":
            j += 1
        name = v[i:j].lower()
        while j < n and v[j] in " \t":
            j += 1
        j += 1  # '='
        while j < n and v[j] in " \t":
            j += 1
        if j < n and v[j] == '"':
            j += 1
            val = []
            while v[j] != '"':
                if v[j] == "\\":
                    j += 1
                val.append(v[j])
                j += 1
            j += 1
            ps.append((name, "".join(val)))
        else:
            k = j
            while k < n and v[k] not in "; \t":
                k += 1
            ps.append((name, v[j:k]))
            j = k
        i = j
    return main, ps


def pct_bytes(s):
    out = bytearray()
    i = 0
    while i < len(s):
        if s[i] == "%" and unhex(s, i + 1):
            out.append(int(s[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(ord(s[i]))
            i += 1
    return bytes(out)


def rfc2231(ps, attr):
    for k, v in ps:
        if k == attr + "*":
            cs, _lang, val = v.split("'", 2)
            return dec_cs(pct_bytes(val), cs)
    segs = {}
    for k, v in ps:
        m = re.fullmatch(re.escape(attr) + r"\*([0-9]+)(\*?)", k)
        if m:
            segs[int(m.group(1))] = (m.group(2) == "*", v)
    if not segs:
        return None
    cs = "us-ascii"
    data = b""
    for n in sorted(segs):
        enc, v = segs[n]
        if enc:
            if n == 0:
                cs, _lang, v = v.split("'", 2)
            data += pct_bytes(v)
        else:
            data += v.encode("ascii")
    return dec_cs(data, cs)


def get_name(ctp, cdp):
    for ps, a in ((cdp, "filename"), (ctp, "name")):
        r = rfc2231(ps, a)
        if r is not None:
            return r
        for k, v in ps:
            if k == a:
                return rfc2047(v)
    return None


def qp(b):
    out = bytearray()
    for line in lines_of(b):
        content, eol = strip_eol(line)
        content = content.rstrip(b" \t")
        if content.endswith(b"="):
            content, eol = content[:-1], b""
        i = 0
        s = content
        while i < len(s):
            if s[i] == 0x3D and len(s) >= i + 3 and chr(s[i + 1]) in HEX and chr(s[i + 2]) in HEX:
                out.append(int(s[i + 1:i + 3], 16))
                i += 3
            else:
                out.append(s[i])
                i += 1
        out += eol
    return bytes(out)


def b64(b):
    alpha = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")
    keep = bytearray()
    for x in b:
        if x == 0x3D:
            break
        if x in alpha:
            keep.append(x)
    keep += b"=" * (-len(keep) % 4)
    return base64.b64decode(bytes(keep))


def parts_of(body, boundary):
    d = b"--" + boundary.encode()
    parts = []
    cur = None
    for line in lines_of(body):
        c, _ = strip_eol(line)
        if c.startswith(d):
            rest = c[len(d):]
            if rest.strip(b" \t") == b"" or (rest.startswith(b"--") and rest[2:].strip(b" \t") == b""):
                if cur is not None:
                    p = b"".join(cur)
                    parts.append(strip_eol(p)[0])
                if rest.startswith(b"--"):
                    return parts
                cur = []
                continue
        if cur is not None:
            cur.append(line)
    return parts


def show(b, depth, out, top):
    h, body = split_entity(b)
    ind = "  " * depth
    if top:
        for k in ("from", "subject"):
            out.append(k.capitalize() + ": " + (quote(rfc2047(h[k])) if k in h else "-"))
    ct, ctp = params(h["content-type"]) if "content-type" in h else ("text/plain", [])
    cd, cdp = params(h["content-disposition"]) if "content-disposition" in h else ("-", [])
    cte = h.get("content-transfer-encoding", "7bit").lower()
    if ct.startswith("multipart/"):
        ps = parts_of(body, dict(ctp)["boundary"])
        out.append(f"{ind}{ct} parts={len(ps)}")
        for p in ps:
            show(p, depth + 1, out, False)
        return
    if cte == "base64":
        data = b64(body)
    elif cte == "quoted-printable":
        data = qp(body)
    else:
        data = body
    if ct.startswith("text/") and cd != "attachment":
        cs = dict(ctp).get("charset", "us-ascii").lower()
        t = dec_cs(data, cs).replace("\r\n", "\n").replace("\r", "\n")
        out.append(f"{ind}{ct} charset={cs} cte={cte} chars={len(t)}")
        out.append(f"{ind}  {quote(t)}")
    else:
        nm = get_name(ctp, cdp)
        out.append(f"{ind}{ct} cte={cte} disposition={cd} name={quote(nm) if nm is not None else '-'} "
                   f"size={len(data)} crc32={zlib.crc32(data):08x}")


d = sys.argv[1]
blocks = []
for f in sorted((x for x in os.listdir(d) if x.endswith(".eml")), key=lambda x: x.encode()):
    out = ["== " + f]
    show(open(os.path.join(d, f), "rb").read(), 0, out, True)
    blocks.append("\n".join(out))
sys.stdout.buffer.write(("\n\n".join(blocks) + "\n").encode("utf-8"))
