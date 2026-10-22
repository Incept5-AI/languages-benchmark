import json

def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        r = {}
        for k in set(a) | set(b):
            if k in a and k in b:
                r[k] = merge(a[k], b[k])
            elif k in a:
                r[k] = a[k]
            else:
                r[k] = b[k]
        return r
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for x in a + b:
            if x not in out:
                out.append(x)
        return out
    return b

with open("__FIXTURE_DIR__/config_a.json") as f:
    a = json.load(f)
with open("__FIXTURE_DIR__/config_b.json") as f:
    b = json.load(f)
print(json.dumps(merge(a, b), indent=2, sort_keys=True))
