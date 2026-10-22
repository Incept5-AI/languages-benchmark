"""protobuf_decode — parse a .proto (proto3 subset), decode length-delimited binary records, print proto3 JSON.

Hard parts: a .proto tokenizer (comments, string literals with braces/semicolons in options, nested
declarations, forward references), the wire format (10-byte negative int32 varints, max uint64, zigzag, fixed
types, non-minimal varints), packed and unpacked encodings of the same repeated field mixed in one message,
last-wins scalars, merging of split singular embedded messages, oneof clearing/merging, map entries with
missing/reversed/duplicate keys, unknown fields of every wire type including nested groups, and a precisely
pinned JSON output (field order, presence, 64-bit strings, base64, escaping, map key order by code point).

The oracle below decodes the bytes with the schema *structure* (it never parses the .proto text). It was
cross-checked against google.protobuf (protoc-generated classes + json_format.MessageToDict) while authoring.
"""
import base64
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = """\
Write a {LANG} program that decodes Protocol Buffers binary messages using a `.proto` schema and prints
them as JSON.

The program is run with one argument: a directory containing `schema.proto` and `messages.bin`.

## The schema (`schema.proto`)

A proto3 file using only this subset of the language:

- `syntax = "proto3";`, `package a.b.c;` and `option name = <constant>;` statements (options may also appear
  inside messages and enums, and as `[name = value, ...]` after a field or enum value). Options have no effect.
  Constants may be string literals in double quotes (which can contain `;`, `{`, `}`, `//` and similar
  characters, but no escaped quotes), identifiers, or numbers.
- `message Name { ... }` containing fields, nested `message` and `enum` declarations, `oneof name { ... }`
  groups, `reserved ...;` statements (numbers, ranges like `9 to 11`, or quoted names — ignore them) and options.
- Fields: `[repeated | optional] <type> <name> = <number> [ [options] ];` and
  `map<<keytype>, <type>> <name> = <number>;`. Fields inside a `oneof` have no label.
- `enum Name { VALUE_NAME = <int>; ... }` — values may be negative; the first value is 0. No two values of an
  enum share a number.
- Comments: `// …` to end of line and `/* … */` (not nested). Whitespace and line breaks are free-form.
- Field types: `int32 int64 uint32 uint64 sint32 sint64 bool string bytes fixed32 fixed64 sfixed32 sfixed64`, or
  the name of a message or enum. Type names are always simple (unqualified) names, and every message and enum
  name in the file is unique, so a name refers to that declaration wherever it is nested. A type may be used
  before it is declared. Map key types are `string` or `int32`; map value types are any non-map type.
  (`float`, `double`, `group` declarations, `import`, `extend`, `service` and qualified names never occur.)

The **root type** is the first message declared at the top level of the file.

## The messages (`messages.bin`)

A sequence of records until end of file. Each record is a varint byte length followed by that many bytes: one
encoded root-type message (a record may be empty). Decode it with the standard protobuf wire format:

- A message is a sequence of fields, each a varint key `(field_number << 3) | wire_type` followed by a value.
  Wire types: 0 varint, 1 fixed 64-bit (little-endian), 2 length-delimited (varint length + bytes),
  5 fixed 32-bit (little-endian), 3 start group / 4 end group.
- Varints are little-endian base-128, at most 10 bytes. A varint may be non-minimal (e.g. `81 80 00` is 1).
- `int32`, `int64`, `uint32`, `uint64`, `bool` and enums are varints. A negative `int32` or enum value is
  encoded as the 10-byte varint of its 64-bit two's complement; take the low 32 bits as a signed value.
  `sint32`/`sint64` use ZigZag encoding. `fixed32`/`sfixed32` are wire type 5, `fixed64`/`sfixed64` are wire
  type 1 (the `s` types signed two's complement). A `bool` is true if its varint is non-zero.
  `string` (valid UTF-8), `bytes` and embedded messages are wire type 2.
- **Unknown fields** (a field number not declared in that message) are skipped, whatever their wire type. An
  unknown field may be a group: wire type 3 with field number F, then any fields (which may themselves be
  groups), up to the matching wire type 4 key with the same field number F. Groups only occur as unknown fields.
- A **singular scalar** field (`string`, `bytes`, numbers, `bool`, enum) that occurs more than once: the last
  occurrence wins.
- A **singular message** field that occurs more than once: the occurrences are merged in order — scalars of
  later occurrences replace earlier ones, repeated fields and maps are appended/updated, and singular message
  fields are merged recursively by the same rule.
- **Repeated** fields append each occurrence in order. Repeated numeric, `bool` and enum fields may be encoded
  *packed* (one wire type 2 field whose payload is the concatenated elements, possibly zero of them) or
  *unpacked* (one key per element, with the element's normal wire type) — both forms may be mixed within the
  same message, whatever the schema says about packing; the elements are concatenated in wire order.
- **oneof**: setting a member clears any other member of the same oneof (so the last member seen wins). If a
  message-typed member occurs again while it is still the set member, the occurrences are merged as above;
  if a different member was set in between, it starts from empty again.
- **map** fields: each occurrence is one entry, an embedded message with key = field 1 and value = field 2
  (in either order, and either may be missing).
  A missing key or value takes the default (`""`, `0`, the empty message, the enum's 0 value). A later entry
  with the same key replaces the earlier one.

## Output

For every record, in order, print one line containing its JSON (no whitespace anywhere outside strings):

- A message is a JSON object. Its fields appear **in ascending field-number order** (not declaration order).
  The JSON name of a field is its name converted to lowerCamelCase: each `_` is removed and the character
  after it, if it is a lowercase ASCII letter, is upper-cased (`max_u64` → `maxU64`, `origin_2_code` →
  `origin2Code`); all other characters are kept.
- **Presence.** A singular scalar field is omitted when its value is the default (0, false, `""`, empty bytes,
  the enum value 0) — even if it occurred on the wire. Exceptions that are printed whenever they occurred,
  even with a default value: fields declared `optional`, and the set member of a oneof. A singular message
  field is printed whenever it occurred (an empty one is `{}`). Repeated fields and maps are omitted when empty.
- `int32`, `uint32`, `sint32`, `fixed32`, `sfixed32` → JSON number. `int64`, `uint64`, `sint64`, `fixed64`,
  `sfixed64` → decimal number in a JSON **string** (e.g. `"-9223372036854775808"`). `bool` → `true`/`false`.
- Enum → the value's name as a JSON string, or, if no value of the enum has that number, the number itself
  (a JSON number, e.g. `7` or `-2`).
- `bytes` → JSON string of standard base64 (alphabet `A–Z a–z 0–9 + /`, with `=` padding; empty → `""`).
- `string` → JSON string. Escape `"` as `\\"` and `\\` as `\\\\`; U+0008, U+0009, U+000A, U+000C, U+000D as
  `\\b \\t \\n \\f \\r`; every other character below U+0020 as `\\u00XX` with lowercase hex (e.g. `\\u001f`).
  Output every other character (including `/`, U+007F and all non-ASCII) literally as UTF-8.
- Repeated field → JSON array of the elements (elements are printed even if they are default values).
- Map → JSON object whose member names are the keys (an `int32` key in decimal, e.g. `"-5"`), escaped like
  strings, and whose values follow the rules above (printed even if default; a message value is an object).
  Members are sorted: `string` keys by Unicode code point (i.e. by their UTF-8 bytes), `int32` keys
  numerically.

Example: with

```proto
syntax = "proto3";
message Order {
  repeated int32 nums = 3;
  string title = 1;
  int64 big_num = 2;
  Status status = 4;
  map<string, int32> counts = 6;
  oneof pick { bytes raw = 7; uint32 code = 8; }
  enum Status { NONE = 0; OPEN = 1; }
}
```

records encoding {title "a\\"b", big_num -1, nums [1, 2], status 1, counts {"x": 0}} and {code 0}
print
```
{"title":"a\\"b","bigNum":"-1","nums":[1,2],"status":"OPEN","counts":{"x":0}}
{"code":0}
```
"""

