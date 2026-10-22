"""mime_parse — parse MIME messages: nested multipart, QP/base64, charsets, RFC 2047 words, RFC 2231 params.

Hard parts: boundary look-alikes (an inner boundary that extends the outer one, body lines that start with the
delimiter, transport padding after a delimiter, a "ghost" part in the epilogue), the line break before a
delimiter belonging to the delimiter, a part with no header lines, LF-only files; quoted-printable soft breaks,
transport whitespace, `=20` at line end, lowercase hex, a stray `=`; iso-8859-1 is NOT windows-1252 (C1
controls must come out as escapes) and windows-1252 has undefined bytes; RFC 2047 words split mid-character
across adjacent words with different case charset labels, whitespace between adjacent words dropped but kept
next to plain text, `*lang` suffixes, a look-alike that is not an encoded-word; RFC 2231 continuations that are
out of order, more than ten (numeric sort), mixed encoded/literal segments, a multi-byte character split across
segments, and precedence over `filename`/`name`; RFC 2047 inside a `name` parameter.

The oracle below implements the rules as stated in the prompt; the tree structure, leaf payload bytes and
(where Python agrees with the stated rules) file names are cross-checked against Python's `email` package.
"""
import base64
import binascii
import email
import email.policy
import quopri
import random
import re
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

CP1252 = {0x80: 0x20AC, 0x82: 0x201A, 0x83: 0x0192, 0x84: 0x201E, 0x85: 0x2026, 0x86: 0x2020, 0x87: 0x2021,
          0x88: 0x02C6, 0x89: 0x2030, 0x8A: 0x0160, 0x8B: 0x2039, 0x8C: 0x0152, 0x8E: 0x017D, 0x91: 0x2018,
          0x92: 0x2019, 0x93: 0x201C, 0x94: 0x201D, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014, 0x98: 0x02DC,
          0x99: 0x2122, 0x9A: 0x0161, 0x9B: 0x203A, 0x9C: 0x0153, 0x9E: 0x017E, 0x9F: 0x0178}

TABLE = "\n".join("    " + "  ".join(f"{b:02X}→{CP1252[b]:04X}" if b in CP1252 else f"{b:02X}→{b:04X}"
                                    for b in range(r, r + 8)) for r in range(0x80, 0xA0, 8))

