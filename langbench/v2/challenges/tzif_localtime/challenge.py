"""tzif_localtime — parse TZif v2/v3 files (RFC 8536) and convert UTC <-> local time, incl. the POSIX TZ footer.

Hard parts: skipping the v1 block correctly (all six counts) and using the 64-bit block, which has transitions
the 32-bit block cannot hold (pre-1901 LMT changes such as Alaska 1867 or Manila 1844, and Gaza's explicit
table up to 2086); time type 0 before the first transition; footer rules after the last transition (sign
inversion, `<+1030>` quoted names, explicit 30-minute DST, rule times of -1, 24, 50 and 2:45 hours, southern
hemisphere years); offsets with seconds (LMT); gaps and overlaps in both the table and the footer era, at their
exact boundaries; day-skips (Apia 2011, Kwajalein 1993).

Fixtures are copied verbatim from macOS /usr/share/zoneinfo (tzdata 2026c, "fat" files: the v1 block is fully
populated). The oracle is zoneinfo.ZoneInfo.from_file on the same bytes; the set of matching instants for a
LOCAL query is found by brute force and cross-checked against zoneinfo's PEP 495 fold semantics.
"""
import random
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
T1800 = -5364662400        # 1800-01-01T00:00:00Z
T2400 = 13569465600        # 2400-01-01T00:00:00Z

POOLS = [
    ["America_Anchorage", "Asia_Manila", "Asia_Kolkata", "America_Adak", "Europe_Sofia"],  # pre-1901, v2 only
    ["Asia_Gaza", "Asia_Hebron"],                    # explicit v2 table to 2086, then a /50 footer
    ["America_Nuuk"],                                # footer rule times -1 and 0
    ["America_Santiago", "Africa_Cairo", "Asia_Jerusalem"],   # footer rule times 24 / 26
    ["Australia_Lord_Howe", "Antarctica_Troll"],     # explicit DST offset (30 min / 2 h)
    ["Pacific_Chatham", "America_St_Johns", "Australia_Adelaide"],  # minute offsets / 2:45 rule times
    ["Pacific_Apia", "Pacific_Kwajalein"],           # skipped a whole day
    ["America_Sao_Paulo", "Asia_Kathmandu"],         # fixed offset after the table
    ["Europe_London", "America_New_York", "Europe_Dublin"],
]