M64 = (1 << 64) - 1
VARINT_T = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool", "enum"}
FIX32_T = {"fixed32", "sfixed32"}
FIX64_T = {"fixed64", "sfixed64"}
INT64_T = {"int64", "uint64", "sint64", "fixed64", "sfixed64"}


# ── schema model ─────────────────────────────────────────────────────────────
class F:
    def __init__(self, name, typ, num, label="", oneof=None, key=None, opts=""):
        self.name, self.typ, self.num, self.label, self.oneof, self.key, self.opts = \
            name, typ, num, label, oneof, key, opts


class Schema:
    def __init__(self):
        self.msgs = {}     # name -> list[F]
        self.enums = {}    # name -> list[(name, num)]

    def kind(self, typ):
        if typ in self.msgs:
            return "message"
        if typ in self.enums:
            return "enum"
        return typ


def json_name(n):
    out, up = [], False
    for c in n:
        if c == "_":
            up = True
            continue
        out.append(c.upper() if up and "a" <= c <= "z" else c)
        up = False
    return "".join(out)


# ── wire encoding ────────────────────────────────────────────────────────────
def varint(v, extra=0):
    v &= M64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v or extra:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
        if not v and extra:
            # redundant groups: 0x80 ... 0x00
            for _ in range(extra - 1):
                out.append(0x80)
            out.append(0x00)
            break
    assert len(out) <= 10
    return bytes(out)


