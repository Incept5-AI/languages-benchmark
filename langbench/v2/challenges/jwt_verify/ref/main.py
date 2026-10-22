import base64, hashlib, hmac, json, sys, os

d = sys.argv[1]
cfg = json.load(open(os.path.join(d, "config.json"), encoding="utf-8"))
keys = {k["kid"]: k for k in cfg["keys"]}
ALPH = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
HASH = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def dec(s):
    if not s or len(s) % 4 == 1 or not set(s) <= ALPH:
        raise ValueError
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def check(tok):
    parts = tok.split(".")
    try:
        if len(parts) != 3:
            raise ValueError
        raw = [dec(p) for p in parts]
        h = json.loads(raw[0].decode("utf-8"))
        p = json.loads(raw[1].decode("utf-8"))
        if type(h) is not dict or type(p) is not dict:
            raise ValueError
    except Exception:
        return "MALFORMED"
    alg = h.get("alg")
    if not isinstance(alg, str) or alg not in HASH:
        return "UNSUPPORTED_ALG"
    kid = h.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        return "UNKNOWN_KEY"
    if keys[kid]["alg"] != alg:
        return "ALG_MISMATCH"
    mac = hmac.new(dec(keys[kid]["k"]), (parts[0] + "." + parts[1]).encode(), HASH[alg]).digest()
    if mac != raw[2]:
        return "BAD_SIGNATURE"
    now, lw = cfg["now"], cfg["leeway"]
    if "exp" in p and now >= p["exp"] + lw:
        return "EXPIRED"
    if "nbf" in p and now + lw < p["nbf"]:
        return "NOT_YET_VALID"
    if p.get("iss") != cfg["issuer"]:
        return "BAD_ISSUER"
    aud = p.get("aud")
    if not (aud == cfg["audience"] if isinstance(aud, str) else isinstance(aud, list) and cfg["audience"] in aud):
        return "BAD_AUDIENCE"
    sc = sorted({x for x in (p.get("scope") or "").split(" ") if x})
    return f"VALID sub={p['sub']} scope={','.join(sc)}"


out, v, n = [], 0, 0
for line in open(os.path.join(d, "tokens.txt"), encoding="utf-8").read().split("\n"):
    t = line.strip(" \t")
    if not t:
        continue
    n += 1
    r = check(t)
    v += r.startswith("VALID")
    out.append(f"{n}: {r}")
sys.stdout.buffer.write(("\n".join(out) + f"\n\nvalid={v} invalid={n - v}\n").encode("utf-8"))