PROMPT = """\
Write a {LANG} program that converts between UTC and local time using time zone files in the binary TZif
format (RFC 8536).

The program is run with one argument: a directory containing `queries.txt` and several TZif files (names
like `tz3.tzif`). The TZif files are the ONLY source of time zone truth: do not use the operating system's
time zone database or any zone name — the files may differ from it and the file names are not zone names.
You may parse the files yourself, or use a standard-library facility that loads time zone rules from the
bytes of a given file, as long as it reproduces the rules below exactly.

## The TZif files

All files are TZif version 2 or 3: a version-1 header and data block (32-bit times), followed by a second
header and data block with 64-bit transition times, followed by the footer: a newline, a POSIX TZ string, and
a newline. Each header is 44 bytes: magic `TZif`, a version byte, 15 unused bytes, then six 32-bit big-endian
counts in this order: isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt. A data block is: timecnt
transition times (4 bytes each in block 1, 8 bytes in block 2; signed big-endian Unix seconds, ascending),
timecnt one-byte type indices, typecnt 6-byte local time type records (signed 32-bit big-endian UTC offset
in seconds, one byte isdst, one byte index into the abbreviation characters), charcnt bytes of
NUL-terminated abbreviations, leapcnt leap-second records (4+4 bytes in block 1, 8+4 in block 2; there are
none in these files), isstdcnt bytes and isutcnt bytes (ignore their values).

Use ONLY the second (64-bit) data block and the footer — the first block cannot represent every transition
and is not sufficient. The local time type in effect at Unix time `t` is:
- before the first transition time: type 0;
- otherwise, if `t` is at or after the first transition and at or before the last one: the type of the
  latest transition whose time is `<= t` (a transition takes effect exactly at its time);
- after the last transition: given by the footer rule.

## The footer (POSIX TZ string)

`std offset [dst [offset] [,start[/time],end[/time]]]`, for example `EST5EDT,M3.2.0,M11.1.0` or
`<+1030>-10:30<+11>-11,M10.1.0,M4.1.0`.
- A name is 3 or more letters, or any characters between `<` and `>` (the brackets are not part of the
  abbreviation, so `<+1030>` is the abbreviation `+1030`).
- An offset is `[+|-]hh[:mm[:ss]]` and is the amount to ADD to local time to get UTC, so `EST5` means UTC
  offset -05:00 and `<+0545>-5:45` means +05:45.
- If the DST name is present without an offset, the DST UTC offset is the standard one plus one hour.
- If there is no DST part, the standard type applies forever after the last transition.
- start/end: these files only use the form `Mm.w.d`: day `d` (0 = Sunday … 6 = Saturday) of week `w`
  (1–5, where 5 means the last such day in the month) of month `m` (1–12). Week 1 is the first `d`-day of the
  month, week 2 the second, and so on.
- `/time` is `[+|-]hh[:mm[:ss]]`, default `02:00:00`; hours may be from -167 to 167 (so `/24`, `/50` and `/-1`
  occur), and the transition then lands on another day. The start (switch to DST) time is measured in the
  local standard time; the end (switch back) time is measured in local daylight time.
- DST is in effect from each year's start transition until that year's end transition; in the southern
  hemisphere the end comes earlier in the calendar year than the start. Evaluate the rule in the proleptic
  Gregorian calendar for any year.

## Queries and output

`queries.txt` has one query per line (ignore blank lines), fields separated by single spaces:
- `UTC <file> <t>` — `<t>` is a Unix time in seconds (may be negative). Print the local time:
  `<query> -> <local> <abbr>` where `<local>` is `YYYY-MM-DDTHH:MM:SS` followed by the UTC offset as `+HH:MM` or
  `-HH:MM`, with `:SS` appended only when the offset has a non-zero seconds part (zero is `+00:00`; an offset
  of -75 seconds is `-00:01:15`), and `<abbr>` is the type's abbreviation.
- `LOCAL <file> <YYYY-MM-DDTHH:MM:SS>` — a wall-clock time. Find every Unix time `t` whose local time
  (as computed for `UTC` queries) is exactly this wall-clock time.
  - Exactly one: print `<query> -> <t> <offset> <abbr>`.
  - Two (an overlap, when clocks were set back): print both, earlier `t` first, as
    `<query> -> <t1> <offset1> <abbr1> | <t2> <offset2> <abbr2>`.
  - None (a gap, when clocks were set forward): take the UTC offset that was in effect immediately BEFORE the
    transition that skipped this wall-clock time, and let `t = wall-clock − that offset` (this is the
    instant that the same reading on a clock not yet set forward would show). Print
    `<query> -> GAP <t> <local> <abbr>`, where `<local>` and `<abbr>` are the result of converting this `t`
    back to local time as for `UTC` queries.

`<query>` is the query line exactly as given. All times lie in the years 1800 to 2399. Print one line per
query, in input order, and nothing else.

Example (`queries.txt` lines on the left of `->`; `tz1.tzif` is a United Kingdom zone file):
```
UTC tz1.tzif 1700000000 -> 2023-11-14T22:13:20+00:00 GMT
UTC tz1.tzif -4000000000 -> 1843-03-31T16:52:05-00:01:15 LMT
LOCAL tz1.tzif 2050-03-27T01:30:00 -> GAP 2531957400 2050-03-27T02:30:00+01:00 BST
LOCAL tz1.tzif 2050-10-30T01:30:00 -> 2550702600 +01:00 BST | 2550706200 +00:00 GMT
LOCAL tz1.tzif 2050-06-01T12:00:00 -> 2537694000 +01:00 BST
```
"""


