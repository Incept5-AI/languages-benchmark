"""json_schema — validate JSON instances against a JSON Schema (2020-12 subset) and report exactly which
keywords fail, as sorted (instance pointer, schema pointer) pairs.

Hard parts: a JSON parser (C/Rust), numbers compared by value (1 == 1.0, -0 == 0, 1e2 is an integer) while
booleans are never numbers, string length in code points with surrogate-pair escapes, deep equality with
key order ignored, type-specific keywords ignored on other types, additionalProperties seeing only its own
`properties`, items after prefixItems, boolean schemas, the precise reporting rules for anyOf/oneOf/not/
if-then-else/allOf/$ref (incl. recursive refs and refs to `#`), JSON Pointer escaping (~0 before ~1), and a
code-point (not UTF-16) sort.
"""
import copy
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = r"""
Write a {LANG} program that validates JSON documents against a JSON Schema and reports exactly which schema
keywords fail.

The program is run with one argument: a directory containing two UTF-8 files.
- `schema.json` — one schema, in the dialect described below.
- `instances.json` — a JSON array; each element is one instance to validate against the schema. The instances
  are numbered 0, 1, 2, … in array order.

## JSON values

Both files are ordinary JSON (RFC 8259) with arbitrary whitespace. Strings may contain any JSON escape,
including `\uXXXX` escapes and surrogate pairs for characters outside the Basic Multilingual Plane
(`"\ud83d\ude00"` is the single character U+1F600). An escaped character is identical to the same character
written literally. Strings never contain unpaired surrogates, and no object has two members with the same
name (after decoding escapes).

The JSON types are null, boolean, number, string, array and object. `true` and `false` are booleans and are
never equal to any number.

**Numbers** are compared by mathematical value: `1`, `1.0`, `1e0` and `10E-1` are equal, and `-0` equals `0`.
A number is an *integer* when its value has no fractional part, so `1.0`, `1e2`, `-0` and `2.50e1` are
integers and `1.5` is not. Every number in either file has at most 15 significant digits and an absolute value
below 10^15 (parsing numbers as IEEE-754 doubles and comparing the doubles therefore gives exact answers).

**Deep equality** (used by `enum`, `const` and `uniqueItems`): two values are equal when they have the same
JSON type and
- numbers: equal values (as above);
- strings: the same sequence of Unicode code points;
- booleans, null: the same value;
- arrays: the same length and pairwise equal elements, in order;
- objects: the same set of member names and equal values for every name (member order is irrelevant).

**String length** is the number of Unicode code points: U+1F600 counts as 1, and `e` followed by the
combining accent U+0301 counts as 2.

## Schemas

A schema is a boolean or an object. `true` accepts every instance; `false` rejects every instance. An object
schema applies all of the keywords listed below that it contains; every keyword is evaluated (there is no
short-circuiting). Any other member (`$schema`, `title`, `description`, `$comment`, `format`, `default`,
`examples`, …) is ignored, and `$defs` only holds schemas that `$ref` refers to.

A keyword listed under one instance type is **ignored** (it neither fails nor reports anything) when the
instance has a different type — e.g. `minimum` against a string, or `required` against an array.

Any instance type:
- `type` — a type name or an array of type names, from `null`, `boolean`, `object`, `array`, `number`,
  `string`, `integer`. Fails unless the instance has (one of) the named type(s); `number` matches every number
  and `integer` matches numbers that are integers.
- `enum` — an array; fails unless the instance is deep-equal to at least one element.
- `const` — any value; fails unless the instance is deep-equal to it.

Numbers (the keyword values are numbers):
- `minimum` fails unless instance ≥ value; `maximum` unless instance ≤ value; `exclusiveMinimum` unless
  instance > value; `exclusiveMaximum` unless instance < value.

Strings (non-negative integer values):
- `minLength` fails unless length ≥ value; `maxLength` unless length ≤ value.

Arrays:
- `minItems` / `maxItems` (non-negative integers) — fails unless the element count is ≥ / ≤ the value.
- `uniqueItems` — when `true`, fails if any two elements are deep-equal. `false` does nothing.
- `prefixItems` — an array of schemas: element i is validated against `prefixItems[i]`, for every i that is
  an index of both arrays.
- `items` — one schema: every element whose index is ≥ the length of `prefixItems` (every element, if the
  schema has no `prefixItems`) is validated against it.

Objects:
- `properties` — an object mapping names to schemas: every member of the instance whose name is a key of
  `properties` is validated against that schema.
- `required` — an array of distinct strings; each one should be a member name of the instance.
- `additionalProperties` — one schema, applied to every member of the instance whose name is **not** a key of
  the `properties` keyword **of the same schema object** (all members, if that object has no `properties`).
  `properties` found anywhere else (inside `allOf`, behind `$ref`, …) do not count.
- `minProperties` / `maxProperties` (non-negative integers) — fails unless the member count is ≥ / ≤ the value.

Applicators (any instance type):
- `allOf`, `anyOf`, `oneOf` — non-empty arrays of schemas.
- `not` — a schema.
- `if`, `then`, `else` — schemas. `then` and `else` are ignored when the same object has no `if`.
- `$ref` — a string: either `#` (the whole schema document) or `#/$defs/<name>`, where `<name>` is the name of a
  member of the top-level `$defs` object written as a JSON Pointer token (`~0` stands for `~`, `~1` for `/`;
  there is no percent-encoding). The instance is validated against the referenced schema. Other keywords next
  to `$ref` in the same object still apply as normal. References may be recursive, but never in a way that
  returns to the same schema without first descending into a member or element of the instance.

## Errors

Validating an instance against a schema produces a **set of errors**; each error is a pair
(instance location, schema location). Both are JSON Pointers (RFC 6901):
- the instance location is the path from the root of the instance to the value concerned (member names and
  decimal array indices);
- the schema location is the path from the root of `schema.json` to the failing keyword or schema **where it
  is written in `schema.json`**. After a `$ref` the path continues from the referenced schema's own location
  (e.g. `#/$defs/node/properties/v/type`), not from the path that reached it.

A pointer is printed as `#` followed by its reference tokens, each preceded by `/`. In each token every `~`
is written `~0` and every `/` is written `~1`. Nothing else is escaped; non-ASCII characters are printed as
UTF-8. The root is `#`; the root's member named `a/b~` is `#/a~1b~0`; element 3 of that member is
`#/a~1b~0/3`.

The errors of an instance I at instance location L against a schema S at schema location P:
- S is `false` → one error (L, P). S is `true` → none.
- Each **assertion** keyword that fails — `type`, `enum`, `const`, `minimum`, `maximum`, `exclusiveMinimum`,
  `exclusiveMaximum`, `minLength`, `maxLength`, `minItems`, `maxItems`, `uniqueItems`, `minProperties`,
  `maxProperties` — gives one error (L, P/<keyword>).
- `required` gives one error (L, P/required/<j>) for every index j whose name `required[j]` is missing from
  the instance.
- `properties`, `prefixItems`, `items` and `additionalProperties` give no error of their own; they contribute
  all errors from validating each member or element (at L/<name> or L/<index>) against the subschema at
  P/properties/<name>, P/prefixItems/<i>, P/items or P/additionalProperties respectively. So a `false`
  subschema reports the child's location with the subschema's location, e.g. (`#/x`, `#/additionalProperties`).
- `allOf` contributes the errors of every subschema (P/allOf/<i>); no error of its own.
- `$ref` contributes the errors of the referenced schema (located where it is written).
- `anyOf` — if at least one subschema accepts I: nothing. Otherwise one error (L, P/anyOf); the
  subschemas' own errors are **not** reported.
- `oneOf` — if exactly one subschema accepts I: nothing. Otherwise (none, or more than one) one error
  (L, P/oneOf); the subschemas' errors are not reported.
- `not` — if the subschema accepts I: one error (L, P/not). Otherwise nothing.
- `if`/`then`/`else` — the errors of `if` are never reported. If `if` accepts I and `then` is present, the
  errors of `then` are contributed; if `if` rejects I and `else` is present, the errors of `else` are
  contributed. No error of their own.

A schema *accepts* an instance when validating produces no errors. An instance is valid when the root schema
(location `#`) accepts it at instance location `#`. The same pair reached in several ways is one error.

## Output

For each instance, in order:
- `<n>: valid`, or
- `<n>: invalid (<k>)` followed by its k distinct errors, one per line, each as two spaces, the instance
  location, one space, the schema location. Errors are sorted by instance location, then by schema location,
  comparing the printed pointer strings by Unicode code point (not by UTF-16 code unit; this is the same as
  comparing their UTF-8 bytes). For example `#/a/10` sorts before `#/a/2`.

After the last instance print `valid=<V> invalid=<I>`.

"""