PROMPT = """\
Write a {LANG} program that parses MIME e-mail messages and prints each message's structure and decoded
content.

The program is run with one argument: a directory containing several `.eml` files. Process them in order of
file name (byte-wise ascending). Each file is one message. All header lines are ASCII. Within a file, every
line break is CRLF, or every line break is LF alone (both kinds of file occur); "line break" below means
whichever the file uses.

## Parsing

**Entities.** A message, and each body part of a multipart, is an entity: header lines, an empty line, then
the body. A part may have no header lines at all (it then starts with the empty line). Header lines are
unfolded by removing every line break that is immediately followed by a space or tab (the space or tab
stays). Each header is `Name: value`; names are case-insensitive; the value is the text after the colon with
leading and trailing spaces and tabs removed. No header occurs twice in an entity.

**Content-Type** is `type/subtype` followed by zero or more `; attribute=value` parameters, with optional
spaces and tabs around `/`, `;` and `=`. Type, subtype and attribute names are case-insensitive (print them in
lower case). A value is either a token (up to the next `;`, space or tab) or a quoted string (`"…"`, in which
`\\x` stands for `x`). There are no comments. If an entity has no Content-Type, it is `text/plain` with charset
`us-ascii`. The `charset` parameter of a text part defaults to `us-ascii`; print it in lower case.
**Content-Disposition** has the same syntax with a disposition type (`inline` or `attachment`,
case-insensitive) instead of `type/subtype`.

**Multipart.** For `multipart/*`, the `boundary` parameter gives the boundary `B`. A line is a delimiter if it
is exactly `--B` followed only by zero or more spaces/tabs, and a close delimiter if it is exactly `--B--`
followed only by zero or more spaces/tabs. Any other line (for example `--Bxyz` or `--B text`) is ordinary
content. Everything before the first delimiter (the preamble) and after the close delimiter (the epilogue)
is ignored, even if it looks like a part. Each part is the content between two consecutive delimiters, where
the line break that ends the last content line before a delimiter belongs to the delimiter, not to the part.
Parts may themselves be multipart. Boundaries are case-sensitive. The body of the message itself (when
it is not multipart) runs to the end of the file, including its final line break.

**Content-Transfer-Encoding** (case-insensitive; default `7bit`; ignored for multipart): `7bit`, `8bit`,
`binary` (bytes as they are), `base64` (ignore every character that is not in the base64 alphabet
`A–Z a–z 0–9 + /`, and stop at `=` padding) or `quoted-printable`, decoded as follows: first remove all spaces
and tabs at the end of each line; a line that then ends with `=` is joined to the next line with the `=` and
the line break removed (a soft line break); every other line break is kept; `=XY` with two hex digits (either
case) is the byte 0xXY; an `=` that is not followed by two hex digits is kept as a literal `=`. A soft line break never splits an `=XY` sequence.

**Charsets** (names case-insensitive): `us-ascii` and `utf-8` (all data is valid), `iso-8859-1` (every byte 0xXY is
the code point U+00XY, so 0x80–0x9F are the C1 control characters U+0080–U+009F) and
`windows-1252`, which is iso-8859-1 except for bytes 0x80–0x9F, which map as follows (bytes 81, 8D, 8F, 90
and 9D are undefined and map to the same code point, U+0081 etc.):
```
""" + TABLE + """
```

**Encoded-words (RFC 2047)** are decoded in the `From` and `Subject` header values and in the `name` and
`filename` parameter values described below. An encoded-word is any text matching
`=?charset?encoding?encoded-text?=` where charset is one or more characters other than `?`, space and tab,
encoding is `B`, `b`, `Q` or `q`, and encoded-text is zero or more characters other than `?`, space and tab;
take the leftmost match, then continue after it. The charset may end with `*language` (e.g. `utf-8*en`),
which is ignored. `B`: base64. `Q`: `_` is the byte 0x20, `=XY` (hex, either case) is the byte 0xXY, any
other character is its own byte. Two encoded-words are adjacent if only spaces and tabs (possibly none) lie
between them: that whitespace is removed. A maximal run of adjacent encoded-words with the same charset
(compared case-insensitively, without `*language`) is decoded by concatenating the bytes of all the words of
the run and decoding them together — a character may be split across words. All other text, including
whitespace next to ordinary text, is kept as it is. Text matching the pattern only partly (e.g. encoding `X`)
is ordinary text.

**File names (RFC 2231).** A part's name comes from the first of these that is present: the
Content-Disposition `filename` parameter in RFC 2231 form, the Content-Disposition `filename` parameter, the
Content-Type `name` parameter in RFC 2231 form, the Content-Type `name` parameter; otherwise there is none.
RFC 2231 form for an attribute `a` is either one parameter `a*=charset'language'value` or continuation
segments `a*0`, `a*1`, `a*2`, … (they may appear in any order and there may be more than ten; join them
in numeric order). A segment whose name ends with an extra `*` (`a*` or `a*0*`, `a*1*`, …) is
percent-encoded: `%XY` (hex, either case) is one byte and every other character is its own byte; the first
such segment begins with `charset'language'` (either may be empty — here the charset never is), and when
any segment is percent-encoded, segment 0 is. Segments without the extra `*` are literal (token or quoted
string). Concatenate the bytes of all segments, then decode them with the charset (us-ascii if there is
none). A value that is not in RFC 2231 form is decoded for RFC 2047 encoded-words.

## Output

For each file print:
```
== <file name>
From: <value>
Subject: <value>
<the tree>
```
with a blank line between files. `<value>` is the decoded header value as a quoted string (below), or `-` if
the header is absent. The tree has one line per entity, starting with the message itself, in depth-first
order, indented by two spaces per nesting level:
- multipart: `multipart/<subtype> parts=<number of parts>`, followed by its parts one level deeper;
- a text part — type `text/*` whose Content-Disposition type is not `attachment` — prints
  `text/<subtype> charset=<charset> cte=<encoding> chars=<N>` and then, indented one more level, the decoded
  text as a quoted string. The text is decoded with the transfer encoding and then the charset, and then
  every CRLF and every remaining lone CR is replaced by LF; `<N>` is its number of Unicode code points;
- any other part: `<type>/<subtype> cte=<encoding> disposition=<inline|attachment|-> name=<name> size=<S>
  crc32=<C>` where `<name>` is the decoded file name as a quoted string or `-`, `<S>` the number of bytes
  after transfer decoding and `<C>` the CRC-32 (as in zlib/PNG) of those bytes, as 8 lower-case hex digits.
  `disposition` is the Content-Disposition type in lower case, or `-` if there is none.

`<encoding>` is the Content-Transfer-Encoding in lower case (`7bit` if absent).

A quoted string is `"` + the text + `"` where `\\` is written `\\\\`, `"` is `\\"`, LF is `\\n`, tab is `\\t`, any
other code point below U+0020 or from U+007F to U+009F is `\\u` followed by 4 lower-case hex digits, and every
other character is written as itself (output is UTF-8).

Example output for one file:
```
== a.eml
From: "Zoë Ångström <zoe@example.test>"
Subject: "Re: “Q3” plans — €5"
multipart/mixed parts=2
  multipart/alternative parts=2
    text/plain charset=iso-8859-1 cte=quoted-printable chars=15
      "Hi\\tall,\\nsee \\u0093x\\u0094"
    text/html charset=us-ascii cte=7bit chars=15
      "<p>Hi all,</p>\\n"
  application/pdf cte=base64 disposition=attachment name="naïve plan.pdf" size=5120 crc32=0a1b2c3d
```
"""