def key(num, wt):
    return varint((num << 3) | wt)


def zz(v):
    return ((v << 1) ^ (v >> 63)) & M64


def enc_scalar(kind, v, rng):
    """Return (wire_type, payload bytes without key)."""
    if kind in ("int32", "int64", "enum", "uint32", "uint64"):
        extra = 0
        if 0 <= v < (1 << 40) and rng.random() < .12:
            extra = rng.randint(1, 2)
        return 0, varint(v, extra)
    if kind in ("sint32", "sint64"):
        return 0, varint(zz(v))
    if kind == "bool":
        return 0, varint(rng.choice([1, 1, 2, 300]) if v else 0)
    if kind == "fixed32":
        return 5, struct.pack("<I", v)
    if kind == "sfixed32":
        return 5, struct.pack("<i", v)
    if kind == "fixed64":
        return 1, struct.pack("<Q", v)
    if kind == "sfixed64":
        return 1, struct.pack("<q", v)
    if kind == "string":
        b = v.encode()
        return 2, varint(len(b)) + b
    if kind == "bytes":
        return 2, varint(len(v)) + v
    raise ValueError(kind)


# ── oracle decoder ───────────────────────────────────────────────────────────
def rd_varint(b, p):
    r = s = 0
    while True:
        c = b[p]
        p += 1
        r |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            return r & M64, p


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def s64(v):
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def skip(b, p, wt, fn):
    if wt == 0:
        return rd_varint(b, p)[1]
    if wt == 1:
        return p + 8
    if wt == 5:
        return p + 4
    if wt == 2:
        n, p = rd_varint(b, p)
        return p + n
    if wt == 3:
        while True:
            k, p = rd_varint(b, p)
            if k & 7 == 4:
                assert k >> 3 == fn
                return p
            p = skip(b, p, k & 7, k >> 3)
    raise ValueError(wt)


def conv(kind, raw):
    if kind in ("int32", "enum"):
        return s32(raw)
    if kind == "uint32":
        return raw & 0xFFFFFFFF
    if kind == "int64":
        return s64(raw)
    if kind == "uint64":
        return raw
    if kind == "sint32":
        return s32((raw >> 1) ^ -(raw & 1))
    if kind == "sint64":
        return s64((raw >> 1) ^ -(raw & 1))
    if kind == "bool":
        return raw != 0
    raise ValueError(kind)


def read_one(sch, kind, b, p, wt):
    if kind in VARINT_T:
        raw, p = rd_varint(b, p)
        return conv(kind, raw), p
    if kind == "fixed32":
        return struct.unpack_from("<I", b, p)[0], p + 4
    if kind == "sfixed32":
        return struct.unpack_from("<i", b, p)[0], p + 4
    if kind == "fixed64":
        return struct.unpack_from("<Q", b, p)[0], p + 8
    if kind == "sfixed64":
        return struct.unpack_from("<q", b, p)[0], p + 8
    n, p = rd_varint(b, p)
    return b[p:p + n], p + n


