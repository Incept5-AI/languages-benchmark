import os
import sys


def parse(s):
    """'[-]d+[.d+]' -> (numerator, scale) meaning numerator / 10**scale."""
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    ip, _, fp = s.partition(".")
    n = int(ip + fp)
    return (-n if neg else n), len(fp)


def div_half_even(num, den):
    """round(num / den) with ties to even, symmetric for negatives (den > 0)."""
    neg = num < 0
    q, r = divmod(abs(num), den)
    if 2 * r > den or (2 * r == den and q % 2 == 1):
        q += 1
    return -q if neg else q


def to_units(s, m):
    n, sc = parse(s)
    if sc <= m:
        return n * 10 ** (m - sc)
    return div_half_even(n, 10 ** (sc - m))


def mul_units(units, m_from, rate, m_to):
    n, sc = parse(rate)
    return div_half_even(units * n * 10 ** m_to, 10 ** (m_from + sc))


def fmt(units, m):
    neg = units < 0
    s = str(abs(units)).rjust(m + 1, "0")
    ip, fp = (s[:-m], s[-m:]) if m else (s, "")
    parts = []
    while len(ip) > 3:
        parts.append(ip[-3:])
        ip = ip[:-3]
    parts.append(ip)
    out = ",".join(reversed(parts)) + ("." + fp if m else "")
    return ("-" + out) if neg and units != 0 else out


minor = {}
acc = {}          # name -> [currency, balance units, limit units]
rej = []


def run(f):
    op = f[0]
    if op == "currency":
        minor[f[1]] = int(f[2])
        return None
    if op == "open":
        if f[1] in acc:
            return "account exists " + f[1]
        lim = to_units(f[3], minor[f[2]]) if len(f) > 3 else 0
        acc[f[1]] = [f[2], 0, lim]
        return None
    if op == "split":
        names = [f[1]] + [t.split(":")[0] for t in f[3:]]
    elif op in ("transfer", "convert"):
        names = [f[1], f[2]]
    else:
        names = [f[1]]
    for n in names:
        if n not in acc:
            return "unknown account " + n
    if op in ("transfer", "split") and any(acc[n][0] != acc[f[1]][0] for n in names):
        return "currency mismatch"
    a = acc[f[1]]
    m = minor[a[0]]
    if op == "interest":
        a[1] += mul_units(a[1], m, f[2], m)
        return None
    amt = to_units(f[3] if op in ("transfer", "convert") else f[2], m)
    if amt <= 0:
        return "non-positive amount"
    if op == "deposit":
        a[1] += amt
        return None
    if a[1] - amt < -a[2]:
        return "insufficient funds " + f[1]
    a[1] -= amt
    if op == "transfer":
        acc[f[2]][1] += amt
    elif op == "convert":
        t = acc[f[2]]
        t[1] += mul_units(amt, m, f[4], minor[t[0]])
    elif op == "split":
        targets = [t.split(":") for t in f[3:]]
        ws = [int(w) for _, w in targets]
        W = sum(ws)
        share = [amt * w // W for w in ws]
        rem = [amt * w % W for w in ws]
        left = amt - sum(share)
        idx = sorted(range(len(ws)), key=lambda i: (-rem[i], i))[:left]
        for i in idx:
            share[i] += 1
        for (n, _), s in zip(targets, share):
            acc[n][1] += s
    return None


text = open(os.path.join(sys.argv[1], "ledger.txt"), encoding="utf-8").read()
for i, line in enumerate(text.split("\n"), 1):
    f = line.split()
    if not f or f[0].startswith("#"):
        continue
    r = run(f)
    if r:
        rej.append(f"line {i}: REJECT {r}")
out = rej + [""]
for n in sorted(acc, key=lambda x: x.encode()):
    c, b, _ = acc[n]
    out.append(f"{n} {c} {fmt(b, minor[c])}")
out.append("")
for c in sorted({v[0] for v in acc.values()}):
    bs = [v[1] for v in acc.values() if v[0] == c]
    out.append(f"total {c} {fmt(sum(bs), minor[c])} ({len(bs)} accounts)")
print("\n".join(out))