EXAMPLE_SCHEMA = ('{"type": "object", "required": ["id", "tags"],\n'
                  ' "properties": {"id": {"type": "integer", "minimum": 1},\n'
                  '                "tags": {"type": "array", "items": {"$ref": "#/$defs/tag"}, "uniqueItems": true},\n'
                  '                "a/b": {"anyOf": [{"type": "null"}, {"type": "string", "maxLength": 2}]}},\n'
                  ' "additionalProperties": false,\n'
                  ' "$defs": {"tag": {"type": "string", "minLength": 2}}}')
EXAMPLE_INSTANCES = ('[{"id": 1.0, "tags": ["\\ud83d\\ude00\\ud83d\\ude00", "ab"]},\n'
                     ' {"id": -0, "tags": ["x", "ab", "a\\u0062"], "a/b": "abc", "m~n": 1},\n'
                     ' [1, 2]]')


# ── oracle ───────────────────────────────────────────────────────────────────
def load(text):
    return json.loads(text, parse_float=Decimal, parse_int=Decimal)


def jtype(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, Decimal):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    raise TypeError(type(v))


def deq(a, b):
    ta, tb = jtype(a), jtype(b)
    if ta != tb:
        return False
    if ta == "array":
        return len(a) == len(b) and all(deq(x, y) for x, y in zip(a, b))
    if ta == "object":
        return set(a) == set(b) and all(deq(a[k], b[k]) for k in a)
    return a == b