def decode(sch, mname, b, msg=None):
    """msg: dict num -> value. message values are dicts, repeated are lists, maps are dicts."""
    if msg is None:
        msg = {}
    fields = {f.num: f for f in sch.msgs[mname]}
    p = 0
    while p < len(b):
        k, p = rd_varint(b, p)
        fn, wt = k >> 3, k & 7
        f = fields.get(fn)
        if f is None:
            p = skip(b, p, wt, fn)
            continue
        kind = sch.kind(f.typ)
        if f.key:  # map entry
            body, p = read_one(sch, "bytes", b, p, wt)
            kk, vv = None, None
            q = 0
            vkind = kind
            while q < len(body):
                ek, q = rd_varint(body, q)
                efn, ewt = ek >> 3, ek & 7
                if efn == 1:
                    kk, q = read_one(sch, f.key, body, q, ewt)
                    if f.key == "string":
                        kk = bytes(kk).decode()
                elif efn == 2:
                    if vkind == "message":
                        sub, q = read_one(sch, "bytes", body, q, ewt)
                        vv = decode(sch, f.typ, sub, vv if isinstance(vv, dict) else None)
                    else:
                        vv, q = read_one(sch, vkind, body, q, ewt)
                        if vkind == "string":
                            vv = bytes(vv).decode()
                else:
                    q = skip(body, q, ewt, efn)
            if kk is None:
                kk = "" if f.key == "string" else 0
            if vv is None:
                vv = {} if vkind == "message" else default(vkind)
            msg.setdefault(fn, {})[kk] = vv
            continue
        if f.label == "repeated":
            lst = msg.setdefault(fn, [])
            if kind in ("string", "bytes", "message"):
                v, p = read_one(sch, "bytes", b, p, wt)
                if kind == "message":
                    v = decode(sch, f.typ, v)
                elif kind == "string":
                    v = bytes(v).decode()
                else:
                    v = bytes(v)
                lst.append(v)
            elif wt == 2:
                body, p = read_one(sch, "bytes", b, p, wt)
                q = 0
                ewt = 0 if kind in VARINT_T else (5 if kind in FIX32_T else 1)
                while q < len(body):
                    v, q = read_one(sch, kind, body, q, ewt)
                    lst.append(v)
            else:
                v, p = read_one(sch, kind, b, p, wt)
                lst.append(v)
            continue
        # singular
        if f.oneof:
            for g in sch.msgs[mname]:
                if g.oneof == f.oneof and g.num != fn:
                    msg.pop(g.num, None)
        if kind == "message":
            body, p = read_one(sch, "bytes", b, p, wt)
            msg[fn] = decode(sch, f.typ, body, msg.get(fn))
        else:
            v, p = read_one(sch, kind, b, p, wt)
            if kind == "string":
                v = bytes(v).decode()
            elif kind == "bytes":
                v = bytes(v)
            msg[fn] = v
    return msg


def default(kind):
    return {"string": "", "bytes": b"", "bool": False}.get(kind, 0)


