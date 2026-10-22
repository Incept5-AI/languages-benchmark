"""decimal_ledger — an exact decimal ledger: big amounts, HALF_EVEN at stated points, overdrafts, splits.

Hard parts: amounts with 36+ integer digits and 6 decimals (beyond 64-bit, beyond 128-bit once scaled, beyond
the 38 significant digits of Swift's Foundation.Decimal), products with 12-decimal rates, HALF_EVEN applied to
the exact value including negative ties, amounts that round to zero (rejected), largest-remainder splits with
tied remainders, overdraft limits at the exact boundary, an ordered set of rejection rules, byte-order sorting
and a pinned number format (thousands separators, no "-0").

Oracle: Python `decimal` at 200 digits of precision with ROUND_HALF_EVEN and quantize; the reference solution
uses scaled integers instead.
"""
import random
import sys
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = """\
Write a {LANG} program that runs a multi-currency ledger with exact decimal arithmetic.

The program is run with one argument: a directory containing `ledger.txt`. Lines are numbered from 1 (every
line counts, including blank and comment lines). Blank lines and lines whose first non-space character is `#`
are ignored. Other lines are commands whose fields are separated by one or more spaces.

Numbers in commands are written `[-]digits[.digits]` (for example `000123.450000` or `-0.5`). Amounts can have
up to 45 integer digits and up to 8 decimal places, rates up to 12 decimal places, so values do not fit in
64- or 128-bit integers or in floating point; all arithmetic must be exact.

**Rounding.** "Round" always means: round the exact value to the currency's number of minor units (decimal
places) using round-half-even — to the nearest multiple of the minor unit, and when exactly halfway, to the one
whose last digit is even. This is symmetric for negative values (`-0.125` rounds to `-0.12` with 2 minor units,
`-0.135` to `-0.14`).

## Commands

- `currency <CODE> <minor units>` — declares a currency (minor units 0–6). Always valid; appears before use.
- `open <account> <CODE> [<limit>]` — opens an account with balance 0 in a declared currency. `<limit>`
  (default 0, rounded) is its overdraft limit: the balance may go down to `-limit` but not below.
- `deposit <account> <amount>` — adds the rounded amount.
- `withdraw <account> <amount>` — subtracts the rounded amount.
- `transfer <from> <to> <amount>` — moves the rounded amount; both accounts must have the same currency.
- `convert <from> <to> <amount> <rate>` — the amount, rounded in `from`'s currency, is subtracted from `from`;
  `to` receives that rounded amount multiplied by `rate`, rounded in `to`'s currency (it may be 0).
- `interest <account> <rate>` — adds the account's balance multiplied by `rate`, rounded (rate may be
  negative, and the balance may be negative).
- `split <from> <amount> <to1>:<w1> <to2>:<w2> …` — weights are positive integers. The rounded amount `A` is
  subtracted from `from` and shared between the targets, all of which must have `from`'s currency. Working
  in minor units (`A` as an integer count of them, `W` the sum of weights), target `i` first gets
  `floor(A × wi / W)`; the minor units still left over are then given one each to the targets with the
  largest remainders `(A × wi) mod W`, equal remainders going to the target listed first.

Amounts are rounded at the start of the command, before any check. Account names are made of ASCII
letters, digits and `_`. Within one command all account names are different.

**Rejections.** A command that breaks a rule is rejected and changes nothing. Check in this order and report
the first failure:
1. `unknown account <name>` — the first account name in the command (in order of appearance) that has not
   been opened; for `open`: `account exists <name>` if it is already open.
2. `currency mismatch` — `transfer` or `split` between accounts with different currencies.
3. `non-positive amount` — the rounded amount of `deposit`, `withdraw`, `transfer`, `convert` or `split` is
   zero or negative.
4. `insufficient funds <name>` — the amount to subtract would take the account (`from`, or the account of a
   `withdraw`) below minus its overdraft limit.

## Output

First one line per rejected command, in order: `line <n>: REJECT <reason>`. Then a blank line, then one line
per account sorted by name (byte-wise ascending, so upper case sorts before lower case):
`<account> <CODE> <balance>`. Then a blank line, then one line per currency that has at least one account,
sorted by code: `total <CODE> <sum of its balances> (<number of accounts> accounts)`.

Money is printed with exactly the currency's minor units: an optional `-`, the integer part with a `,`
between every group of three digits counting from the right, then (if minor units > 0) `.` and the decimals.
Zero is never negative. Examples with 2 minor units: `0.00`, `-0.05`, `1,234,567.80`, `-999.10`; with 0:
`12,000`.

Example `ledger.txt`:
```
currency USD 2
currency JPY 0
open alice USD
open bob USD 100
open zed JPY
deposit alice 1000.005
withdraw bob 100.004
withdraw bob 0.01
convert alice zed 10.5 151.123456789012
interest bob 0.125
split alice 0.05 bob:1 carol:1
split alice 0.05 bob:1 zed:1
```
Output:
```
line 8: REJECT insufficient funds bob
line 11: REJECT unknown account carol
line 12: REJECT currency mismatch

alice USD 989.50
bob USD -112.50
zed JPY 1,587

total JPY 1,587 (1 accounts)
total USD 877.00 (2 accounts)
```
(1000.005 rounds to 1000.00; 100.004 to 100.00; 10.50 × 151.123456789012 = 1586.796… rounds to 1587;
-100.00 × 0.125 = -12.5 rounds to -12.50.)
"""