# ── oracle ───────────────────────────────────────────────────────────────────
def parse_v2(b: bytes):
    """Used only to pick interesting instants (the expected output comes from zoneinfo)."""
    def hdr(o):
        return struct.unpack(">6l", b[o + 20:o + 44])
    c = hdr(0)
    o = 44 + c[3] * 5 + c[4] * 6 + c[5] + c[2] * 8 + c[1] + c[0]
    _, _, _, tc, typ, _ = hdr(o)
    o += 44
    tr = list(struct.unpack(f">{tc}q", b[o:o + 8 * tc]))
    idx = list(b[o + 8 * tc:o + 9 * tc])
    o += 9 * tc
    types = [struct.unpack(">lBB", b[o + 6 * i:o + 6 * i + 6]) for i in range(typ)]
    return tr, idx, types


class Zone:
    def __init__(self, fname):
        self.raw = (FIX / fname).read_bytes()
        import io
        self.zi = ZoneInfo.from_file(io.BytesIO(self.raw), key=None)
        self.tr, self.idx, self.types = parse_v2(self.raw)
        assert self.types[0][1] == 0, fname   # type 0 non-DST: RFC 8536 "type 0" == zoneinfo's tti_before
        self.offsets = sorted({u for u, _, _ in self.types} | {self.off(t) for t in (T2400 - 86400 * 200,
                                                                                   T2400 - 86400 * 20)})

    def local(self, t):
        d = (EPOCH + timedelta(seconds=t)).astimezone(self.zi)
        return d, int(d.utcoffset().total_seconds()), d.tzname()

    def off(self, t):
        return self.local(t)[1]

    def footer_transitions(self, year):
        """Offset changes during a UTC year (footer era), found by hourly stepping + bisection."""
        start = int((datetime(year, 1, 1, tzinfo=timezone.utc) - EPOCH).total_seconds())
        out, prev = [], self.local(start)[1:]
        for t in range(start + 3600, start + 366 * 86400, 3600):
            cur = self.local(t)[1:]
            if cur != prev:
                lo, hi = t - 3600, t
                while hi - lo > 1:
                    m = (lo + hi) // 2
                    if self.local(m)[1:] == prev:
                        lo = m
                    else:
                        hi = m
                out.append(hi)
            prev = cur
        return out


def fmt_off(o):
    s = "-" if o < 0 else "+"
    o = abs(o)
    r = f"{s}{o // 3600:02d}:{o % 3600 // 60:02d}"
    return r + (f":{o % 60:02d}" if o % 60 else "")