def jstr(s):
    out = ['"']
    for c in s:
        o = ord(c)
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c in "\b\t\n\f\r":
            out.append({"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}[c])
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def jval(sch, typ, v):
    kind = sch.kind(typ)
    if kind == "message":
        return to_json(sch, typ, v)
    if kind == "enum":
        names = {n: nm for nm, n in sch.enums[typ]}
        return jstr(names[v]) if v in names else str(v)
    if kind == "string":
        return jstr(v)
    if kind == "bytes":
        return jstr(base64.b64encode(v).decode())
    if kind == "bool":
        return "true" if v else "false"
    if kind in INT64_T:
        return '"%d"' % v
    return "%d" % v


def to_json(sch, mname, msg):
    parts = []
    for f in sorted(sch.msgs[mname], key=lambda f: f.num):
        if f.num not in msg:
            continue
        v = msg[f.num]
        kind = sch.kind(f.typ)
        if f.key:
            if not v:
                continue
            ks = sorted(v, key=(lambda s: s) if f.key == "string" else int)
            body = ",".join(jstr(str(k)) + ":" + jval(sch, f.typ, v[k]) for k in ks)
            parts.append(jstr(json_name(f.name)) + ":{" + body + "}")
        elif f.label == "repeated":
            if not v:
                continue
            parts.append(jstr(json_name(f.name)) + ":[" + ",".join(jval(sch, f.typ, x) for x in v) + "]")
        else:
            if kind != "message" and not f.oneof and f.label != "optional" and v == default(kind):
                continue
            parts.append(jstr(json_name(f.name)) + ":" + jval(sch, f.typ, v))
    return "{" + ",".join(parts) + "}"


# ── schema generation ────────────────────────────────────────────────────────
NAMES = {
    "root": ["Order", "Shipment", "Ticket"],
    "item": ["Item", "LineItem", "Part"],
    "detail": ["Detail", "Spec", "Extra"],
    "status": ["Status", "State", "Phase"],
    "color": ["Color", "Hue", "Tint"],
}


def build_schema(rng):
    sch = Schema()
    R, I, D, S, C = (rng.choice(NAMES[k]) for k in ("root", "item", "detail", "status", "color"))
    reserved_nums = {40, 41, 42, 43}
    pool = [n for n in range(1, 64) if n not in reserved_nums]
    rng.shuffle(pool)
    pool = pool[:34] + [2047, 262143, 536870911]
    rng.shuffle(pool)
    rf = [
        ("int32", "order_id", ""), ("int64", "big_num", ""), ("uint32", "count_u32", ""),
        ("uint64", "max_u64", ""), ("sint32", "delta_s32", ""), ("sint64", "offset_s64", ""),
        ("bool", "is_active", ""), ("string", "title", ""), ("bytes", "payload", ""),
        ("fixed32", "crc_f32", ""), ("fixed64", "stamp_f64", ""), ("sfixed32", "temp_sf32", ""),
        ("sfixed64", "balance_sf64", ""), (S, "status", ""), (C, "main_color", ""),
        ("int32", "retry_count", "optional"), ("string", "nick_name", "optional"),
        (I, "main_item", ""), (I, "items", "repeated"), ("int32", "nums", "repeated"),
        ("sint64", "deltas", "repeated"), ("fixed32", "codes", "repeated"), (S, "history", "repeated"),
        ("string", "tags", "repeated"), ("bytes", "chunks", "repeated"), ("bool", "flags", "repeated"),
        ("uint64", "big_list", "repeated"), ("sfixed64", "ledger", "repeated"),
    ]
    fields = []
    for t, n, lab in rf:
        opts = ""
        if lab == "repeated" and t in ("fixed32", "int32") and rng.random() < .7:
            opts = rng.choice(["packed = false", "packed=true", 'deprecated = true'])
        fields.append(F(n, t, pool.pop(), lab, opts=opts))
    fields.append(F("counts", "int32", pool.pop(), key="string"))
    fields.append(F("labels", "string", pool.pop(), key="int32"))
    fields.append(F("by_name", I, pool.pop(), key="string"))
    fields.append(F("shade_map", C, pool.pop(), key="int32"))
    for t, n in (("string", "note_text"), ("int64", "ticket_2_id"), (I, "gift"), (C, "shade")):
        fields.append(F(n, t, pool.pop(), oneof="choice"))
    sch.msgs[R] = fields
    inum = list(range(1, 12))
    rng.shuffle(inum)
    sch.msgs[I] = [F("sku", "string", inum[0]), F("qty", "int32", inum[1]), F("price_micros", "uint64", inum[2]),
                   F("notes", "string", inum[3], "repeated"), F("detail", D, inum[4]),
                   F("tint", C, inum[5]), F("weights", "sint32", inum[6], "repeated")]
    dnum = rng.sample(range(1, 9), 5)
    sch.msgs[D] = [F("fragile", "bool", dnum[0]), F("tilt", "sint32", dnum[1]), F("dims", "int32", dnum[2], "repeated"),
                   F("origin_2_code", "string", dnum[3]), F("x_y", "fixed64", dnum[4])]
    sch.enums[S] = [(S.upper() + "_UNSPECIFIED", 0), ("ACTIVE", 1), ("PAUSED", 2), ("CLOSED", 5), ("LEGACY", -1)]
    sch.enums[C] = [(C.upper() + "_NONE", 0), ("RED", 1), ("GREEN", 2), ("BLUE", 3), ("INFRA_RED", 100)]
    sch.names = dict(R=R, I=I, D=D, S=S, C=C)
    return sch


def render_schema(sch, rng):
    N = sch.names
    R, I, D, S, C = N["R"], N["I"], N["D"], N["S"], N["C"]

    def ftxt(f):
        if f.key:
            t = f"map<{f.key},{rng.choice(['', ' '])}{f.typ}>"
        else:
            t = (f.label + " " if f.label else "") + f.typ
        o = f" [{f.opts}]" if f.opts else ""
        sp = rng.choice([" ", "  ", "\t"])
        return f"{t}{sp}{f.name} = {f.num}{o};"

    def enum_txt(name, ind):
        vals = sch.enums[name]
        body = [f"{ind}  {n} = {v};" for n, v in vals]
        if rng.random() < .5:
            body.insert(1, f'{ind}  option deprecated = false; // no effect')
        return [f"{ind}enum {name} {{"] + body + [f"{ind}}}"]

    root_fields = [f for f in sch.msgs[R] if not f.oneof]
    oneof_fields = [f for f in sch.msgs[R] if f.oneof]
    rng.shuffle(root_fields)
    lines = ['// generated for codebench2', 'syntax = "proto3";', "",
             "/* message Decoy { int32 not_real = 1; }  -- commented out */",
             f"package cb2.wire{rng.randint(1, 9)};",
             'option java_package = "com.example;weird{brace}//not a comment";',
             "option optimize_for = SPEED;", ""]
    lines.append(f"message {R} {{ // the root type")
    lines.append("  reserved 40, 41 to 43;")
    lines.append('  reserved "old_field", "legacy";')
    half = len(root_fields) // 2
    for f in root_fields[:half]:
        lines.append("  " + ftxt(f))
    lines += enum_txt(S, "  ")
    lines.append("  oneof choice {")
    for f in oneof_fields:
        lines.append("    " + ftxt(f))
    lines.append("  }")
    for f in root_fields[half:]:
        lines.append("  " + ftxt(f) + ("  /* trailing */" if rng.random() < .2 else ""))
    lines.append("}")
    lines.append("")
    lines += enum_txt(C, "")
    lines.append("")
    item_fields = list(sch.msgs[I])
    rng.shuffle(item_fields)
    lines.append(f"message {I}")
    lines.append("{")
    lines.append(f"  message {D} {{")
    for f in sch.msgs[D]:
        lines.append("    " + ftxt(f))
    lines.append("  }")
    for f in item_fields:
        lines.append("  " + ftxt(f))
    lines.append("}")
    return "\n".join(lines) + "\n"


# ── message generation ───────────────────────────────────────────────────────
STR_POOL = ["", "hello", 'say "hi"', "back\\slash", "tab\there", "line\nbreak", "cr\r", "nul\x00byte",
            "ctl\x01\x1f\x7f", "bell\b\f", "café", "€100", "😀 grin", "x nbsp", "a/b<c>", "it's",
            "日本語", "ｚ", "￿", "𝄞clef"]
KEY_POOL = ["a", "b", "", "Z", "zz", "é", "ｚ", "😀", "￿", "a b", 'q"', "\t", "aa", "𝄞"]


def rand_val(sch, kind, typ, rng, nondefault=False):
    while True:
        v = _rand_val(sch, kind, typ, rng)
        if not nondefault or v != default(kind):
            return v


def _rand_val(sch, kind, typ, rng):
    r = rng.random
    if kind == "int32":
        return rng.choice([0, 1, -1, 127, 128, 2147483647, -2147483648, rng.randint(-10**6, 10**6), 300])
    if kind == "int64":
        return rng.choice([0, -1, 2**63 - 1, -2**63, 2**53 + 1, rng.randint(-2**62, 2**62)])
    if kind == "uint32":
        return rng.choice([0, 2**32 - 1, 300, rng.randint(0, 2**32 - 1)])
    if kind == "uint64":
        return rng.choice([0, 2**64 - 1, 2**63, rng.randint(0, 2**64 - 1), 1])
    if kind == "sint32":
        return rng.choice([0, -1, 1, -2**31, 2**31 - 1, rng.randint(-10**5, 10**5)])
    if kind == "sint64":
        return rng.choice([0, -1, -2**63, 2**63 - 1, rng.randint(-2**62, 2**62)])
    if kind == "fixed32":
        return rng.choice([0, 2**32 - 1, rng.randint(0, 2**32 - 1)])
    if kind == "fixed64":
        return rng.choice([0, 2**64 - 1, rng.randint(0, 2**64 - 1)])
    if kind == "sfixed32":
        return rng.choice([0, -1, -2**31, rng.randint(-2**31, 2**31 - 1)])
    if kind == "sfixed64":
        return rng.choice([0, -1, -2**63, rng.randint(-2**63, 2**63 - 1)])
    if kind == "bool":
        return r() < .6
    if kind == "string":
        return "".join(rng.sample(STR_POOL, rng.randint(1, 3)))
    if kind == "bytes":
        return bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 2, 3, 4, 5, 9])))
    if kind == "enum":
        nums = [n for _, n in sch.enums[typ]]
        return rng.choice(nums + [7, -2, 0])
    raise ValueError(kind)