# ── oracle ───────────────────────────────────────────────────────────────────
class Ledger:
    def __init__(self):
        self.minor, self.acct, self.rejects = {}, {}, []

    def q(self, x: Decimal, cur):
        return x.quantize(Decimal(1).scaleb(-self.minor[cur]), rounding=ROUND_HALF_EVEN)

    def run(self, text: str) -> str:
        with localcontext() as c:
            c.prec = 300
            for n, line in enumerate(text.split("\n"), 1):
                f = line.split()
                if not f or f[0].startswith("#"):
                    continue
                r = self.cmd(f)
                if r:
                    self.rejects.append(f"line {n}: REJECT {r}")
            out = list(self.rejects) + [""]
            for name in sorted(self.acct, key=lambda s: s.encode()):
                cur, bal, _ = self.acct[name]
                out.append(f"{name} {cur} {self.fmt(bal, cur)}")
            out.append("")
            for cur in sorted({a[0] for a in self.acct.values()}):
                bals = [a[1] for a in self.acct.values() if a[0] == cur]
                out.append(f"total {cur} {self.fmt(sum(bals, Decimal(0)), cur)} ({len(bals)} accounts)")
            return "\n".join(out)

    def fmt(self, v: Decimal, cur):
        m = self.minor[cur]
        units = int(v.scaleb(m))
        assert Decimal(units).scaleb(-m) == v
        neg, units = units < 0, abs(units)
        s = str(units).rjust(m + 1, "0")
        ip, fp = s[:len(s) - m] if m else s, s[len(s) - m:] if m else ""
        groups = []
        while len(ip) > 3:
            groups.insert(0, ip[-3:])
            ip = ip[:-3]
        groups.insert(0, ip)
        return ("-" if neg else "") + ",".join(groups) + ("." + fp if m else "")

    def cmd(self, f):
        op = f[0]
        A = self.acct
        if op == "currency":
            self.minor[f[1]] = int(f[2])
            return None
        if op == "open":
            if f[1] in A:
                return f"account exists {f[1]}"
            lim = self.q(Decimal(f[3]), f[2]) if len(f) > 3 else Decimal(0)
            A[f[1]] = [f[2], self.q(Decimal(0), f[2]), lim]
            return None
        names = {"deposit": [1], "withdraw": [1], "transfer": [1, 2], "convert": [1, 2], "interest": [1]}.get(op)
        if op == "split":
            names = [1] + list(range(3, len(f)))
            accts = [f[1]] + [x.split(":")[0] for x in f[3:]]
        else:
            accts = [f[i] for i in names]
        for a in accts:
            if a not in A:
                return f"unknown account {a}"
        if op in ("transfer", "split") and len({A[a][0] for a in accts}) > 1:
            return "currency mismatch"
        src = f[1]
        cur = A[src][0]
        if op == "interest":
            A[src][1] += self.q(A[src][1] * Decimal(f[2]), cur)
            return None
        amt = self.q(Decimal(f[3] if op in ("transfer", "convert") else f[2]), cur)
        if amt <= 0:
            return "non-positive amount"
        if op == "deposit":
            A[src][1] += amt
            return None
        if A[src][1] - amt < -A[src][2]:
            return f"insufficient funds {src}"
        A[src][1] -= amt
        if op == "transfer":
            A[f[2]][1] += amt
        elif op == "convert":
            A[f[2]][1] += self.q(amt * Decimal(f[4]), A[f[2]][0])
        elif op == "split":
            m = self.minor[cur]
            units = int(amt.scaleb(m))
            ws = [int(x.split(":")[1]) for x in f[3:]]
            W = sum(ws)
            shares = [(units * w) // W for w in ws]
            rems = [(units * w) % W for w in ws]
            left = units - sum(shares)
            order = sorted(range(len(ws)), key=lambda i: (-rems[i], i))
            for i in order[:left]:
                shares[i] += 1
            for x, s in zip(f[3:], shares):
                A[x.split(":")[0]][1] += Decimal(s).scaleb(-m)
        return None


# ── generation ───────────────────────────────────────────────────────────────
CURS = [("USD", 2), ("JPY", 0), ("KWD", 3), ("CLF", 4), ("EUR", 2), ("BHD", 3)]
NAMES = ["alice", "bob", "carol", "Dave", "erin", "Frank", "grace", "heidi", "ivan", "judy", "Mallory", "oscar",
         "peggy", "trent", "victor", "walter", "Zed", "acct_9", "ACME_corp", "x1"]


def num(rng, int_digits, dp, trailing=False):
    ip = str(rng.randrange(10 ** (int_digits - 1), 10 ** int_digits)) if int_digits > 0 else "0"
    s = ip
    if dp:
        s += "." + "".join(rng.choice("0123456789") for _ in range(dp))
    if trailing:
        s = rng.choice(["", "00", "0"]) + s + rng.choice(["", "0", "00"])
        if "." not in s:
            s += "." + "0" * rng.randrange(1, 4)
    return s


def tie_amount(rng, m, int_digits):
    """A positive amount exactly halfway between two multiples of the minor unit, with an odd or even
    preceding digit."""
    ip = str(rng.randrange(10 ** (int_digits - 1), 10 ** int_digits)) if int_digits else "0"
    if m == 0:
        return ip + ".5"
    return ip + "." + "".join(rng.choice("0123456789") for _ in range(m)) + "5"


def P(d: Decimal) -> str:
    """Plain notation (never exponent form)."""
    return format(d, "f")


def make_case(seed: int):
    with localcontext() as c:
        c.prec = 300                     # the generator's own boundary arithmetic must be exact too
        return _make_case(seed)


def _make_case(seed: int):
    rng = random.Random(17170 + seed)
    curs = rng.sample(CURS[2:], 2) + CURS[:2]
    rng.shuffle(curs)
    lines = ["# ledger " + str(seed)]
    for c, m in curs:
        lines.append(f"currency {c} {m}")
    led = Ledger()
    for l in lines:
        led.cmd(l.split()) if not l.startswith("#") else None
    names = rng.sample(NAMES, 10)
    acc_cur = {}
    for i, n in enumerate(names):
        c, m = curs[i % len(curs)]
        acc_cur[n] = (c, m)
        lim = ""
        if i % 3 == 1:
            lim = " " + num(rng, rng.randrange(1, 4), rng.randrange(0, 4))
        lines.append(f"open {n} {c}{lim}")
    lines.append(f"open {names[3]} {curs[0][0]}")                       # account exists
    lines.append("")
    by_cur = {}
    for n in names:
        by_cur.setdefault(acc_cur[n][0], []).append(n)

    def emit(s):
        lines.append(s)
        led_lines.append(s)

    led_lines = []
    # big deposits: 30-40 integer digits with 6 decimals, ties
    for n in names:
        c, m = acc_cur[n]
        emit(f"deposit {n}{' ' * rng.randrange(1, 3)}{num(rng, rng.randrange(30, 41), rng.randrange(0, 7), True)}")
    for _ in range(3):
        n = rng.choice(names)
        emit(f"deposit {n} {tie_amount(rng, acc_cur[n][1], rng.randrange(1, 5))}")
    pool = [n for n in names]
    ops = []
    for _ in range(30):
        k = rng.choice(["deposit", "withdraw", "transfer", "convert", "interest", "split", "tie_interest",
                        "small_convert", "comment"])
        ops.append(k)
    ops += ["reject_unknown", "reject_unknown2", "reject_mismatch", "reject_zero", "reject_neg", "reject_funds",
            "overdraft_edge", "split_tie", "split_mismatch", "huge_interest", "neg_frac", "neg_frac",
            "tie_q3_neg", "tie_q1_neg", "tie_q3_pos", "tie_q1_pos"]
    rng.shuffle(ops)
    for k in ops:
        # the state is simulated so that ties and boundaries can be targeted
        state = Ledger()
        state.run("\n".join(lines))
        A = state.acct
        n = rng.choice(pool)
        c, m = acc_cur[n]
        same = [x for x in by_cur[c] if x != n]
        other = [x for x in names if acc_cur[x][0] != c]
        if k == "deposit":
            emit(f"deposit {n} {num(rng, rng.randrange(1, 38), rng.randrange(0, 7))}")
        elif k == "withdraw":
            emit(f"withdraw {n} {num(rng, rng.randrange(1, 30), rng.randrange(0, 7))}")
        elif k == "transfer" and same:
            emit(f"transfer {n} {rng.choice(same)} {num(rng, rng.randrange(1, 32), rng.randrange(0, 7))}")
        elif k == "convert" and other:
            rate = num(rng, rng.randrange(0, 4), 12)
            emit(f"convert {n} {rng.choice(other)} {num(rng, rng.randrange(5, 33), rng.randrange(0, 7))} {rate}")
        elif k == "small_convert" and other:
            t = rng.choice(other)
            # ties: an odd number of minor units times a rate with a 5 in the right place
            mt = acc_cur[t][1]
            units = rng.randrange(1, 500) * 2 + 1
            amt = P(Decimal(units).scaleb(-m))
            rate = P(Decimal(5).scaleb(m - mt - 1) * rng.choice([1, 3, 7]))
            emit(f"convert {n} {t} {amt} {rate}")
        elif k == "interest":
            emit(f"interest {n} {rng.choice(['', '-'])}0.{''.join(rng.choice('0123456789') for _ in range(12))}")
        elif k == "huge_interest":
            emit(f"interest {n} {rng.randrange(1, 9)}.{''.join(rng.choice('0123456789') for _ in range(12))}")
        elif k == "neg_frac":
            # a negative product whose fractional part (in minor units) exceeds one half in magnitude:
            # truncating toward zero or flooring the magnitude gives a different result than rounding
            pos = [x for x in names if A[x][1] > 0]
            n = rng.choice(pos)
            m = acc_cur[n][1]
            while True:
                r = "-0." + "".join(rng.choice("0123456789") for _ in range(12))
                v = A[n][1] * Decimal(r) * Decimal(10) ** m
                frac = -v - int(-v)
                if Decimal("0.5") < frac < Decimal("0.95"):
                    break
            emit(f"interest {n} {r}")
        elif k.startswith("tie_q"):
            # balance |units| = 3 or 1 (mod 4), times +-0.5 → a tie whose even neighbour is away from / toward 0
            pos = [x for x in names if A[x][1] > 0]
            n = rng.choice(pos)
            m = acc_cur[n][1]
            units = int(A[n][1].scaleb(m))
            want = 3 if k.startswith("tie_q3") else 1
            add = (want - units) % 4
            if add:
                emit(f"deposit {n} {P(Decimal(add).scaleb(-m))}")
            emit(f"interest {n} {'-' if k.endswith('neg') else ''}{rng.choice(['0.5', '0.50', '0.500000'])}")
        elif k in ("tie_interest", "neg_interest_tie"):
            # make the balance an odd number of minor units, then apply +-0.5 → exact tie
            bal = A[n][1]
            units = int(bal.scaleb(m))
            if units % 2 == 0:
                emit(f"deposit {n} {P(Decimal(1).scaleb(-m))}")
            emit(f"interest {n} {'-' if k == 'neg_interest_tie' else ''}{rng.choice(['0.5', '0.50', '1.5'])}")
        elif k == "split" and len(same) >= 2:
            ts = rng.sample(same, min(len(same), rng.randrange(2, 4)))
            emit(f"split {n} {num(rng, rng.randrange(1, 35), rng.randrange(0, 7))} " +
                 " ".join(f"{t}:{rng.randrange(1, 9)}" for t in ts))
        elif k == "split_tie" and len(same) >= 2:
            ts = rng.sample(same, min(3, len(same)))
            ws = [1] * len(ts) if len(ts) == 3 else [1, 1]
            # 3 equal weights and A = 3k + 2 minor units: two units left over, equal remainders → first two
            units = 3 * rng.randrange(10 ** 20, 10 ** 21) + (2 if len(ts) == 3 else 1)
            emit(f"split {n} {P(Decimal(units).scaleb(-m))} " + " ".join(f"{t}:{w}" for t, w in zip(ts, ws)))
        elif k == "split_mismatch" and same and other:
            emit(f"split {n} 1 {same[0]}:1 {rng.choice(other)}:2")
        elif k == "reject_unknown":
            emit(f"transfer {n} {rng.choice(['mallory', 'Bob', 'nobody', 'alice_'])} 5")
        elif k == "reject_unknown2":
            emit(f"convert ghost {rng.choice(['ghost2', n])} 1 1.5")
        elif k == "reject_mismatch" and other:
            emit(f"transfer {n} {rng.choice(other)} 1")
        elif k == "reject_zero":
            half = Decimal(5).scaleb(-m - 1)   # exactly half a minor unit → rounds to 0 (even)
            emit(f"withdraw {n} {P(half)}")
        elif k == "reject_neg":
            emit(f"deposit {n} -{num(rng, 2, 2)}")
        elif k == "reject_funds":
            bal, lim = A[n][1], A[n][2]
            emit(f"withdraw {n} {P(bal + lim + Decimal(1).scaleb(-m))}")
        elif k == "overdraft_edge":
            lims = [x for x in names if A[x][2] > 0]
            n = rng.choice(lims)
            m = acc_cur[n][1]
            bal, lim = A[n][1], A[n][2]
            emit(f"withdraw {n} {P(bal + lim)}")                   # exactly to -limit: allowed
            emit(f"withdraw {n} {P(Decimal(1).scaleb(-m))}")        # one minor unit further: rejected
        elif k == "comment":
            lines.append(rng.choice(["# month end", "", "   # indented comment", "  "]))
    # final balances: one small negative (inside its overdraft limit), one exactly zero
    state = Ledger()
    state.run("\n".join(lines))
    A = state.acct
    lim = rng.choice([x for x in names if A[x][2] > 0 and A[x][1] > 0])
    m = acc_cur[lim][1]
    small = Decimal(rng.randrange(1, 10 ** m if m else 2)).scaleb(-m) if m else Decimal(1)
    small = min(small, A[lim][2])
    emit(f"withdraw {lim} {P(A[lim][1] + small)}")
    zero = rng.choice([x for x in names if x != lim and A[x][1] > 0])
    emit(f"transfer {zero} {rng.choice([x for x in by_cur[acc_cur[zero][0]] if x != zero] or [lim])} "
         f"{P(A[zero][1])}" if len(by_cur[acc_cur[zero][0]]) > 1 else f"withdraw {zero} {P(A[zero][1])}")
    text = "\n".join(lines) + "\n"
    return text, Ledger().run(text)


class Challenge(H.Challenge):
    name = "decimal_ledger"
    title = "Exact decimal ledger: big amounts, HALF_EVEN, overdrafts, largest-remainder splits"
    category = "text"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        text, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        (d / "ledger.txt").write_text(text)
        return H.Case(args=[str(d)], expected=exp)