def iso_local(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S") if d.year >= 1000 else None


def wall_ts(wall: str) -> int:
    d = datetime.fromisoformat(wall).replace(tzinfo=timezone.utc)
    return int((d - EPOCH).total_seconds())


def answer(z: Zone, q: str) -> str:
    kind, _, val = q.split(" ")
    if kind == "UTC":
        d, o, ab = z.local(int(val))
        return f"{q} -> {iso_local(d)}{fmt_off(o)} {ab}"
    L = wall_ts(val)
    hits = sorted({L - o for o in z.offsets if z.off(L - o) == o})
    naive = datetime.fromisoformat(val)
    f0 = int(naive.replace(tzinfo=z.zi, fold=0).timestamp())
    f1 = int(naive.replace(tzinfo=z.zi, fold=1).timestamp())
    if len(hits) == 1:
        assert f0 == f1 == hits[0], (q, hits, f0, f1)
        _, o, ab = z.local(hits[0])
        return f"{q} -> {hits[0]} {fmt_off(o)} {ab}"
    if len(hits) == 2:
        assert (f0, f1) == tuple(hits), (q, hits, f0, f1)     # PEP 495: fold=0 is the earlier instant
        parts = []
        for t in hits:
            _, o, ab = z.local(t)
            parts.append(f"{t} {fmt_off(o)} {ab}")
        return f"{q} -> {' | '.join(parts)}"
    assert not hits, (q, hits)
    # independent computation of "offset just before the transition that skipped it"
    trans = set(z.tr)
    if naive.year > 2030:
        for y in (naive.year - 1, naive.year, naive.year + 1):
            trans.update(z.footer_transitions(y))
    cands = [T for T in sorted(trans) if T + z.off(T - 1) <= L < T + z.off(T)]
    assert len(cands) == 1, (q, cands)
    t = L - z.off(cands[0] - 1)
    assert t == f0, (q, t, f0)                                 # PEP 495: fold=0 in a gap = offset before
    d, o, ab = z.local(t)
    return f"{q} -> GAP {t} {iso_local(d)}{fmt_off(o)} {ab}"


def wall(t: int) -> str:
    return (datetime(1970, 1, 1) + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S")


def zone_queries(z: Zone, label: str, rng: random.Random, special: str):
    qs = []
    U = lambda t: qs.append(f"UTC {label} {t}")
    Lq = lambda t: qs.append(f"LOCAL {label} {wall(t)}")
    tr = [t for t in z.tr if T1800 < t < T2400]
    # before the first transition (type 0)
    if z.tr[0] > T1800 + 86400 * 400:
        t = rng.randrange(T1800, z.tr[0] - 86400 * 2)
        U(t)
        Lq(t + z.types[0][0])

    def around(T, o1, o2, full):
        U(T - 1)
        U(T)
        if o2 > o1:         # gap [T+o1, T+o2)
            Lq(T + o1)
            if full:
                Lq(T + o1 + (o2 - o1) // 2 + rng.randrange(-60, 60) * ((o2 - o1) > 7200))
                Lq(T + o2)
        elif o2 < o1:       # overlap [T+o2, T+o1)
            Lq(T + o2)
            if full:
                Lq(T + o1 - 1)
                Lq(T + o1)

    def tinfo(T):
        return T, z.off(T - 1), z.off(T)

    # the most dramatic table transition (day skips, LMT changes), plus a random one
    if tr:
        big = max(tr, key=lambda T: (abs(z.off(T) - z.off(T - 1)), -T))
        around(*tinfo(big), True)
        changing = [T for T in tr if z.off(T) != z.off(T - 1) and T != big]
        if changing:
            around(*tinfo(rng.choice(changing)), rng.random() < .5)
    if special == "v2table":   # transitions only the 64-bit block has, between 2038 and 2086
        late = [T for T in tr if T > 2**31 and z.off(T) != z.off(T - 1)]
        for T in sorted(rng.sample(late, 3)):
            around(*tinfo(T), True)
        U(rng.choice(late) + rng.randrange(1, 86400 * 20))
        year = rng.randrange(2088, 2400)
    else:
        year = rng.randrange(2040, 2400)
    # footer era
    ft = z.footer_transitions(year)
    U(rng.randrange(2**31 + 86400 * 400, T2400 - 86400 * 400))
    for T in ft:
        around(*tinfo(T), True)
    tt = rng.randrange(2**31 + 86400 * 400, T2400 - 86400 * 400)
    Lq(tt + z.off(tt) + 3600 * 5)
    return qs


def make_case(seed: int):
    rng = random.Random(41300 + seed)
    files = {}
    blocks = []
    labels = [f"tz{i}.tzif" for i in range(1, len(POOLS) + 1)]
    rng.shuffle(labels)
    for pi, pool in enumerate(POOLS):
        fname = pool[(seed + pi) % len(pool)] if seed else pool[0]
        if seed == 0 and pi == len(POOLS) - 1:
            fname = "Europe_London"
        z = Zone(fname)
        label = labels[pi]
        files[label] = z.raw
        special = "v2table" if pi == 1 else ""
        qs = zone_queries(z, label, rng, special)
        blocks.append((z, qs))
    lines = []
    for z, qs in blocks:
        lines += [(z, q) for q in qs]
    rng.shuffle(lines)
    # dedupe while keeping order
    seen, uniq = set(), []
    for z, q in lines:
        if q not in seen:
            seen.add(q)
            uniq.append((z, q))
    text = "\n".join(q for _, q in uniq) + "\n"
    expected = "\n".join(answer(z, q) for z, q in uniq)
    return files, text, expected


class Challenge(H.Challenge):
    name = "tzif_localtime"
    title = "TZif v2/v3 + POSIX TZ footer: UTC to local and local to UTC with gaps and overlaps"
    category = "binary"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        files, text, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        for name, raw in files.items():
            (d / name).write_bytes(raw)
        (d / "queries.txt").write_text(text)
        return H.Case(args=[str(d)], expected=exp)
