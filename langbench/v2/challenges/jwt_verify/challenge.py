"""jwt_verify — verify a file of JWTs against a JWK set (HS256/384/512), with an exact rule order.

Hard parts: HMAC-SHA-2 (by hand in Rust; CommonCrypto in C), strict base64url, signing input taken from the
raw segments (re-serialising the JSON breaks it), a JSON parser with \\u escapes, `aud` as string or array,
leeway arithmetic, and a precise order of checks.
"""
import base64
import hashlib
import hmac
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

NOW = 1767225600          # 2026-01-01T00:00:00Z
LEEWAY = 30
ISS = "https://auth.example.test"
AUD = "orders-api"
ALGS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}

PROMPT = """\
Write a {LANG} program that verifies JSON Web Tokens.

The program is run with one argument: a directory containing two files.

`config.json` — the verification settings and the key set, for example:
```json
{"now": 1767225600, "leeway": 30, "issuer": "https://auth.example.test", "audience": "orders-api",
 "keys": [{"kid": "k1", "kty": "oct", "alg": "HS256", "k": "c2VjcmV0LW9uZQ"}]}
```
Every key has string `kid`, `kty`, `alg` and `k` fields. Each key's `k` is the HMAC secret, base64url-encoded (RFC 4648 §5, no padding). Keys are looked up by `kid`.

`tokens.txt` — UTF-8 text with `\n` line endings, one compact-serialised JWT per line (`header.payload.signature`). Lines may have leading or
trailing spaces/tabs, which are not part of the token; skip lines that are empty after trimming. Number the
remaining lines 1, 2, 3, … in file order.

Check each token with the rules below, IN THIS ORDER, and report the first rule that fails:

1. `MALFORMED` — the token does not have exactly three dot-separated segments; or any segment is empty or is
   not valid base64url (only `A–Z a–z 0–9 - _`, no `=` padding, and a length that is not 1 more than a multiple
   of 4); or the decoded header or payload is not a JSON object. (Decoded JSON is UTF-8. Segments never have
   non-zero unused bits, and objects never repeat a key.)
2. `UNSUPPORTED_ALG` — the header's `alg` is missing, not a string, or not one of `HS256`, `HS384`, `HS512`
   (this includes `none`).
3. `UNKNOWN_KEY` — the header has no string `kid`, or no key in the key set has that `kid`.
4. `ALG_MISMATCH` — the key's `alg` differs from the header's `alg`.
5. `BAD_SIGNATURE` — the signature segment does not decode to exactly the HMAC (with the hash named by `alg`)
   of the ASCII signing input `<header segment>.<payload segment>`, keyed with the decoded secret. Use the
   segments exactly as they appear in the token.
6. `EXPIRED` — the payload has an `exp` claim and `now >= exp + leeway`.
7. `NOT_YET_VALID` — the payload has an `nbf` claim and `now + leeway < nbf`.
8. `BAD_ISSUER` — the payload's `iss` is missing or not exactly equal to the configured issuer.
9. `BAD_AUDIENCE` — the payload's `aud` is missing, or is a string not equal to the configured audience, or
   is an array that contains no element equal to it, or is any other JSON type.

`exp` and `nbf`, when present, are JSON numbers and may have a fractional part (compare numerically).

Output one line per token: `<n>: <RESULT>`. A token that passes every rule prints
`<n>: VALID sub=<sub> scope=<scopes>`, where `<sub>` is the payload's `sub` string (decode JSON escapes such as
`\\u00e9` and print it as UTF-8), and `<scopes>` is the payload's `scope` string split on spaces (ignoring empty pieces),
de-duplicated, sorted by Unicode code point and joined with `,` (an empty string or a missing `scope` gives an
empty `<scopes>`). Every token that reaches VALID has a string `sub`.

After the token lines print one blank line and then `valid=<V> invalid=<I>`.

Example output:
```
1: VALID sub=alice scope=orders:read,orders:write
2: EXPIRED
3: BAD_SIGNATURE

valid=1 invalid=2
```
"""


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    if not s or len(s) % 4 == 1 or any(c not in
                                       "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                                       for c in s):
        raise ValueError
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(tok: str, cfg: dict) -> str:
    keys = {k["kid"]: k for k in cfg["keys"]}
    parts = tok.split(".")
    try:
        if len(parts) != 3:
            raise ValueError
        raw = [b64u_dec(p) for p in parts]
        hdr = json.loads(raw[0].decode("utf-8"))
        pay = json.loads(raw[1].decode("utf-8"))
        if not isinstance(hdr, dict) or not isinstance(pay, dict):
            raise ValueError
    except Exception:
        return "MALFORMED"
    alg = hdr.get("alg")
    if not isinstance(alg, str) or alg not in ALGS:
        return "UNSUPPORTED_ALG"
    kid = hdr.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        return "UNKNOWN_KEY"
    key = keys[kid]
    if key["alg"] != alg:
        return "ALG_MISMATCH"
    mac = hmac.new(b64u_dec(key["k"]), f"{parts[0]}.{parts[1]}".encode(), ALGS[alg]).digest()
    if not hmac.compare_digest(mac, raw[2]):
        return "BAD_SIGNATURE"
    now, lw = cfg["now"], cfg["leeway"]
    if "exp" in pay and now >= pay["exp"] + lw:
        return "EXPIRED"
    if "nbf" in pay and now + lw < pay["nbf"]:
        return "NOT_YET_VALID"
    if pay.get("iss") != cfg["issuer"]:
        return "BAD_ISSUER"
    aud = pay.get("aud")
    if not ((isinstance(aud, str) and aud == cfg["audience"]) or
            (isinstance(aud, list) and cfg["audience"] in aud)):
        return "BAD_AUDIENCE"
    scopes = sorted(set(s for s in (pay.get("scope") or "").split(" ") if s != ""))
    # split on single spaces: consecutive spaces yield empty items, which are dropped above
    return f"VALID sub={pay['sub']} scope={','.join(scopes)}"


def expected_output(lines, cfg) -> str:
    toks = [l.strip(" \t") for l in lines]
    toks = [t for t in toks if t]
    res = [verify(t, cfg) for t in toks]
    out = [f"{i}: {r}" for i, r in enumerate(res, 1)]
    v = sum(1 for r in res if r.startswith("VALID"))
    out += ["", f"valid={v} invalid={len(res) - v}"]
    return "\n".join(out)


# ── generation ───────────────────────────────────────────────────────────────
def json_text(obj, rng, style):
    """Serialise with varied whitespace/key order so re-serialising cannot reproduce the segment."""
    if style == 0:
        return json.dumps(obj, separators=(",", ":"))
    if style == 1:
        return json.dumps(obj, indent=1)
    items = list(obj.items())
    rng.shuffle(items)
    return "{ " + " ,\n\t".join(f"{json.dumps(k)} :{json.dumps(v, ensure_ascii=rng.random() < .5)}"
                                 for k, v in items) + " }"


def make_case(seed: int):
    rng = random.Random(9000 + seed)
    names = ["alice", "bob", "carol", "dave", "Zoë Ångström", "李雷", "eve\"q", "frank\\b", "grace", "heidi", "😀 smile"]
    keys = []
    for i, alg in enumerate(["HS256", "HS384", "HS512", "HS256", "HS512"]):
        secret = bytes(rng.randrange(256) for _ in range(rng.choice([16, 32, 48, 64, 70])))
        keys.append({"kid": f"key-{seed}-{i}", "kty": "oct", "alg": alg, "k": b64u(secret), "_s": secret})
    cfg = {"now": NOW + seed * 86400, "leeway": LEEWAY, "issuer": ISS, "audience": AUD,
           "keys": [{k: v for k, v in key.items() if k != "_s"} for key in keys]}
    now = cfg["now"]

    def sign(hdr, pay, key, hstyle=None, pstyle=None, tamper=None):
        h = b64u(json_text(hdr, rng, rng.randrange(3) if hstyle is None else hstyle).encode())
        p = b64u(json_text(pay, rng, rng.randrange(3) if pstyle is None else pstyle).encode())
        alg = hdr.get("alg") if hdr.get("alg") in ALGS else "HS256"
        sig = hmac.new(key["_s"], f"{h}.{p}".encode(), ALGS[alg]).digest()
        if tamper == "sig":
            sig = bytes([sig[0] ^ 1]) + sig[1:]
        if tamper == "short":
            sig = sig[:-1]
        return f"{h}.{p}.{b64u(sig)}"

    def good_pay():
        scopes = rng.sample(["orders:read", "orders:write", "admin", "Orders:read", "billing", "a", "zz",
                             "orders:read"], rng.randrange(0, 5))
        p = {"sub": rng.choice(names), "iss": ISS, "aud": AUD if rng.random() < .5 else ["x", AUD],
             "iat": now - 100, "exp": now + rng.choice([5, 600, 3600]) + rng.choice([0, 0.25]),
             "scope": rng.choice([" ".join(scopes), "  ".join(scopes)])}
        if rng.random() < .3:
            del p["scope"]
        if rng.random() < .3:
            p["nbf"] = now + rng.choice([-10, 0, 29, 30])
        return p

    toks = []
    kinds = (["valid"] * 9 + ["sig", "short", "expired", "expired_edge", "leeway_ok", "nbf", "iss", "aud",
              "aud_list", "none", "rs256", "nokid", "unkkid", "mismatch", "dots", "pad", "badjson", "array",
              "badchar", "empty_seg", "noexp_valid", "resign_trap", "aud_missing", "iss_case"])
    rng.shuffle(kinds)
    for kind in kinds:
        key = rng.choice(keys)
        hdr = {"alg": key["alg"], "typ": "JWT", "kid": key["kid"]}
        if rng.random() < .5:
            hdr = {"kid": key["kid"], "alg": key["alg"]}
        pay = good_pay()
        t = None
        if kind == "sig":
            t = sign(hdr, pay, key, tamper="sig")
        elif kind == "short":
            t = sign(hdr, pay, key, tamper="short")
        elif kind == "expired":
            pay["exp"] = now - 1000
        elif kind == "expired_edge":
            pay["exp"] = now - LEEWAY          # now >= exp + leeway → expired exactly at the boundary
        elif kind == "leeway_ok":
            pay["exp"] = now - LEEWAY + 0.5   # still inside leeway
        elif kind == "nbf":
            pay["nbf"] = now + LEEWAY + rng.choice([1, 0.5])
        elif kind == "iss":
            pay["iss"] = ISS + "/"
        elif kind == "iss_case":
            pay["iss"] = ISS.upper()
        elif kind == "aud":
            pay["aud"] = "orders-api-v2"
        elif kind == "aud_list":
            pay["aud"] = ["billing", "Orders-api"]
        elif kind == "aud_missing":
            del pay["aud"]
        elif kind == "none":
            hdr["alg"] = "none"
            t = b64u(json_text(hdr, rng, 0).encode()) + "." + b64u(json_text(pay, rng, 0).encode()) + ".c2ln"
        elif kind == "rs256":
            hdr["alg"] = "RS256"
        elif kind == "nokid":
            del hdr["kid"]
        elif kind == "unkkid":
            hdr["kid"] = "key-x"
        elif kind == "mismatch":
            other = next(k for k in keys if k["alg"] != key["alg"])
            hdr["alg"] = other["alg"]          # signed correctly with the right alg, but key says otherwise
        elif kind == "dots":
            t = sign(hdr, pay, key) + ".e30"
        elif kind == "pad":
            t = sign(hdr, pay, key)
            h, p, s = t.split(".")
            t = f"{h}.{p}.{s}=" if len(s) % 4 else f"{h}=.{p}.{s}"
        elif kind == "badjson":
            h = b64u(b'{"alg":"HS256","kid":"' + key["kid"].encode() + b'"')
            t = f"{h}.{b64u(json.dumps(pay).encode())}.c2ln"
        elif kind == "array":
            t = f"{b64u(json.dumps(hdr).encode())}.{b64u(b'[1,2]')}.c2ln"
        elif kind == "badchar":
            t = sign(hdr, pay, key)
            t = t[:5] + "+" + t[6:]
        elif kind == "empty_seg":
            h, p, s = sign(hdr, pay, key).split(".")
            t = f"{h}..{s}"
        elif kind == "noexp_valid":
            pay.pop("exp", None)
        elif kind == "resign_trap":
            t = sign(hdr, pay, key, hstyle=2, pstyle=1)
        if t is None:
            t = sign(hdr, pay, key)
        toks.append(t)

    lines = []
    for t in toks:
        lines.append(rng.choice(["", " ", "\t", "  "]) + t + rng.choice(["", " ", "\t "]))
        if rng.random() < .15:
            lines.append(rng.choice(["", "   ", "\t"]))
    text = "\n".join(lines) + "\n"
    return cfg, text, expected_output(text.split("\n"), cfg)


class Challenge(H.Challenge):
    name = "jwt_verify"
    title = "Verify JWTs (HS256/384/512) against a JWK set with ordered rules"
    category = "text"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        cfg, text, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        (d / "config.json").write_text(json.dumps(cfg, indent=2))
        (d / "tokens.txt").write_text(text)
        return H.Case(args=[str(d)], expected=exp)