def esc(tok):
    return str(tok).replace("~", "~0").replace("/", "~1")


def unesc(tok):
    return tok.replace("~1", "/").replace("~0", "~")


class Validator:
    ASSERT = ("type", "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
              "minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties")

    def __init__(self, root):
        self.root = root

    def errors(self, inst):
        out = set()
        self.v(self.root, "#", inst, "#", out)
        return sorted(out)

    def accepts(self, s, sp, inst, ip):
        out = set()
        self.v(s, sp, inst, ip, out)
        return not out

    def resolve(self, ref):
        if ref == "#":
            return self.root, "#"
        assert ref.startswith("#/$defs/") and ref.count("/") == 2, ref
        tok = ref[len("#/$defs/"):]
        return self.root["$defs"][unesc(tok)], "#/$defs/" + esc(unesc(tok))

    def v(self, s, sp, inst, ip, out):
        if s is True:
            return
        if s is False:
            out.add((ip, sp))
            return
        t = jtype(inst)

        def fail(kw):
            out.add((ip, f"{sp}/{kw}"))

        if "type" in s:
            names = s["type"] if isinstance(s["type"], list) else [s["type"]]
            ok = any(n == t or (n == "integer" and t == "number" and inst == inst.to_integral_value())
                     for n in names)
            if not ok:
                fail("type")
        if "enum" in s and not any(deq(inst, e) for e in s["enum"]):
            fail("enum")
        if "const" in s and not deq(inst, s["const"]):
            fail("const")
        if t == "number":
            for kw, ok in (("minimum", lambda x, v: x >= v), ("maximum", lambda x, v: x <= v),
                           ("exclusiveMinimum", lambda x, v: x > v), ("exclusiveMaximum", lambda x, v: x < v)):
                if kw in s and not ok(inst, s[kw]):
                    fail(kw)
        if t == "string":
            n = len(inst)       # Python str = code points (json decodes surrogate pairs)
            if "minLength" in s and n < s["minLength"]:
                fail("minLength")
            if "maxLength" in s and n > s["maxLength"]:
                fail("maxLength")
        if t == "array":
            if "minItems" in s and len(inst) < s["minItems"]:
                fail("minItems")
            if "maxItems" in s and len(inst) > s["maxItems"]:
                fail("maxItems")
            if s.get("uniqueItems") is True:
                if any(deq(inst[i], inst[j]) for i in range(len(inst)) for j in range(i + 1, len(inst))):
                    fail("uniqueItems")
            pre = s.get("prefixItems", [])
            for i, sub in enumerate(pre[:len(inst)]):
                self.v(sub, f"{sp}/prefixItems/{i}", inst[i], f"{ip}/{i}", out)
            if "items" in s:
                for i in range(len(pre), len(inst)):
                    self.v(s["items"], f"{sp}/items", inst[i], f"{ip}/{i}", out)
        if t == "object":
            props = s.get("properties", {})
            for name, sub in props.items():
                if name in inst:
                    self.v(sub, f"{sp}/properties/{esc(name)}", inst[name], f"{ip}/{esc(name)}", out)
            for j, name in enumerate(s.get("required", [])):
                if name not in inst:
                    out.add((ip, f"{sp}/required/{j}"))
            if "additionalProperties" in s:
                for name in inst:
                    if name not in props:
                        self.v(s["additionalProperties"], f"{sp}/additionalProperties", inst[name],
                               f"{ip}/{esc(name)}", out)
            if "minProperties" in s and len(inst) < s["minProperties"]:
                fail("minProperties")
            if "maxProperties" in s and len(inst) > s["maxProperties"]:
                fail("maxProperties")
        for i, sub in enumerate(s.get("allOf", [])):
            self.v(sub, f"{sp}/allOf/{i}", inst, ip, out)
        if "anyOf" in s:
            if not any(self.accepts(sub, f"{sp}/anyOf/{i}", inst, ip) for i, sub in enumerate(s["anyOf"])):
                fail("anyOf")
        if "oneOf" in s:
            n = sum(self.accepts(sub, f"{sp}/oneOf/{i}", inst, ip) for i, sub in enumerate(s["oneOf"]))
            if n != 1:
                fail("oneOf")
        if "not" in s and self.accepts(s["not"], f"{sp}/not", inst, ip):
            fail("not")
        if "if" in s:
            if self.accepts(s["if"], f"{sp}/if", inst, ip):
                if "then" in s:
                    self.v(s["then"], f"{sp}/then", inst, ip, out)
            elif "else" in s:
                self.v(s["else"], f"{sp}/else", inst, ip, out)
        if "$ref" in s:
            sub, rp = self.resolve(s["$ref"])
            self.v(sub, rp, inst, ip, out)