# ── oracle ───────────────────────────────────────────────────────────────────
def decode_charset(b: bytes, cs: str) -> str:
    cs = cs.lower()
    if cs in ("utf-8", "us-ascii"):
        return b.decode("utf-8")
    if cs == "iso-8859-1":
        return b.decode("latin-1")
    if cs == "windows-1252":
        return "".join(chr(CP1252.get(x, x)) for x in b)
    raise ValueError(cs)


def esc(s: str) -> str:
    out = []
    for ch in s:
        c = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif c < 0x20 or 0x7F <= c <= 0x9F:
            out.append(f"\\u{c:04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


qp_src = {}
EW = re.compile(r"=\?([^?\s]+)\?([BbQq])\?([^?\s]*)\?=")


def ew_bytes(enc, text):
    if enc in "Bb":
        return base64.b64decode(text)
    out, i = bytearray(), 0
    while i < len(text):
        c = text[i]
        if c == "_":
            out.append(0x20)
        elif c == "=" and i + 2 < len(text) + 0 and re.fullmatch(r"[0-9A-Fa-f]{2}", text[i + 1:i + 3]):
            out.append(int(text[i + 1:i + 3], 16))
            i += 2
        else:
            out.append(ord(c))
        i += 1
    return bytes(out)


def decode_2047(s: str) -> str:
    out, pos, run = [], 0, []      # run: list of (charset, bytes)

    def flush():
        groups = []
        for cs, b in run:
            if groups and groups[-1][0] == cs:
                groups[-1][1] += b
            else:
                groups.append([cs, bytearray(b)])
        out.extend(decode_charset(bytes(b), cs) for cs, b in groups)
        run.clear()

    for m in EW.finditer(s):
        between = s[pos:m.start()]
        if run and re.fullmatch(r"[ \t]*", between):
            pass
        else:
            flush()
            out.append(between)
        run.append((m.group(1).split("*")[0].lower(), ew_bytes(m.group(2), m.group(3))))
        pos = m.end()
    flush()
    out.append(s[pos:])
    return "".join(out)


def split_lines(b: bytes):
    """Lines with their line breaks."""
    out = [l + b"\n" for l in b.split(b"\n")]
    out[-1] = out[-1][:-1]
    return [l for l in out if l]


def parse_headers(raw: bytes):
    lines = split_lines(raw)
    i, hl = 0, []
    while i < len(lines) and lines[i] not in (b"\r\n", b"\n"):
        hl.append(lines[i])
        i += 1
    body = b"".join(lines[i + 1:])
    text = b"".join(hl).decode("ascii")
    text = re.sub(r"\r?\n(?=[ \t])", "", text)
    hdrs = {}
    for line in text.splitlines():
        if not line:
            continue
        name, _, val = line.partition(":")
        hdrs[name.strip().lower()] = val.strip(" \t")
    return hdrs, body


def parse_params(v: str):
    m = re.match(r"\s*([^;\s]+)\s*", v)
    main = m.group(1).lower().replace(" ", "")
    rest, params = v[m.end():], []
    i = 0
    while i < len(rest):
        if rest[i] in "; \t":
            i += 1
            continue
        m = re.compile(r"([^=\s;]+)[ \t]*=[ \t]*").match(rest, i)
        name = m.group(1).lower()
        i = m.end()
        if i < len(rest) and rest[i] == '"':
            j, val = i + 1, []
            while rest[j] != '"':
                if rest[j] == "\\":
                    j += 1
                val.append(rest[j])
                j += 1
            params.append((name, "".join(val)))
            i = j + 1
        else:
            m = re.compile(r"[^;\s]*").match(rest, i)
            params.append((name, m.group(0)))
            i = m.end()
    return main, params


def pct(s):
    return re.sub(rb"%([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), s.encode("ascii"))


def param_2231(params, attr):
    d = dict(params)
    if attr + "*" in d:
        cs, _, rest = d[attr + "*"].split("'", 2)
        return decode_charset(pct(rest), cs)
    segs = {}
    for k, v in params:
        m = re.fullmatch(re.escape(attr) + r"\*(\d+)(\*?)", k)
        if m:
            segs[int(m.group(1))] = (v, bool(m.group(2)))
    if not segs:
        return None
    data, cs = b"", "us-ascii"
    for n in sorted(segs):
        v, enc = segs[n]
        if enc:
            if n == 0:
                cs, _, v = v.split("'", 2)
            data += pct(v)
        else:
            data += v.encode("ascii")
    return decode_charset(data, cs)


def file_name(ct_params, cd_params):
    for params, attr in ((cd_params, "filename"), (ct_params, "name")):
        v = param_2231(params, attr)
        if v is not None:
            return v
        if attr in dict(params):
            return decode_2047(dict(params)[attr])
    return None


def qp_decode(b: bytes) -> bytes:
    out = bytearray()
    for line in split_lines(b):
        brk = b""
        if line.endswith(b"\r\n"):
            line, brk = line[:-2], b"\r\n"
        elif line.endswith(b"\n"):
            line, brk = line[:-1], b"\n"
        line = line.rstrip(b" \t")
        if line.endswith(b"="):
            line, brk = line[:-1], b""
        i = 0
        while i < len(line):
            if line[i:i + 1] == b"=" and re.fullmatch(rb"[0-9A-Fa-f]{2}", line[i + 1:i + 3]):
                out.append(int(line[i + 1:i + 3], 16))
                i += 3
            else:
                out.append(line[i])
                i += 1
        out += brk
    return bytes(out)


def b64_decode(b: bytes) -> bytes:
    s = re.sub(rb"[^A-Za-z0-9+/=]", b"", b).split(b"=")[0]
    return base64.b64decode(s + b"=" * (-len(s) % 4))


def split_multipart(body: bytes, boundary: str):
    dl = ("--" + boundary).encode("ascii")
    parts, cur, started = [], None, False
    for line in split_lines(body):
        content = line.rstrip(b"\r\n")
        is_close = content.startswith(dl + b"--") and content[len(dl) + 2:].strip(b" \t") == b""
        is_delim = content.startswith(dl) and content[len(dl):].strip(b" \t") == b""
        if is_delim or is_close:
            if cur is not None:
                parts.append(cur)
            if is_close:
                break
            cur = []
            continue
        if cur is not None:
            cur.append(line)
    else:
        raise ValueError("no close delimiter")
    out = []
    for p in parts:
        raw = b"".join(p)
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        out.append(raw)
    return out


def walk(raw: bytes, depth: int, lines: list, leaves: list, top=False):
    hdrs, body = parse_headers(raw)
    ind = "  " * depth
    if "content-type" in hdrs:
        ctype, ct_params = parse_params(hdrs["content-type"])
    else:
        ctype, ct_params = "text/plain", []
    cd, cd_params = parse_params(hdrs["content-disposition"]) if "content-disposition" in hdrs else ("-", [])
    cte = hdrs.get("content-transfer-encoding", "7bit").lower()
    if top:
        for h in ("from", "subject"):
            lines.append(f"{h.capitalize()}: " + (esc(decode_2047(hdrs[h])) if h in hdrs else "-"))
    if ctype.startswith("multipart/"):
        parts = split_multipart(body, dict(ct_params)["boundary"])
        lines.append(f"{ind}{ctype} parts={len(parts)}")
        for p in parts:
            walk(p, depth + 1, lines, leaves)
        return
    data = {"base64": b64_decode, "quoted-printable": qp_decode}.get(cte, lambda x: x)(body)
    qp_src[id(data)] = body
    leaves.append((ctype, data, file_name(ct_params, cd_params), cte))
    if ctype.startswith("text/") and cd != "attachment":
        cs = dict(ct_params).get("charset", "us-ascii").lower()
        text = decode_charset(data, cs).replace("\r\n", "\n").replace("\r", "\n")
        lines.append(f"{ind}{ctype} charset={cs} cte={cte} chars={len(text)}")
        lines.append(f"{ind}  {esc(text)}")
    else:
        nm = file_name(ct_params, cd_params)
        lines.append(f"{ind}{ctype} cte={cte} disposition={cd} name={esc(nm) if nm is not None else '-'} "
                     f"size={len(data)} crc32={zlib.crc32(data):08x}")


def render(name: str, raw: bytes) -> str:
    lines, leaves = [f"== {name}"], []
    walk(raw, 0, lines, leaves, top=True)
    # cross-check against Python's email package (structure, payload bytes, names where comparable)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    py = [p for p in msg.walk() if not p.is_multipart()]
    assert [p.get_content_type() for p in py] == [l[0] for l in leaves], (name, [p.get_content_type() for p in py])
    for p, (ct, data, nm, cte) in zip(py, leaves):
        pd = p.get_payload(decode=True)
        if cte == "quoted-printable":
            assert quopri.decodestring(re.sub(rb"[ \t]+(?=\r?\n|$)", b"", qp_src[id(data)])) == data
        else:
            assert pd == data, (name, ct, pd[:60], data[:60])
        cdv = " ".join(v for k, v in p.raw_items() if k.lower() == "content-disposition")
        both = re.search(r"filename\*", cdv) and re.search(r"filename\s*=", cdv)   # Python prefers the plain one
        if nm is not None and not both:
            assert p.get_filename() == nm, (name, p.get_filename(), nm)
    return "\n".join(lines)


# ── generation ───────────────────────────────────────────────────────────────
WORDS = ["meeting", "budget", "plan", "review", "notes", "draft", "update", "invoice", "agenda", "report",
         "summary", "figures", "launch", "contract", "status"]
UNI = ["Zoë", "Ångström", "naïve", "café", "Grüße", "€5", "李雷", "😀", "Ωmega", "São Paulo", "Øresund",
       "façade", "Crème brûlée", "Dvořák", "Łódź"]
CP_ONLY = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
LATIN = "éèüößàçñÉ£¥©®°±µ¶·¿"


def cs_label(rng, cs):
    return rng.choice({"utf-8": ["utf-8", "UTF-8", "Utf-8"], "iso-8859-1": ["iso-8859-1", "ISO-8859-1"],
                       "windows-1252": ["windows-1252", "Windows-1252"]}[cs])


def enc_word(rng, cs, b: bytes, enc=None, lang=False):
    enc = enc or rng.choice("BQbq")
    label = cs_label(rng, cs) + ("*en" if lang else "")
    if enc in "Bb":
        t = base64.b64encode(b).decode()
    else:
        t = ""
        for x in b:
            if x == 0x20:
                t += "_"
            elif 48 <= x <= 57 or 65 <= x <= 90 or 97 <= x <= 122 or x in b"!*+-/":
                t += chr(x)
            else:
                t += f"={x:02X}" if rng.random() < .7 else f"={x:02x}"
    return f"=?{label}?{enc}?{t}?="


def ew_run(rng, cs, text, pieces=2, lang=False):
    """Encode `text` as `pieces` adjacent words, cutting the bytes at random (possibly mid-character)."""
    b = decode_safe_encode(text, cs)
    cuts = set(rng.sample(range(1, len(b)), min(pieces - 1, len(b) - 1)))
    mid = [i for i in range(1, len(b)) if cs == "utf-8" and 0x80 <= b[i] <= 0xBF]
    if mid and pieces > 1:
        cuts = set(list(cuts)[1:]) | {rng.choice(mid)}       # always split one character across words
    cuts = sorted(cuts)
    chunks = [b[i:j] for i, j in zip([0] + cuts, cuts + [len(b)])]
    words = [enc_word(rng, cs, c, lang=lang and k == 0) for k, c in enumerate(chunks)]
    out = words[0]
    for w in words[1:]:
        out += rng.choice([" ", "\r\n ", "\r\n\t", "", "  \t"]) + w
    return out


def decode_safe_encode(text, cs):
    if cs == "windows-1252":
        inv = {v: k for k, v in CP1252.items()}
        return bytes(inv[ord(c)] if ord(c) in inv else ord(c) for c in text)
    return text.encode("latin-1" if cs == "iso-8859-1" else cs)


def qp_encode(rng, data: bytes, eol: bytes, lower=False, stray=False) -> bytes:
    """Quoted-printable with narrow random wrapping, transport whitespace and optional stray '='."""
    out_lines = []
    lines = data.split(b"\n")
    for li, line in enumerate(lines):
        toks = []
        for k, x in enumerate(line):
            last = k == len(line) - 1
            if (33 <= x <= 126 and x != 61) or (x in (32, 9) and not last):
                toks.append(bytes([x]))
            else:
                h = f"={x:02X}"
                toks.append((h.lower() if lower and rng.random() < .5 else h).encode())
        if stray and li == 0:
            toks.insert(6, b"E=mc2 ")
        cur, width = b"", rng.randrange(18, 60)
        for t in toks:
            if len(cur) + len(t) > width:
                out_lines.append(cur + b"=" + rng.choice([b"", b"", b" ", b"\t "]))
                cur, width = b"", rng.randrange(18, 60)
            cur += t
        out_lines.append(cur + (rng.choice([b"", b"", b"  ", b"\t"]) if li < len(lines) - 1 else b""))
    return eol.join(out_lines)


def b64_lines(data: bytes, eol: bytes, width=76) -> bytes:
    s = base64.b64encode(data)
    return eol.join(s[i:i + width] for i in range(0, len(s), width))


def para(rng, n, extra=()):
    w = [rng.choice(WORDS + list(extra)) for _ in range(n)]
    return " ".join(w)


def rand_boundary(rng):
    return "=_" + "".join(rng.choice("abcdef0123456789") for _ in range(rng.randrange(8, 14)))


def file_newsletter(rng, eol=b"\r\n"):
    E = eol.decode()
    B1 = rand_boundary(rng)
    B2, B3 = B1 + "_alt", B1 + ".rel"          # both start with the outer boundary
    name = rng.choice(UNI[:6]) + " " + rng.choice(UNI[6:])
    subj_parts = [rng.choice(["Re:", "Fwd:", "Re: Fwd:"]) + "  " + rng.choice(WORDS),
                  ew_run(rng, "utf-8", rng.choice(UNI) + " " + rng.choice(UNI) + " " + rng.choice(CP_ONLY[15:22]),
                         rng.randrange(2, 4)),
                  ew_run(rng, "iso-8859-1", rng.choice(LATIN) + " " + rng.choice(WORDS), 1),
                  rng.choice(WORDS) + " (" + str(rng.randrange(1, 99)) + ")"]
    subject = subj_parts[0] + " " + subj_parts[1] + rng.choice([" ", "\r\n "]) + subj_parts[2] + \
        "\r\n " + subj_parts[3]
    frm = ew_run(rng, "utf-8", name, 2).replace("\r\n", E) + " <" + rng.choice(WORDS) + "@example.test>"
    text = (f"Hello {rng.choice(UNI)},\n\n{para(rng, 14)} {rng.choice(UNI)} = {rng.randrange(10, 99)}%\n"
            f"\tindented {rng.choice(LATIN)} line ends with space \n{para(rng, 8, UNI)}\n-- \nbye\n")
    html = (f"<html><body>\r\n<p>{para(rng, 10, UNI)}</p>\r\n<img src=\"cid:logo\">\r\n"
            f"<p>{rng.choice(CP_ONLY)} {rng.choice(UNI)}</p>\r\n</body></html>\r\n")
    png = bytes(rng.randrange(256) for _ in range(rng.randrange(300, 900)))
    pdf = b"%PDF-1.4\n" + bytes(rng.randrange(256) for _ in range(rng.randrange(1500, 4000)))
    # RFC 2231 file name: > 10 segments, shuffled, a multi-byte char split across two encoded segments
    fname = rng.choice(UNI[:6]) + " " + rng.choice(WORDS) + " " + rng.choice(["€", "“q”", "—"]) + " " + \
        rng.choice(WORDS) + "-" + rng.choice(UNI[6:]) + " final version.pdf"
    fb = fname.encode("utf-8")
    nseg = rng.randrange(11, 14)
    step = max(1, len(fb) // nseg)
    segs, i = [], 0
    for k in range(nseg):
        j = len(fb) if k == nseg - 1 else i + step
        segs.append(fb[i:j])
        i = j
    params = []
    for k, sb in enumerate(segs):
        literal_ok = all(32 < x < 127 and x not in b'%"\\;\'' for x in sb) and k > 0
        if literal_ok and rng.random() < .6:
            v = sb.decode()
            params.append(f'filename*{k}="{v}"' if rng.random() < .5 else f"filename*{k}={v}")
        else:
            pe = "".join(chr(x) if (48 <= x <= 57 or 65 <= x <= 90 or 97 <= x <= 122 or x in b".-")
                         else (f"%{x:02X}" if rng.random() < .7 else f"%{x:02x}") for x in sb)
            params.append(f"filename*{k}*=" + (("utf-8''" if k == 0 else "") + pe))
    first = params[0]
    rest = params[1:]
    rng.shuffle(rest)
    params = rest[:3] + [first] + rest[3:]
    cd = "attachment;" + ";".join(E + " " + p for p in params)
    pre = f"This is a multi-part message in MIME format.{E}--{B1}-preamble is not a delimiter{E}"
    s = (f"From: {frm}{E}To: team@example.test{E}Subject: {subject.replace(chr(13) + chr(10), E)}{E}"
         f"MIME-Version: 1.0{E}Content-Type: multipart/mixed;{E}\tboundary=\"{B1}\"{E}{E}{pre}"
         f"--{B1}{E}Content-Type: multipart/alternative; boundary=\"{B2}\"{E}{E}"
         f"--{B2}{E}Content-Type: text/plain; charset=\"utf-8\"; format=flowed{E}"
         f"Content-Transfer-Encoding: quoted-printable{E}{E}").encode()
    s += qp_encode(rng, text.encode("utf-8"), eol, lower=True, stray=True) + eol
    s += (f"--{B2}{E}Content-Type: multipart/related; boundary=\"{B3}\"; type=\"text/html\"{E}{E}"
          f"--{B3}{E}Content-Type: text/html; charset=UTF-8{E}Content-Transfer-Encoding: base64{E}{E}").encode()
    s += b64_lines(html.encode("utf-8"), eol) + eol
    s += (f"--{B3}{E}Content-Type: image/png; name=\"logo.png\"{E}Content-Transfer-Encoding: base64{E}"
          f"Content-ID: <logo>{E}Content-Disposition: inline; filename=\"logo-{rng.randrange(100)}.png\"{E}{E}").encode()
    s += b64_lines(png, eol) + eol
    s += (f"--{B3}--{E}--{B2}--{E}trailing text after the inner close delimiter{E}{E}"
          f"--{B1}{E}Content-Type: application/pdf; name=\"fallback.pdf\"{E}Content-Disposition: {cd}{E}"
          f"Content-Transfer-Encoding: base64{E}{E}").encode()
    s += b64_lines(pdf, eol) + eol
    s += f"--{B1}--{E}epilogue{E}".encode()
    return s


def file_cp1252(rng, eol=b"\r\n"):
    E = eol.decode()
    body = (f"Dear {rng.choice(['Mr', 'Ms', 'Dr'])} {rng.choice(['Smith', 'Jones', 'Okafor'])},\n\n"
            f"{rng.choice(CP_ONLY)}{para(rng, 6)}{rng.choice(CP_ONLY)} {rng.choice(LATIN)}{rng.choice(LATIN)} "
            f"costs {rng.choice(['€', '£'])}{rng.randrange(10, 999)}.{rng.randrange(10, 99)} {rng.choice(CP_ONLY)}\n"
            f"{para(rng, 9)} \u2026 \u201cquoted\u201d \u2013 done\nBack\\slash and \"dq\"\n")
    data = decode_safe_encode(body, "windows-1252").replace(b"done", b"done\x81")    # an undefined byte
    subj = (ew_run(rng, "iso-8859-1", f"Price {rng.choice(LATIN)} {rng.choice(['£', '¥'])}{rng.randrange(5, 50)}", 1)
            + rng.choice(["  ", " "]) + "and" + "\r\n " +
            ew_run(rng, "windows-1252", f"{rng.choice(CP_ONLY)} {rng.choice(WORDS)} {rng.choice(CP_ONLY)}", 2))
    who = rng.choice(["Smith, John", "O'Brien, Ann", "Okafor, Ada"])
    s = (f"Subject: {subj.replace(chr(13) + chr(10), E)}{E}From: \"{who}\" <{who.split(',')[0].lower()}@example.test>"
         f"{E}MIME-Version: 1.0{E}Content-Type: text/plain; charset=windows-1252{E}"
         f"Content-Transfer-Encoding: Quoted-Printable{E}{E}").encode()
    return s + qp_encode(rng, data, eol) + eol


def file_latin1_lf(rng, eol=b"\n"):
    E = eol.decode()
    B = rng.choice(["simple boundary", "next part", "part separator"]) + f" {rng.randrange(10, 99)}"
    t1 = f"Implicitly typed plain ASCII text {rng.randrange(1000)}.\nSecond line.\n"
    smart = b"\x93" + rng.choice(WORDS).encode() + b"\x94"
    t2 = (rng.choice(LATIN).encode("latin-1") + b" " + para(rng, 5).encode() + b" " + smart + b"\n" +
          f"--{B} is not a delimiter\n--{B}--x neither\n".encode() + para(rng, 4).encode() +
          b" \xe9t\xe9 \xfcber\n")
    att_text = (para(rng, 12) + "\r\n" + para(rng, 7) + "\r\n").encode()
    blob = bytes(rng.randrange(256) for _ in range(rng.randrange(200, 700)))
    nm = f"r{rng.choice(LATIN)}sum{rng.choice(LATIN)}"
    name_ew = enc_word(rng, "iso-8859-1", nm.encode("latin-1"), enc="Q")
    fn = f"{rng.choice(LATIN)}t{rng.choice(LATIN)} ({rng.choice(WORDS)}).bin"
    fn_pe = "".join(chr(x) if (48 <= x <= 57 or 65 <= x <= 90 or 97 <= x <= 122 or x in b".")
                    else rng.choice([f"%{x:02X}", f"%{x:02x}"]) for x in fn.encode("latin-1"))
    s = (f"CONTENT-TYPE: Multipart/Mixed; BOUNDARY=\"{B}\"{E}MIME-Version: 1.0{E}"
         f"subject: plain {rng.choice(WORDS)} =?utf-8?X?not-encoded?= =?broken {E}\tcontinued{E}{E}"
         f"--{B}{E}{E}").encode() + t1.encode() + \
        (f"--{B}{E}Content-Type: TEXT/Plain; CHARSET=ISO-8859-1{E}Content-Transfer-Encoding: 8BIT{E}{E}").encode() + \
        t2 + \
        (f"--{B}{E}Content-Type: text/plain; charset=us-ascii; name=\"{name_ew} {rng.choice(WORDS)}.txt\"{E}"
         f"Content-Disposition: ATTACHMENT{E}Content-Transfer-Encoding: base64{E}{E}").encode() + \
        b64_lines(att_text, eol, 64) + eol + \
        (f"--{B} \t{E}Content-Type: application/octet-stream{E}"
         f"Content-Disposition: attachment; filename=\"fallback.bin\";{E} filename*=iso-8859-1'fr'{fn_pe}{E}"
         f"Content-Transfer-Encoding: Base64{E}{E}").encode() + \
        b64_lines(blob, eol, 60) + eol + \
        (f"--{B}--{E}--{B}{E}Content-Type: text/plain{E}{E}ghost part in the epilogue{E}").encode()
    return s


def file_alt_b64(rng, eol=b"\r\n"):
    E = eol.decode()
    B = "alt-" + "".join(rng.choice("0123456789abcdef") for _ in range(10))
    plain = (f"{rng.choice(UNI)} {para(rng, 6)}\r\n\t{rng.choice(UNI)} 😀 {rng.choice(['李雷', '東京', '서울'])}\r\n"
             f"old mac line\r{para(rng, 3)}\r\n\r\n").encode("utf-8")
    html = f"<p>{para(rng, 5)} &amp; more</p>{E}{E}<p>end</p>{E}{E}"
    subj = (f"Re:{E}\t{rng.choice(WORDS)} " + enc_word(rng, "utf-8", f"caf{rng.choice('éè')}".encode(), lang=True) +
            " =?utf-8?X?abc?= " + enc_word(rng, "utf-8", rng.choice(UNI).encode()) + " " +
            enc_word(rng, "iso-8859-1", rng.choice(LATIN).encode("latin-1")) + f" \"{rng.choice(WORDS)}\"")
    s = (f"Subject: {subj}{E}MIME-Version: 1.0{E}Content-Type: multipart/alternative; boundary={B}{E}{E}"
         f"--{B}{E}Content-Type: text/plain;{E}  charset = \"utf-8\" ; format=flowed{E}"
         f"Content-Transfer-Encoding: BASE64{E}{E}").encode()
    s += b64_lines(plain, eol, rng.choice([64, 72, 76])) + eol
    s += (f"--{B}{E}Content-Type: text/html; charset=\"US-ASCII\"{E}{E}{html}--{B}--{E}").encode()
    return s


def make_case(seed: int):
    rng = random.Random(16160 + seed)
    gens = [file_newsletter, file_cp1252, file_latin1_lf, file_alt_b64]
    names = sorted({f"{rng.choice(['msg', 'mail', 'm', 'x'])}-{rng.randrange(100, 999)}.eml" for _ in range(12)})
    names = rng.sample(names, 4)
    files = {}
    for g, n in zip(gens, names):
        files[n] = g(rng)
    out = [render(n, files[n]) for n in sorted(files)]
    return files, "\n\n".join(out)


class Challenge(H.Challenge):
    name = "mime_parse"
    title = "MIME messages: nested multipart, QP/base64, charsets, RFC 2047 and RFC 2231"
    category = "text"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        files, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        for n, raw in files.items():
            (d / n).write_bytes(raw)
        return H.Case(args=[str(d)], expected=exp)