def unknown_field(rng, used, depth=0):
    fn = rng.choice([n for n in list(range(1, 70)) + [1000, 5000, 99999] if n not in used])
    wt = rng.choice([0, 1, 2, 5, 3])
    if wt == 3 and depth < 2:
        inner = b"".join(unknown_field(rng, set(), depth + 1) for _ in range(rng.randint(0, 3)))
        return key(fn, 3) + inner + key(fn, 4)
    if wt == 3:
        wt = 0
    if wt == 0:
        return key(fn, 0) + varint(rng.choice([0, 1, 2**64 - 1, rng.randint(0, 2**40)]))
    if wt == 1:
        return key(fn, 1) + bytes(rng.randrange(256) for _ in range(8))
    if wt == 5:
        return key(fn, 5) + bytes(rng.randrange(256) for _ in range(4))
    blob = bytes(rng.randrange(256) for _ in range(rng.randint(0, 12)))
    return key(fn, 2) + varint(len(blob)) + blob


def gen_message(sch, mname, rng, depth=0, density=0.6):
    """Return encoded bytes: a shuffled list of field occurrences for message `mname`."""
    fields = sch.msgs[mname]
    used = {f.num for f in fields}
    events = []   # list of (order_group, bytes); same-field order preserved by appending sequentially
    oneof_members = [f for f in fields if f.oneof]
    for f in fields:
        if f.oneof:
            continue
        kind = sch.kind(f.typ)
        if rng.random() > density:
            continue
        evs = []
        if f.key:
            keys = []
            for _ in range(rng.randint(1, 4)):
                kk = rng.choice(KEY_POOL) if f.key == "string" else rng.choice([0, 1, -1, 7, -300, 2**31 - 1, 42])
                keys.append(kk)
            if rng.random() < .5:
                keys.append(keys[0])   # duplicate key later -> replaces
            for kk in keys:
                kb = b"" if rng.random() < .1 else key(1, enc_scalar(f.key, kk, rng)[0]) + enc_scalar(f.key, kk, rng)[1]
                if kind == "message":
                    vb = gen_message(sch, f.typ, rng, depth + 1, 0.4) if depth < 2 else b""
                    vpart = key(2, 2) + varint(len(vb)) + vb
                else:
                    vv = rand_val(sch, kind, f.typ, rng)
                    wt, pl = enc_scalar(kind, vv, rng)
                    vpart = key(2, wt) + pl
                if rng.random() < .15:
                    vpart = b""
                parts = [kb, vpart]
                if rng.random() < .3:
                    parts.reverse()
                entry = b"".join(parts)
                evs.append(key(f.num, 2) + varint(len(entry)) + entry)
        elif f.label == "repeated":
            n = rng.randint(1, 6)
            vals = [rand_val(sch, kind, f.typ, rng) if kind != "message" else None for _ in range(n)]
            if kind == "message":
                for _ in range(n):
                    b = gen_message(sch, f.typ, rng, depth + 1, 0.4) if depth < 2 else b""
                    evs.append(key(f.num, 2) + varint(len(b)) + b)
            elif kind in ("string", "bytes"):
                for v in vals:
                    evs.append(key(f.num, 2) + enc_scalar(kind, v, rng)[1])
            else:
                i = 0
                while i < n:
                    j = min(n, i + rng.randint(1, 3))
                    if rng.random() < .5:
                        pl = b"".join(enc_scalar(kind, v, rng)[1] for v in vals[i:j])
                        evs.append(key(f.num, 2) + varint(len(pl)) + pl)
                    else:
                        for v in vals[i:j]:
                            wt, pl = enc_scalar(kind, v, rng)
                            evs.append(key(f.num, wt) + pl)
                    i = j
                if rng.random() < .15:
                    evs.insert(rng.randint(0, len(evs)), key(f.num, 2) + varint(0))  # empty packed run
        elif kind == "message":
            for _ in range(rng.choice([1, 2, 2, 3])):   # split occurrences -> merge
                b = gen_message(sch, f.typ, rng, depth + 1, 0.35) if depth < 2 else b""
                evs.append(key(f.num, 2) + varint(len(b)) + b)
        else:
            reps = rng.choice([1, 1, 2, 3])
            for i in range(reps):
                v = rand_val(sch, kind, f.typ, rng)
                if i == reps - 1 and reps > 1 and rng.random() < .3:
                    v = default(kind)        # last occurrence is the default -> omitted (unless optional)
                wt, pl = enc_scalar(kind, v, rng)
                evs.append(key(f.num, wt) + pl)
        events.append(evs)
    if oneof_members and rng.random() < density + .2:
        seq = [rng.choice(oneof_members) for _ in range(rng.randint(1, 4))]
        evs = []
        for f in seq:
            kind = sch.kind(f.typ)
            if kind == "message":
                b = gen_message(sch, f.typ, rng, depth + 1, 0.35) if depth < 2 else b""
                evs.append(key(f.num, 2) + varint(len(b)) + b)
            else:
                v = rand_val(sch, kind, f.typ, rng) if rng.random() < .75 else default(kind)
                wt, pl = enc_scalar(kind, v, rng)
                evs.append(key(f.num, wt) + pl)
        events.append(evs)
    for _ in range(rng.randint(0, 3)):
        events.append([unknown_field(rng, used)])
    # interleave the per-field event lists, preserving each list's internal order
    out = []
    while events:
        i = rng.randrange(len(events))
        out.append(events[i].pop(0))
        if not events[i]:
            events.pop(i)
    return b"".join(out)


