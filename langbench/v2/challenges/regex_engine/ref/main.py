import os
import sys

# ---------- parsing ----------
DIG = set("0123456789")
WRD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
SPC = set(" \t\n\r\f\v")
ESC = {"d": (DIG, False), "D": (DIG, True), "w": (WRD, False), "W": (WRD, True), "s": (SPC, False),
       "S": (SPC, True)}


class P:
    def __init__(self, src):
        self.s, self.i, self.groups = src, 0, 0

    def more(self):
        return self.i < len(self.s)

    def cur(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def regex(self):
        alts = [self.branch()]
        while self.cur() == "|":
            self.i += 1
            alts.append(self.branch())
        return ("alt", alts)

    def branch(self):
        seq = []
        while self.more() and self.cur() not in "|)":
            seq.append(self.piece())
        return ("seq", seq)

    def piece(self):
        a = self.atom()
        c = self.cur()
        lo = hi = None
        if c == "*":
            lo, hi = 0, -1
        elif c == "+":
            lo, hi = 1, -1
        elif c == "?":
            lo, hi = 0, 1
        elif c == "{":
            j = self.s.index("}", self.i)
            body = self.s[self.i + 1:j]
            if "," in body:
                x, y = body.split(",")
                lo, hi = int(x), (int(y) if y else -1)
            else:
                lo = hi = int(body)
            self.i = j
        else:
            return a
        self.i += 1
        greedy = True
        if self.cur() == "?":
            greedy = False
            self.i += 1
        return ("loop", a, lo, hi, greedy)

    def atom(self):
        c = self.cur()
        self.i += 1
        if c == "(":
            cap = None
            if self.s.startswith("?:", self.i):
                self.i += 2
            else:
                self.groups += 1
                cap = self.groups
            r = self.regex()
            self.i += 1  # ')'
            return ("grp", cap, r)
        if c == ".":
            return ("cls", set(), True)
        if c == "^":
            return ("bol",)
        if c == "$":
            return ("eol",)
        if c == "\\":
            e = self.cur()
            self.i += 1
            if e in ESC:
                st, neg = ESC[e]
                return ("cls", st, neg)
            return ("cls", {e}, False)
        if c == "[":
            neg = False
            if self.cur() == "^":
                neg = True
                self.i += 1
            st = set()
            while self.cur() != "]":
                ch = self.cur()
                self.i += 1
                if ch == "\\":
                    e = self.cur()
                    self.i += 1
                    if e in ESC:
                        es, en = ESC[e]
                        st |= {chr(k) for k in range(32, 127) if (chr(k) in es) != en}
                        continue
                    ch = e
                if self.cur() == "-":
                    self.i += 1
                    hi = self.cur()
                    self.i += 1
                    if hi == "\\":
                        hi = self.cur()
                        self.i += 1
                    st |= {chr(k) for k in range(ord(ch), ord(hi) + 1)}
                else:
                    st.add(ch)
            self.i += 1
            return ("cls", st, neg)
        return ("cls", {c}, False)


# ---------- NFA with loop registers ----------
# node kinds: ("chr", set, neg, nxt) ("spl", [targets]) ("sav", slot, nxt) ("bol", nxt) ("eol", nxt)
#             ("lin", loop, nxt)   enter loop: reset its register
#             ("ltest", loop)      decide: another iteration or leave
#             ("lbeg", loop, nxt)  an iteration begins here: remember position, count it
#             ("lend", loop, nxt)  an iteration ended
#             ("ok",)
class NFA:
    def __init__(self):
        self.nodes = []
        self.loops = []          # (min, max, greedy, body_entry, exit, enclosing loops)

    def add(self, n):
        self.nodes.append(list(n))
        return len(self.nodes) - 1


def build(nfa, ast, nxt, encl):
    """Return entry node of ast, continuing at nxt."""
    k = ast[0]
    if k == "cls":
        return nfa.add(("chr", ast[1], ast[2], nxt))
    if k == "bol" or k == "eol":
        return nfa.add((k, nxt))
    if k == "seq":
        e = nxt
        for x in reversed(ast[1]):
            e = build(nfa, x, e, encl)
        return e
    if k == "alt":
        if len(ast[1]) == 1:
            return build(nfa, ast[1][0], nxt, encl)
        return nfa.add(("spl", [build(nfa, b, nxt, encl) for b in ast[1]]))
    if k == "grp":
        if ast[1] is None:
            return build(nfa, ast[2], nxt, encl)
        close = nfa.add(("sav", 2 * ast[1] + 1, nxt))
        return nfa.add(("sav", 2 * ast[1], build(nfa, ast[2], close, encl)))
    if k == "loop":
        _, body, lo, hi, greedy = ast
        lid = len(nfa.loops)
        nfa.loops.append(None)
        test = nfa.add(("ltest", lid))
        inner = encl + (lid,)
        end = nfa.add(("lend", lid, test))
        b = build(nfa, body, end, inner)
        beg = nfa.add(("lbeg", lid, b))
        nfa.loops[lid] = (lo, hi, greedy, beg, nxt)
        return nfa.add(("lin", lid, test))
    raise ValueError(k)


def run(nfa, start, ngroups, text):
    nodes, loops = nfa.nodes, nfa.loops
    L = len(text)
    nloops = len(loops)

    def key(node, regs, pos):
        # count matters only up to min+1 (or exactly, when bounded); the iteration start only as "== pos"
        out = [node]
        for lid in range(nloops):
            r = regs[lid]
            if r is None:
                out.append(None)
                continue
            cnt, st = r
            lo, hi = loops[lid][0], loops[lid][1]
            if hi == -1:
                cnt = min(cnt, lo + 1)
            out.append((cnt, st == pos))
        return tuple(out)

    def closure(node, caps, regs, pos, seen, out):
        k = key(node, regs, pos)
        if k in seen:
            return
        seen.add(k)
        n = nodes[node]
        t = n[0]
        if t == "chr" or t == "ok":
            out.append((node, caps, regs))
        elif t == "spl":
            for x in n[1]:
                closure(x, caps, regs, pos, seen, out)
        elif t == "sav":
            c = list(caps)
            c[n[1]] = pos
            closure(n[2], tuple(c), regs, pos, seen, out)
        elif t == "bol":
            if pos == 0:
                closure(n[1], caps, regs, pos, seen, out)
        elif t == "eol":
            if pos == L:
                closure(n[1], caps, regs, pos, seen, out)
        elif t == "lin":
            r = list(regs)
            r[n[1]] = (0, -1)
            closure(n[2], caps, tuple(r), pos, seen, out)
        elif t == "lbeg":
            r = list(regs)
            r[n[1]] = (regs[n[1]][0], pos)
            closure(n[2], caps, tuple(r), pos, seen, out)
        elif t == "lend":
            r = list(regs)
            cnt, st = regs[n[1]]
            r[n[1]] = (cnt + 1, st)
            closure(n[2], caps, tuple(r), pos, seen, out)
        elif t == "ltest":
            lid = n[1]
            lo, hi, greedy, beg, ext = loops[lid]
            cnt, st = regs[lid]
            if cnt < lo:
                closure(beg, caps, regs, pos, seen, out)
                return
            allowed = (hi == -1 or cnt < hi) and not (cnt > lo and st == pos)
            done = list(regs)
            done[lid] = None
            done = tuple(done)
            if not allowed:
                closure(ext, caps, done, pos, seen, out)
            elif greedy:
                closure(beg, caps, regs, pos, seen, out)
                closure(ext, caps, done, pos, seen, out)
            else:
                closure(ext, caps, done, pos, seen, out)
                closure(beg, caps, regs, pos, seen, out)

    threads = []
    best = None
    init_regs = (None,) * nloops
    for pos in range(L + 1):
        seen = set()
        cur = []
        for node, caps, regs in threads:
            closure(node, caps, regs, pos, seen, cur)
        if best is None:
            c = [None] * (2 * ngroups + 2)
            c[0] = pos
            closure(start, tuple(c), init_regs, pos, seen, cur)
        threads = []
        for node, caps, regs in cur:
            n = nodes[node]
            if n[0] == "ok":
                best = list(caps)
                best[1] = pos
                break
            if pos < L and ((text[pos] in n[1]) != n[2]):
                threads.append((n[3], caps, regs))
        if not threads and best is not None:
            break
    return best


def solve(pat, text):
    ps = P(pat)
    ast = ps.regex()
    nfa = NFA()
    ok = nfa.add(("ok",))
    start = build(nfa, ast, ok, ())
    m = run(nfa, start, ps.groups, text)
    if m is None:
        return "no match"
    parts = []
    for g in range(ps.groups + 1):
        a, b = m[2 * g], m[2 * g + 1]
        parts.append("-" if a is None or b is None else f"{a}-{b}")
    return " ".join(parts)


def main():
    sys.setrecursionlimit(100000)
    lines = open(os.path.join(sys.argv[1], "cases.txt"), encoding="ascii").read().split("\n")
    out = []
    num = 0
    for line in lines:
        if line == "":
            continue
        num += 1
        pat, text = line.split("\t", 1)
        out.append(f"{num}: {solve(pat, text)}")
    sys.stdout.write("\n".join(out) + "\n")


main()