def expected_output(schema_text, instances_text):
    val = Validator(load(schema_text))
    out, nv = [], 0
    insts = load(instances_text)
    for n, inst in enumerate(insts):
        errs = val.errors(inst)
        if not errs:
            nv += 1
            out.append(f"{n}: valid")
        else:
            out.append(f"{n}: invalid ({len(errs)})")
            out += [f"  {a} {b}" for a, b in errs]
    out.append(f"valid={nv} invalid={len(insts) - nv}")
    return "\n".join(out)


def prompt_text():
    """The prompt with the example output computed by the oracle (so it can never drift)."""
    return (PROMPT + "Example. `schema.json`:\n```json\n" + EXAMPLE_SCHEMA + "\n```\n`instances.json`:\n```json\n"
            + EXAMPLE_INSTANCES + "\n```\nOutput:\n```\n" + expected_output(EXAMPLE_SCHEMA, EXAMPLE_INSTANCES)
            + "\n```\n")


# ── generation ───────────────────────────────────────────────────────────────
class Num:
    """A JSON number with its exact source text."""

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return self.text


def dump(v, rng, ind=0):
    """Serialise with varied escapes/number spellings so a careless parser is caught."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, Num):
        return v.text
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return dump_str(v, rng)
    if isinstance(v, list):
        return "[" + ", ".join(dump(x, rng, ind) for x in v) + "]"
    if isinstance(v, dict):
        items = list(v.items())
        return "{" + ", ".join(f"{dump_str(k, rng)}: {dump(x, rng, ind)}" for k, x in items) + "}"
    raise TypeError(type(v))


def dump_str(s, rng):
    out = ['"']
    for ch in s:
        c = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "/" and rng.random() < .3:
            out.append("\\/")
        elif c < 0x20:
            out.append({"\n": "\\n", "\t": "\\t"}.get(ch, f"\\u{c:04x}"))
        elif c > 0xFFFF:
            if rng.random() < .75:
                c -= 0x10000
                out.append(f"\\u{0xD800 + (c >> 10):04x}\\u{0xDC00 + (c & 0x3FF):04X}")
            else:
                out.append(ch)
        elif c > 0x7E and rng.random() < .5:
            out.append(f"\\u{c:04x}")
        elif c < 0x7F and ch.isalpha() and rng.random() < .02:
            out.append(f"\\u{c:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def make_case(seed):
    rng = random.Random(15015 + seed)
    R = rng.randint
    SL = rng.choice(["a/b", "in/out", "/lead", "x/y/z"])          # name with '/'
    TL = rng.choice(["m~n", "~1", "x~0y", "~"])                    # name with '~' (incl. literal "~1")
    UN = rng.choice(["é", "naïve", "ü/ß", "Ωmega"])               # non-ASCII name
    DEF = rng.choice(["a~b/c", "nz/~", "odd~/num"])               # $defs name needing escapes
    NODE = rng.choice(["node", "tree", "branch"])
    FW, ASTRAL = rng.choice(["Ａ", "ﬁ", "￥"]), rng.choice(["\U0001F600", "\U0001D11E", "\U00010437"])
    lo, hi = R(2, 3), R(5, 7)                 # name length bounds (code points)
    taglen = R(3, 4)
    maxtags = R(4, 6)
    big = R(40, 60)                           # if kind == big then score >= big
    cap = big + R(5, 15)                      # else score <= cap
    qmin = R(8, 12)
    ex_lo, ex_hi = R(0, 3), None
    ex_hi = ex_lo + R(2, 4)
    enum_obj = {"p": Num("1"), "q": [Num("2"), "z"]}
    enum_arr = [Num("1"), {"k": True}]
    const_v = {"u": [Num("1"), Num("2.5")], "v": UN, "w": None}

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": "properties under allOf do not count for additionalProperties",
        "title": "record",
        "type": "object",
        "required": ["id", "name", "tags", SL],
        "properties": {
            "id": {"type": "integer", "minimum": 1, "exclusiveMaximum": Num("1e6")},
            "name": {"type": "string", "minLength": lo, "maxLength": hi, "format": "email"},
            "score": {"type": "number", "minimum": Num("-5.5"), "maximum": Num("1E+2")},
            "tags": {"type": "array", "items": {"type": "string", "maxLength": taglen}, "uniqueItems": True,
                     "maxItems": maxtags},
            SL: {"enum": [Num("1"), "x", None, enum_arr, enum_obj]},
            TL: {"const": const_v},
            "point": {"type": "array", "prefixItems": [{"type": "number"}, {"type": "integer"}],
                      "items": False, "minItems": 2},
            "tree": {"$ref": f"#/$defs/{NODE}"},
            "kind": {"type": ["string", "null"]},
            "flag": {"type": "boolean"},
            "contact": {"oneOf": [{"type": "object", "required": ["email"]},
                                  {"type": "object", "required": ["phone"]}]},
            "extra": True,
            "nothing": False,
            "qty": {"anyOf": [{"type": "integer", "minimum": qmin}, {"type": "string", "maxLength": 2}]},
            "code": {"not": {"type": "string", "minLength": 1}},
            "ratio": {"exclusiveMinimum": ex_lo, "exclusiveMaximum": ex_hi, "description": "numbers only"},
            "child": {"$ref": "#"},
            "meta": {"type": "object", "minProperties": 1, "maxProperties": 3,
                     "additionalProperties": {"$ref": "#/$defs/" + esc(DEF)}},
            UN: {"type": "string", "maxLength": 1},
        },
        "additionalProperties": {"type": "string"},
        "allOf": [{"properties": {"zz": {"type": "integer"}}}, {"$ref": "#/$defs/limits"}],
        "if": {"properties": {"kind": {"const": "big"}}, "required": ["kind"]},
        "then": {"properties": {"score": {"minimum": big}}, "required": ["score"]},
        "else": {"properties": {"score": {"maximum": cap}}},
        "$defs": {
            NODE: {"type": "object", "required": ["v"],
                   "properties": {"v": {"type": "integer"},
                                  "kids": {"type": "array", "items": {"$ref": f"#/$defs/{NODE}"}}},
                   "additionalProperties": False},
            DEF: {"type": ["integer", "boolean"], "not": {"const": 0}},
            "limits": {"maxProperties": 20, "then": {"const": 1}},
        },
    }

    def base():
        return {
            "id": Num(str(R(1, 99999))),
            "name": "".join(rng.choice("abcdefgh") for _ in range(R(lo, hi))),
            "score": Num(rng.choice(["3", "2.5", "-5.5", "0"])),
            "tags": rng.sample(["red", "blu", "ok", "x", "yz", "abc"], R(1, maxtags - 1)),
            SL: rng.choice([Num("1"), "x", None]),
            TL: copy.deepcopy(const_v),
            "point": [Num("1.5"), Num(str(R(-9, 9)))],
            "tree": {"v": Num("1"), "kids": [{"v": Num("2")}, {"v": Num("3"), "kids": []}]},
            "kind": rng.choice(["small", None]),
            "flag": rng.choice([True, False]),
            "contact": rng.choice([{"email": "e@x"}, {"phone": "1"}]),
            "extra": rng.choice([[1, {}], "anything", Num("0")]),
            "qty": rng.choice([Num(str(qmin)), "ab", Num(str(qmin + 3) + ".0")]),
            "code": rng.choice(["", Num("5"), None]),
            "ratio": Num(str(ex_lo + 1)),
            UN: rng.choice(["é", ASTRAL]),
        }

    def setp(path, val):
        def f(d):
            cur = d
            for k in path[:-1]:
                cur = cur[k]
            cur[path[-1]] = val(d) if callable(val) else val
        return f

    def delp(*names):
        def f(d):
            for n in names:
                d.pop(n, None)
        return f

    # Each mutation targets one area; every case applies all of them (traps in every case).
    M = [
        ("id_float_int", setp(["id"], Num(rng.choice(["7.0", "1e2", "2.50e1", "1E1"])))),
        ("id_frac", setp(["id"], Num(rng.choice(["1.5", "2.25", "1e-1"])))),
        ("id_negzero", setp(["id"], Num("-0"))),
        ("id_max", setp(["id"], Num(rng.choice(["1000000", "1e6", "1000000.0"])))),
        ("id_str", setp(["id"], str(R(1, 9)))),
        ("id_bool", setp(["id"], True)),
        ("name_astral_ok", setp(["name"], ASTRAL * hi)),             # UTF-16 length 2*hi > hi
        ("name_astral_short", setp(["name"], ASTRAL * (lo - 1))),    # UTF-16 length looks long enough
        ("name_combining", setp(["name"], "é" * ((hi // 2) + 1))),
        ("name_long", setp(["name"], "q" * (hi + 1))),
        ("tags_dup_escape", setp(["tags"], ["ab", "a" + "b"])),
        ("tags_many", setp(["tags"], [f"t{i}" for i in range(9)] + ["long1", "ok", "x" * (taglen + 2)])),
        ("tags_unique_types", setp(["tags"], [Num("1"), True, "1"])),
        ("tags_dup_astral", setp(["tags"], [ASTRAL * 2, "z", ASTRAL + ASTRAL])),
        ("enum_obj_reordered", setp([SL], {"q": [Num("2.0"), "z"], "p": Num("1e0")})),
        ("enum_arr_bool_vs_num", setp([SL], [Num("1"), {"k": Num("1")}])),
        ("enum_true_vs_1", setp([SL], True)),
        ("enum_arr_ok", setp([SL], [Num("1.0"), {"k": True}])),
        ("enum_obj_extra", setp([SL], {"p": Num("1"), "q": [Num("2"), "z"], "r": None})),
        ("const_reordered", setp([TL], {"w": None, "v": UN, "u": [Num("1.0"), Num("25e-1")]})),
        ("const_missing_null", setp([TL], {"u": [Num("1"), Num("2.5")], "v": UN})),
        ("const_arr_order", setp([TL], {"u": [Num("2.5"), Num("1")], "v": UN, "w": None})),
        ("point_extra", setp(["point"], [Num("1"), Num("2"), Num("3"), "x"])),
        ("point_types", setp(["point"], ["x", Num("2.5")])),
        ("point_short", setp(["point"], [Num("1")])),
        ("point_obj", setp(["point"], {"0": 1})),
        ("tree_deep", setp(["tree"], {"v": Num("1"), "kids": [{"v": Num("2"), "kids": [
            {"v": Num("3.5")}, {"w": Num("4")}, {"v": Num("5"), "kids": [{"v": "6", "x/y": 1}]}]}]})),
        ("tree_kids_not_array", setp(["tree"], {"v": Num("1e0"), "kids": {"v": 1}})),
        ("kind_num", setp(["kind"], Num("5"))),
        ("flag_num", setp(["flag"], Num(rng.choice(["0", "1"])))),
        ("contact_both", setp(["contact"], {"email": "a", "phone": "b"})),
        ("contact_neither", setp(["contact"], {"fax": "c"})),
        ("contact_string", setp(["contact"], "email")),
        ("nothing", setp(["nothing"], None)),
        ("qty_low", setp(["qty"], Num(str(qmin - 1)))),
        ("qty_float_ok", setp(["qty"], Num(f"{qmin}.0"))),
        ("qty_str_long", setp(["qty"], "abc")),
        ("qty_astral_ok", setp(["qty"], ASTRAL * 2)),
        ("code_nonempty", setp(["code"], "x")),
        ("ratio_lo", setp(["ratio"], Num(str(ex_lo)))),
        ("ratio_hi", setp(["ratio"], Num(f"{ex_hi}.0"))),
        ("ratio_string_ok", setp(["ratio"], "not a number")),
        ("ratio_mid_ok", setp(["ratio"], Num(f"{ex_lo}.5"))),
        ("extra_props", setp(["zz"], Num("5"))),
        ("extra_zz_str", setp(["zz"], "five")),
        ("extra_unicode", lambda d: d.update({FW: Num("1"), ASTRAL: Num("2"), "Z": "ok", "zy": [], "a/~": None})),
        ("if_then_low", lambda d: d.update(kind="big", score=Num(str(big - 1)))),
        ("if_then_missing", lambda d: (d.update(kind="big"), d.pop("score", None))),
        ("if_then_ok", lambda d: d.update(kind="big", score=Num(f"{big}.0"))),
        ("else_high", lambda d: d.update(kind="Big", score=Num(str(cap + 1)))),
        ("else_no_kind", lambda d: (d.pop("kind", None), d.update(score=Num("1e2")))),
        ("score_bounds", setp(["score"], Num(rng.choice(["-5.6", "100.5", "-6"])))),
        ("missing_required", delp("id", SL, "tags")),
        ("child_rec", setp(["child"], {"id": Num("0"), "name": "ab", "tags": [], SL: "x",
                                        "child": {"id": Num("2"), "tags": "no", "name": "b"}})),
        ("meta_defs", setp(["meta"], {"a": Num("0"), "b": Num("-0"), "c": Num("0.0"), "d": False})),
        ("meta_defs_ok", setp(["meta"], {"k": Num("3"), "l": True})),
        ("meta_too_many", setp(["meta"], {"a": 1, "b": 2, "c": 3, "d": Num("4.5")})),
        ("meta_empty", setp(["meta"], {})),
        ("unicode_prop", setp([UN], "\U0001F600" * 2)),
        ("too_many_props", lambda d: d.update({f"p{i}": "s" for i in range(12)})),
        ("tilde_key_add", lambda d: d.update({"~1": Num("1"), "~01": "s", "a~1b": Num("2")})),
    ]

    instances = [base()]
    for name, f in M:
        inst = base()
        f(inst)
        instances.append(inst)
    # a few random combinations
    for _ in range(8):
        inst = base()
        for name, f in rng.sample(M, 3):
            try:
                f(inst)
            except (KeyError, TypeError):
                pass
        instances.append(inst)
    # non-object roots: type-specific keywords and if/then/else on them
    instances += [[Num("1"), Num("2")], "str", None, Num("3.0"), {}]
    rng.shuffle(instances)

    schema_text = dump_schema(schema, rng)
    inst_text = "[\n" + ",\n".join(dump(i, rng) for i in instances) + "\n]\n"
    return schema_text, inst_text


def dump_schema(schema, rng):
    """Indented, readable schema text (escapes still randomised)."""
    def go(v, ind):
        pad = "  " * ind
        if isinstance(v, dict) and v and any(isinstance(x, (dict, list)) for x in v.values()):
            parts = [f"{pad}  {dump_str(k, rng)}: {go(x, ind + 1).lstrip()}" for k, x in v.items()]
            return pad + "{\n" + ",\n".join(parts) + "\n" + pad + "}"
        return pad + dump(v, rng)
    return go(schema, 0) + "\n"


class Challenge(H.Challenge):
    name = "json_schema"
    title = "Validate JSON against a JSON Schema subset and report the exact failing keywords"
    category = "text"

    def prompt(self, ctx):
        return prompt_text()

    def case(self, seed, ctx):
        d = ctx.data_dir(self, seed)
        st, it = make_case(seed)
        (d / "schema.json").write_text(st, encoding="utf-8")
        (d / "instances.json").write_text(it, encoding="utf-8")
        return H.Case(args=[str(d)], expected=expected_output(st, it))