def forced_root(sch, rng):
    """A record guaranteed to contain the sharpest traps."""
    N = sch.names
    fs = {f.name: f for f in sch.msgs[N["R"]]}
    ifs = {f.name: f for f in sch.msgs[N["I"]]}
    dfs = {f.name: f for f in sch.msgs[N["D"]]}

    def fld(f, kind, v):
        wt, pl = enc_scalar(kind, v, rng)
        return key(f.num, wt) + pl

    def sub(f, b):
        return key(f.num, 2) + varint(len(b)) + b

    out = []
    out.append(fld(fs["order_id"], "int32", -rng.randint(1, 2**31)))            # 10-byte varint
    out.append(fld(fs["max_u64"], "uint64", 2**64 - 1))
    out.append(fld(fs["offset_s64"], "sint64", -2**63))
    out.append(fld(fs["balance_sf64"], "sfixed64", -rng.randint(1, 2**63)))
    out.append(fld(fs["retry_count"], "int32", 0))                                 # optional 0 -> printed
    out.append(fld(fs["title"], "string", "first"))
    out.append(unknown_field(rng, {f.num for f in sch.msgs[N["R"]]}))
    out.append(key(77, 3) + key(3, 0) + varint(5) + key(9, 3) + key(1, 5) + b"abcd" + key(9, 4) + key(77, 4))
    out.append(fld(fs["title"], "string", 'last "wins"\\\x01\x7f'))
    out.append(fld(fs["big_num"], "int64", 5))
    out.append(fld(fs["big_num"], "int64", 0))                                     # last is default -> omitted
    out.append(fld(fs["status"], "enum", -1))
    out.append(fld(fs["main_color"], "enum", 7))                                   # unknown enum -> number
    # nums: packed + unpacked mixed
    nf = fs["nums"]
    pl = varint(1) + varint(-5 & M64) + varint(300)
    out.append(key(nf.num, 2) + varint(len(pl)) + pl)
    out.append(key(nf.num, 0) + varint(2**31 - 1))
    out.append(key(nf.num, 2) + varint(0))
    # main_item split into two occurrences
    mi = fs["main_item"]
    a = fld(ifs["sku"], "string", "A-1") + fld(ifs["qty"], "int32", 3) + \
        sub(ifs["detail"], fld(dfs["fragile"], "bool", True) + fld(dfs["dims"], "int32", 4))
    out.append(sub(mi, a))
    out.append(fld(fs["codes"], "fixed32", 2**32 - 1))
    b = fld(ifs["qty"], "int32", 9) + sub(ifs["detail"], fld(dfs["dims"], "int32", 5) +
                                           fld(dfs["origin_2_code"], "string", "GB")) + \
        fld(ifs["notes"], "string", "n1")
    out.append(sub(mi, b))
    # map with duplicate key, reversed entry, missing value
    cf = fs["counts"]
    for kk, vv, mode in (("b", 2, "n"), ("a", 0, "n"), ("ｚ", 1, "r"), ("😀", 4, "n"), ("b", 7, "n"), ("", None, "n")):
        kb = key(1, 2) + enc_scalar("string", kk, rng)[1]
        vb = b"" if vv is None else key(2, 0) + varint(vv & M64)
        e = vb + kb if mode == "r" else kb + vb
        out.append(sub(cf, e))
    # oneof: message member, then other member, then message member again (fresh), then merge
    g = fs["gift"]
    out.append(sub(g, fld(ifs["sku"], "string", "stale")))
    out.append(fld(fs["ticket_2_id"], "int64", 0))
    out.append(sub(g, fld(ifs["qty"], "int32", 1)))
    out.append(sub(g, fld(ifs["tint"], "enum", 2)))
    return b"".join(out)


def make_case(seed: int):
    rng = random.Random(1100 + seed)
    sch = build_schema(rng)
    text = render_schema(sch, rng)
    R = sch.names["R"]
    recs = [forced_root(sch, rng), b""]
    for _ in range(7):
        recs.append(gen_message(sch, R, rng, 0, rng.choice([0.3, 0.5, 0.8])))
    # a record with only a oneof member at its default value
    of = next(f for f in sch.msgs[R] if f.name == "note_text")
    recs.append(key(of.num, 2) + varint(0))
    rng.shuffle(recs)
    blob = b"".join(varint(len(r)) + r for r in recs)
    lines = [to_json(sch, R, decode(sch, R, r)) for r in recs]
    return text, blob, "\n".join(lines) + "\n", sch, recs


class Challenge(H.Challenge):
    name = "protobuf_decode"
    title = "Decode protobuf binary records with a .proto schema into proto3 JSON"
    category = "binary"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        text, blob, exp, _, _ = make_case(seed)
        d = ctx.data_dir(self, seed)
        (d / "schema.proto").write_text(text)
        (d / "messages.bin").write_bytes(blob)
        return H.Case(args=[str(d)], expected=exp)
