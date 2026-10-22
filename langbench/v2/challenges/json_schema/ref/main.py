import json
import os
import sys

d = sys.argv[1]
schema = json.load(open(os.path.join(d, "schema.json"), encoding="utf-8"))
instances = json.load(open(os.path.join(d, "instances.json"), encoding="utf-8"))


def kind(x):
    if x is None:
        return "null"
    if x is True or x is False:
        return "boolean"
    if isinstance(x, (int, float)):
        return "number"
    if isinstance(x, str):
        return "string"
    if isinstance(x, list):
        return "array"
    return "object"


def same(a, b):
    k = kind(a)
    if k != kind(b):
        return False
    if k == "array":
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if k == "object":
        return a.keys() == b.keys() and all(same(a[n], b[n]) for n in a)
    return a == b


def tok(t):
    return str(t).replace("~", "~0").replace("/", "~1")


def ref_target(r):
    if r == "#":
        return schema, "#"
    name = r[len("#/$defs/"):]
    real = name.replace("~1", "/").replace("~0", "~")
    return schema["$defs"][real], "#/$defs/" + name


def check(s, sl, x, il, errs):
    if s is True:
        return
    if s is False:
        errs.add((il, sl))
        return
    k = kind(x)
    if "type" in s:
        ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
        good = False
        for t in ts:
            if t == k or (t == "integer" and k == "number" and float(x).is_integer()):
                good = True
        if not good:
            errs.add((il, sl + "/type"))
    if "enum" in s and not any(same(x, e) for e in s["enum"]):
        errs.add((il, sl + "/enum"))
    if "const" in s and not same(x, s["const"]):
        errs.add((il, sl + "/const"))
    if k == "number":
        if "minimum" in s and not x >= s["minimum"]:
            errs.add((il, sl + "/minimum"))
        if "maximum" in s and not x <= s["maximum"]:
            errs.add((il, sl + "/maximum"))
        if "exclusiveMinimum" in s and not x > s["exclusiveMinimum"]:
            errs.add((il, sl + "/exclusiveMinimum"))
        if "exclusiveMaximum" in s and not x < s["exclusiveMaximum"]:
            errs.add((il, sl + "/exclusiveMaximum"))
    elif k == "string":
        if "minLength" in s and len(x) < s["minLength"]:
            errs.add((il, sl + "/minLength"))
        if "maxLength" in s and len(x) > s["maxLength"]:
            errs.add((il, sl + "/maxLength"))
    elif k == "array":
        if "minItems" in s and len(x) < s["minItems"]:
            errs.add((il, sl + "/minItems"))
        if "maxItems" in s and len(x) > s["maxItems"]:
            errs.add((il, sl + "/maxItems"))
        if s.get("uniqueItems") is True:
            dup = False
            for i in range(len(x)):
                for j in range(i):
                    if same(x[i], x[j]):
                        dup = True
            if dup:
                errs.add((il, sl + "/uniqueItems"))
        pre = s.get("prefixItems", [])
        for i in range(len(x)):
            if i < len(pre):
                check(pre[i], f"{sl}/prefixItems/{i}", x[i], f"{il}/{i}", errs)
            elif "items" in s:
                check(s["items"], sl + "/items", x[i], f"{il}/{i}", errs)
    elif k == "object":
        props = s.get("properties", {})
        for name, v in x.items():
            if name in props:
                check(props[name], f"{sl}/properties/{tok(name)}", v, f"{il}/{tok(name)}", errs)
            elif "additionalProperties" in s:
                check(s["additionalProperties"], sl + "/additionalProperties", v, f"{il}/{tok(name)}", errs)
        for j, name in enumerate(s.get("required", [])):
            if name not in x:
                errs.add((il, f"{sl}/required/{j}"))
        if "minProperties" in s and len(x) < s["minProperties"]:
            errs.add((il, sl + "/minProperties"))
        if "maxProperties" in s and len(x) > s["maxProperties"]:
            errs.add((il, sl + "/maxProperties"))
    for i, sub in enumerate(s.get("allOf", [])):
        check(sub, f"{sl}/allOf/{i}", x, il, errs)
    if "anyOf" in s:
        if not any(ok(sub, f"{sl}/anyOf/{i}", x, il) for i, sub in enumerate(s["anyOf"])):
            errs.add((il, sl + "/anyOf"))
    if "oneOf" in s:
        if [ok(sub, f"{sl}/oneOf/{i}", x, il) for i, sub in enumerate(s["oneOf"])].count(True) != 1:
            errs.add((il, sl + "/oneOf"))
    if "not" in s and ok(s["not"], sl + "/not", x, il):
        errs.add((il, sl + "/not"))
    if "if" in s:
        branch = "then" if ok(s["if"], sl + "/if", x, il) else "else"
        if branch in s:
            check(s[branch], f"{sl}/{branch}", x, il, errs)
    if "$ref" in s:
        target, loc = ref_target(s["$ref"])
        check(target, loc, x, il, errs)


def ok(s, sl, x, il):
    e = set()
    check(s, sl, x, il, e)
    return not e


out = []
nvalid = 0
for n, inst in enumerate(instances):
    errs = set()
    check(schema, "#", inst, "#", errs)
    if not errs:
        nvalid += 1
        out.append(f"{n}: valid")
    else:
        out.append(f"{n}: invalid ({len(errs)})")
        for a, b in sorted(errs):
            out.append(f"  {a} {b}")
out.append(f"valid={nvalid} invalid={len(instances) - nvalid}")
sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
