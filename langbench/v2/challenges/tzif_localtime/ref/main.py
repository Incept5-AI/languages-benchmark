import os
import struct
import sys
from datetime import date, datetime, timedelta

D = sys.argv[1]
EPOCH = datetime(1970, 1, 1)


def parse_off(s, i):
    """Parse [+|-]hh[:mm[:ss]] at s[i:]; returns (seconds, next index)."""
    sign = 1
    if i < len(s) and s[i] in "+-":
        sign = -1 if s[i] == "-" else 1
        i += 1
    parts = []
    while True:
        j = i
        while j < len(s) and s[j].isdigit():
            j += 1
        parts.append(int(s[i:j]))
        i = j
        if i < len(s) and s[i] == ":" and len(parts) < 3:
            i += 1
            continue
        break
    while len(parts) < 3:
        parts.append(0)
    return sign * (parts[0] * 3600 + parts[1] * 60 + parts[2]), i


def parse_name(s, i):
    if s[i] == "<":
        j = s.index(">", i)
        return s[i + 1:j], j + 1
    j = i
    while j < len(s) and s[j].isalpha():
        j += 1
    return s[i:j], j


def parse_rule(r):
    spec, _, tm = r.partition("/")
    m, w, d = (int(x) for x in spec[1:].split("."))
    t = parse_off(tm, 0)[0] if tm else 7200
    return m, w, d, t


class Footer:
    def __init__(self, s):
        self.std, i = parse_name(s, 0)
        o, i = parse_off(s, i)
        self.stdoff = -o
        self.dst = None
        if i < len(s):
            self.dst, i = parse_name(s, i)
            if i < len(s) and s[i] != ",":
                o, i = parse_off(s, i)
                self.dstoff = -o
            else:
                self.dstoff = self.stdoff + 3600
            a, b = s[i + 1:].split(",")
            self.start, self.end = parse_rule(a), parse_rule(b)

    @staticmethod
    def rule_day(y, m, w, d):
        first = date(y, m, 1)
        day = 1 + (d - (first.weekday() + 1) % 7) % 7
        day += 7 * (w - 1)
        nd = (date(y + (m == 12), m % 12 + 1, 1) - first).days
        while day > nd:
            day -= 7
        return (date(y, m, day) - date(1970, 1, 1)).days * 86400

    def events(self, y):
        m, w, d, t = self.start
        s = self.rule_day(y, m, w, d) + t - self.stdoff
        m, w, d, t = self.end
        e = self.rule_day(y, m, w, d) + t - self.dstoff
        return [(s, True), (e, False)]

    def at(self, t):
        if self.dst is None:
            return self.stdoff, self.std
        y = (EPOCH + timedelta(seconds=t)).year
        ev = sorted(self.events(y - 1) + self.events(y) + self.events(y + 1))
        cur = None
        for et, isd in ev:
            if et <= t:
                cur = isd
        return (self.dstoff, self.dst) if cur else (self.stdoff, self.std)

    def transitions(self, y):
        return [et for yy in (y - 1, y, y + 1) for et, _ in self.events(yy)] if self.dst else []


class Zone:
    def __init__(self, b):
        h = lambda o: struct.unpack(">6l", b[o + 20:o + 44])
        isut, isstd, leap, tc, typ, ch = h(0)
        o = 44 + tc * 5 + typ * 6 + ch + leap * 8 + isstd + isut
        isut, isstd, leap, tc, typ, ch = h(o)
        o += 44
        self.tr = list(struct.unpack(f">{tc}q", b[o:o + 8 * tc]))
        o += 8 * tc
        self.idx = list(b[o:o + tc])
        o += tc
        recs = [struct.unpack(">lBB", b[o + 6 * i:o + 6 * i + 6]) for i in range(typ)]
        o += 6 * typ
        chars = b[o:o + ch]
        o += ch + leap * 12 + isstd + isut
        ab = lambda i: chars[i:chars.index(b"\0", i)].decode()
        self.types = [(u, ab(a)) for u, _, a in recs]
        foot = b[o:].decode().strip("\n")
        self.footer = Footer(foot) if foot else None
        self.offsets = {u for u, _ in self.types}
        if self.footer:
            self.offsets.add(self.footer.stdoff)
            if self.footer.dst:
                self.offsets.add(self.footer.dstoff)

    def at(self, t):
        if not self.tr or t < self.tr[0]:
            if not self.tr and self.footer:
                return self.footer.at(t)
            return self.types[0]
        if t > self.tr[-1] and self.footer:
            return self.footer.at(t)
        lo, hi = 0, len(self.tr) - 1
        while lo < hi:
            m = (lo + hi + 1) // 2
            if self.tr[m] <= t:
                lo = m
            else:
                hi = m - 1
        return self.types[self.idx[lo]]


def fmt_off(o):
    s = "-" if o < 0 else "+"
    o = abs(o)
    r = f"{s}{o // 3600:02d}:{o % 3600 // 60:02d}"
    return r + (f":{o % 60:02d}" if o % 60 else "")


def local_str(z, t):
    o, ab = z.at(t)
    return (EPOCH + timedelta(seconds=t + o)).strftime("%Y-%m-%dT%H:%M:%S") + fmt_off(o), ab


zones = {}
out = []
for line in open(os.path.join(D, "queries.txt"), encoding="utf-8").read().split("\n"):
    if not line.strip():
        continue
    kind, f, v = line.split(" ")
    if f not in zones:
        zones[f] = Zone(open(os.path.join(D, f), "rb").read())
    z = zones[f]
    if kind == "UTC":
        s, ab = local_str(z, int(v))
        out.append(f"{line} -> {s} {ab}")
        continue
    wd = datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    L = int((wd - EPOCH).total_seconds())
    hits = sorted({L - o for o in z.offsets if z.at(L - o)[0] == o})
    if hits:
        out.append(f"{line} -> " + " | ".join(f"{t} {fmt_off(z.at(t)[0])} {z.at(t)[1]}" for t in hits))
        continue
    cand = set(z.tr)
    if z.footer:
        cand.update(z.footer.transitions(wd.year))
    for T in sorted(cand):
        a, b = z.at(T - 1)[0], z.at(T)[0]
        if T + a <= L < T + b:
            t = L - a
            s, ab = local_str(z, t)
            out.append(f"{line} -> GAP {t} {s} {ab}")
            break
    else:
        out.append(f"{line} -> GAP ?")
sys.stdout.write("\n".join(out) + "\n")
