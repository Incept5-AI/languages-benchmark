"""regex_engine — a regex search engine with backtracking (Python `re.search`) semantics that must run in
polynomial time.

Hard parts: leftmost-first (not leftmost-longest) results, greedy/lazy priority, alternation order, Python's
rule for empty iterations of a quantifier (an optional iteration that matches empty ends the loop), capture
groups inside repetition (last iteration wins, never reset), unset groups, classes with escapes/negation, and
catastrophic patterns ((a*)*b, (a|aa)*c, (a?){n}a{n}, …) on which any plain backtracker — including the
built-in regex engines of Java, JavaScript, Swift/ICU and Python — runs for hours.

Oracle: a Pike VM with thread priorities (below). It was cross-checked against Python's `re` on ~500k random
small pattern/subject pairs (0 differences), and `python3 challenge.py --check-re` re-checks every
non-catastrophic case of seeds 0..2 against `re` and confirms `re` times out on the catastrophic ones.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = r"""
Write a {LANG} program that implements a regular-expression search engine.

The program is run with one argument: a directory containing `cases.txt`. Each line of that file is one
test: a pattern, one TAB character, and a subject string. Lines end with `\n` (the file ends with one too).
Patterns and subjects consist only of printable ASCII characters (0x20–0x7E, so never TAB or newline);
patterns are non-empty, subjects may be empty, and either may contain spaces. Number the lines 1, 2, 3, ….

For every line, search the subject for the first match of the pattern, exactly as Perl or Python's
`re.search` would, and print it.

## Pattern syntax

```
regex      := branch ( '|' branch )*            a branch may be empty
branch     := piece*
piece      := atom quantifier?                  anchors are never quantified
quantifier := ( '*' | '+' | '?' | '{' m '}' | '{' m ',}' | '{' m ',' n '}' ) '?'?
atom       := '(' regex ')'                     capturing group
            | '(?:' regex ')'                   non-capturing group
            | '.' | '^' | '$' | class | escape | literal
```
- `m` and `n` are decimal integers with 0 ≤ m ≤ n ≤ 60 and n ≥ 1. A trailing `?` makes the quantifier lazy.
  A quantifier never follows another quantifier (no `a**`, `a{2}{3}`).
- literal: any character other than `\ . [ ] ( ) { } * + ? | ^ $` matches itself (case-sensitive).
- `.` matches any character.
- `^` matches only at position 0; `$` matches only at the end of the subject (position = its length).
- escape: `\d` (digits `0-9`), `\w` (`A-Z a-z 0-9 _`), `\s` (space, TAB, LF, CR, FF, VT) and their complements
  `\D`, `\W`, `\S` (every character not in the set). A backslash followed by any other character, which is
  always a non-alphanumeric printable character, matches that character literally (`\.`, `\*`, `\\`, `\-`, `\ `, …).
- class: `[` items `]`, or `[^` items `]` which matches any character NOT matched by the items. There is at
  least one item. An item is a single character, a range `x-y` (code points x ≤ y, matching every character in
  between inclusive; each end is a single character, possibly escaped), or one of the six class escapes above.
  Inside a class the characters `\`, `]`, `[` and `-` (except as the range dash) always appear escaped with
  `\`; `^` is literal when it is not the first character; every other character (including `.`, `*`, `$`, `(`)
  stands for itself.
- Capturing groups are numbered 1, 2, … in the order of their opening parenthesis; `(?:` groups are not numbered.

## Matching semantics

The answer is the match that a backtracking matcher returns. It tries start positions 0, 1, …, len(subject)
in order; at each start position it explores the possibilities in the priority order below, and the first
complete match it finds is the answer (the match need not reach the end of the subject; the empty string can
be a match). If no start position yields a match, there is no match.

- A branch matches its pieces left to right. `A|B|C` tries `A` first, then `B`, then `C`.
- `X?` is `X{0,1}`, `X*` is `X{0,}` and `X+` is `X{1,}`. For `X{m}`, `X{m,}` and `X{m,n}` (min = m,
  max = m, unbounded or n): iterations 1 … min are mandatory. After k iterations, with min ≤ k < max, one more
  iteration is *allowed* unless iteration k was an optional one (k > min) that matched the empty string. If
  another iteration is allowed, a greedy quantifier first tries one more iteration and then (on backtracking)
  stops; a lazy quantifier first stops and then tries one more iteration. When it stops, matching continues
  with whatever follows the quantified atom. (So an optional iteration that matches the empty string ends the
  loop, while mandatory empty iterations do not; e.g. `(|a)+` against `aa` matches the empty string at 0.)
- Backtracking undoes everything done since the choice point, including captures.

Captures: a group's span is the start and end position of the **last** match of that group's contents on the
path of the final match. Inside a quantifier this is the last iteration that matched the group; if a later
iteration did not pass through the group (another alternative was taken), the group keeps its earlier span —
spans are never reset. A group that the final path never matched is unset. Positions are 0-based offsets
between characters: a match of `bc` in `abcd` is `1-3`.

## Output

One line per test: `<n>: <start>-<end>` for the whole match followed, for each capturing group in order, by
a space and either `<start>-<end>` or `-` (unset). If there is no match: `<n>: no match`.

## Performance

Patterns are at most 60 characters long and subjects at most 80. Some tests are built so that a backtracking
matcher needs exponential time (for example `(a*)*b` against 40 `a`s): your program must finish the whole
file within the time limit (10 seconds), so it needs a polynomial-time method, e.g. simulating the pattern as
an NFA over all positions with prioritised threads (Pike VM), or backtracking with memoisation of failed states.
Rewriting every counted quantifier `X{m,n}` as copies of `X` never makes a pattern longer than 3000 atoms.
Built-in regex libraries are allowed, but the backtracking ones (Java, JavaScript, Python, ICU /
NSRegularExpression) take far longer than the time limit on those tests.

"""

EXAMPLE = [("(a|ab)(c|bcd)(d*)", "abcd"), ("x*", "abc"), ("(a|b)*?c", "zabac"), ("(?:(a)|b)+", "ab"),
           ("(a*)*b", "aab"), ("(|a)+", "aa"), ("[^a\\d]+(\\.)?", "1a2bc.d"), ("(a)|(b)", "xb"),
           ("a{2,3}?", "aaaa"), ("(a*)*b", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), ("^b", "ab")]


def prompt_text():
    lines = "".join(f"{p}\t{s}\n" for p, s in EXAMPLE)
    shown = lines.replace("\t", "<TAB>")
    return (PROMPT + "Example: if `cases.txt` is (`<TAB>` stands for the TAB character)\n```\n" + shown + "```\n"
            "the output is\n```\n" + expected_output(lines) + "\n```\n")


# ── oracle: parser + Pike VM ─────────────────────────────────────────────────
META = set("\\.[](){}*+?|^$")
DIGIT = frozenset("0123456789")
WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
SPACE = frozenset(" \t\n\r\f\v")
ALL = frozenset(chr(c) for c in range(32, 127))
CLASS_ESC = {"d": (DIGIT, False), "D": (DIGIT, True), "w": (WORD, False), "W": (WORD, True),
             "s": (SPACE, False), "S": (SPACE, True)}


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, p):
        self.p, self.i, self.ngroups = p, 0, 0

    def peek(self):
        return self.p[self.i] if self.i < len(self.p) else None

    def take(self):
        c = self.peek()
        if c is None:
            raise ParseError("eof")
        self.i += 1
        return c

    def parse(self):
        n = self.alt()
        if self.i != len(self.p):
            raise ParseError("trailing " + self.p[self.i:])
        return n

    def alt(self):
        branches = [self.concat()]
        while self.peek() == "|":
            self.i += 1
            branches.append(self.concat())
        return ("alt", branches) if len(branches) > 1 else branches[0]

    def concat(self):
        items = []
        while self.peek() is not None and self.peek() not in "|)":
            items.append(self.repeat())
        return ("cat", items)

    def repeat(self):
        a = self.atom()
        c = self.peek()
        if c not in ("*", "+", "?", "{"):
            return a
        if a[0] in ("bol", "eol"):
            raise ParseError("quantified anchor")
        self.i += 1
        if c == "*":
            lo, hi = 0, None
        elif c == "+":
            lo, hi = 1, None
        elif c == "?":
            lo, hi = 0, 1
        else:
            lo = self.num()
            if self.peek() == "}":
                hi = lo
            else:
                if self.take() != ",":
                    raise ParseError("bad {")
                hi = None if self.peek() == "}" else self.num()
            if self.take() != "}":
                raise ParseError("bad }")
            if (hi is not None and (hi < lo or hi < 1)) or lo > 60 or (hi or 0) > 60:
                raise ParseError("bad bounds")
        greedy = True
        if self.peek() == "?":
            self.i += 1
            greedy = False
        if self.peek() in ("*", "+", "?", "{"):
            raise ParseError("multiple repeat")
        return ("rep", a, lo, hi, greedy)

    def num(self):
        s = ""
        while self.peek() is not None and self.peek().isdigit():
            s += self.take()
        if not s:
            raise ParseError("num")
        return int(s)

    def atom(self):
        c = self.take()
        if c == "(":
            if self.p.startswith("?:", self.i):
                self.i += 2
                n = self.alt()
                if self.take() != ")":
                    raise ParseError(")")
                return ("ncg", n)
            self.ngroups += 1
            g = self.ngroups
            n = self.alt()
            if self.take() != ")":
                raise ParseError(")")
            return ("group", g, n)
        if c == ".":
            return ("set", ALL, False)
        if c == "^":
            return ("bol",)
        if c == "$":
            return ("eol",)
        if c == "[":
            return self.cls()
        if c == "\\":
            e = self.take()
            if e in CLASS_ESC:
                return ("set",) + CLASS_ESC[e]
            if e.isalnum():
                raise ParseError("bad escape")
            return ("set", frozenset(e), False)
        if c in META:
            raise ParseError("unexpected " + c)
        return ("set", frozenset(c), False)

    def cls(self):
        neg = False
        if self.peek() == "^":
            self.i += 1
            neg = True
        items = set()
        first = True
        while True:
            c = self.take()
            if c == "]" and not first:
                break
            if c in "][-":
                raise ParseError("unescaped class char")
            first = False
            if c == "\\":
                e = self.take()
                if e in CLASS_ESC:
                    s, n2 = CLASS_ESC[e]
                    items |= (ALL - s) if n2 else s
                    continue
                if e.isalnum():
                    raise ParseError("bad escape")
                c = e
            if self.peek() == "-":
                self.i += 1
                d = self.take()
                if d == "\\":
                    d = self.take()
                    if d.isalnum():
                        raise ParseError("bad range end")
                elif d in "][-":
                    raise ParseError("bad range end")
                if ord(d) < ord(c):
                    raise ParseError("bad range")
                items |= set(chr(x) for x in range(ord(c), ord(d) + 1))
            else:
                items.add(c)
        return ("set", frozenset(items), neg)


def compile_regex(pattern):
    ps = Parser(pattern)
    ast = ps.parse()
    prog, encl, open_loops = [], [], []
    nloops = [0]

    def emit(ins):
        prog.append(ins)
        encl.append(tuple(open_loops))
        return len(prog) - 1

    def gen(n):
        k = n[0]
        if k == "set":
            emit(("char", n[1], n[2]))
        elif k in ("bol", "eol"):
            emit((k,))
        elif k == "cat":
            for x in n[1]:
                gen(x)
        elif k == "ncg":
            gen(n[1])
        elif k == "group":
            emit(("save", 2 * n[1]))
            gen(n[2])
            emit(("save", 2 * n[1] + 1))
        elif k == "alt":
            jmps, br = [], n[1]
            for idx, b in enumerate(br):
                if idx < len(br) - 1:
                    sp = emit(None)
                    gen(b)
                    jmps.append(emit(None))
                    prog[sp] = ("split", sp + 1, len(prog))
                else:
                    gen(b)
            for j in jmps:
                prog[j] = ("jmp", len(prog))
        elif k == "rep":
            _, body, lo, hi, greedy = n
            for _ in range(lo):                       # mandatory iterations
                gen(body)
            fix = []
            for _ in range(1 if hi is None else hi - lo):   # optional iterations
                lid = nloops[0]
                nloops[0] += 1
                sp = emit(None)
                open_loops.append(lid)
                emit(("mark", lid))                   # remember where this optional iteration began
                gen(body)
                chk = emit(None)                      # empty optional iteration -> leave the loop
                open_loops.pop()
                if hi is None:
                    emit(("jmp", sp))
                fix.append((sp, chk, lid))
            X = len(prog)
            for sp, chk, lid in fix:
                prog[sp] = ("split", sp + 1, X) if greedy else ("split", X, sp + 1)
                prog[chk] = ("ifempty", lid, X)
        else:
            raise ValueError(k)

    gen(ast)
    emit(("match",))
    return prog, encl, ps.ngroups, nloops[0]


def search(compiled, s, budget=None):
    """Leftmost-first match. Returns (spans, steps); spans is None or a list of (start, end)|None, group 0 first."""
    prog, encl, ngroups, nloops = compiled
    n = len(s)
    steps = 0

    def add(lst, seen, pc, caps, starts, pos):
        nonlocal steps
        stack = [(pc, caps, starts)]
        while stack:
            pc, caps, starts = stack.pop()
            steps += 1
            key = (pc, tuple(starts[i] == pos for i in encl[pc]))
            if key in seen:
                continue
            seen.add(key)
            ins = prog[pc]
            op = ins[0]
            if op == "jmp":
                stack.append((ins[1], caps, starts))
            elif op == "split":
                stack.append((ins[2], caps, starts))
                stack.append((ins[1], caps, starts))
            elif op == "save":
                c = list(caps)
                c[ins[1]] = pos
                stack.append((pc + 1, tuple(c), starts))
            elif op == "mark":
                st = list(starts)
                st[ins[1]] = pos
                stack.append((pc + 1, caps, tuple(st)))
            elif op == "ifempty":
                stack.append((ins[2] if starts[ins[1]] == pos else pc + 1, caps, starts))
            elif op == "bol":
                if pos == 0:
                    stack.append((pc + 1, caps, starts))
            elif op == "eol":
                if pos == n:
                    stack.append((pc + 1, caps, starts))
            else:
                lst.append((pc, caps, starts))

    clist, matched = [], None
    for pos in range(n + 1):
        seen, nl = set(), []
        for pc, caps, starts in clist:
            add(nl, seen, pc, caps, starts, pos)
        if matched is None:
            c0 = [None] * (2 * ngroups + 2)
            c0[0] = pos
            add(nl, seen, 0, tuple(c0), (-1,) * nloops, pos)
        if budget is not None and steps > budget:
            raise OverflowError("step budget")
        nxt = []
        for pc, caps, starts in nl:
            ins = prog[pc]
            if ins[0] == "match":
                c = list(caps)
                c[1] = pos
                matched = c
                break                                  # lower-priority threads are cut off
            if pos < n and (s[pos] in ins[1]) != ins[2]:
                nxt.append((pc + 1, caps, starts))
        clist = nxt
        if not clist and matched is not None:
            break
    if matched is None:
        return None, steps
    return [None if matched[2 * g] is None else (matched[2 * g], matched[2 * g + 1])
            for g in range(ngroups + 1)], steps


def expanded_atoms(pattern):
    """Number of instructions after expanding counted quantifiers (the prompt's '3000 atoms' bound)."""
    return len(compile_regex(pattern)[0])


def answer(pattern, subject):
    spans, _ = search(compile_regex(pattern), subject)
    return format_spans(spans)


def format_spans(spans):
    if spans is None:
        return "no match"
    return " ".join("-" if sp is None else f"{sp[0]}-{sp[1]}" for sp in spans)


def expected_output(text):
    out = []
    for i, line in enumerate(text.split("\n")[:-1], 1):
        p, s = line.split("\t")
        out.append(f"{i}: {answer(p, s)}")
    return "\n".join(out)


# ── generation ───────────────────────────────────────────────────────────────
# Templates use the placeholders α β γ δ ε for five distinct seed-chosen letters (ε is a filler letter that
# never occurs in the pattern).
TRAPS = [
    ("(α|αβ)(γ|βγδ)(δ*)", "εαβγδ"),          # leftmost-first, not longest
    ("(α*)*β", "γγβ"),                        # empty optional iteration ends the loop; group 1 = empty
    ("(α|)+", "αα"),                          # last (empty) iteration sets the group
    ("(|α)+", "αα"),                          # mandatory empty, then optional empty -> matches empty
    ("(|α)*", "αα"),
    ("(α?){3}", "α"),                         # mandatory empty iterations
    ("(α?){2,}?β", "ααβ"),
    ("(?:(α)|β)+", "αββ"),                    # group keeps value from an earlier iteration
    ("((α)|β)+", "αβ"),
    ("(?:(α)|(β)|(γ))+", "γβαγ"),
    ("(α+?)(α*)", "ααα"),                     # lazy then greedy
    ("(α|β)*?γ", "εαβγ"),
    ("(?:α??)+?β", "ααβ"),
    ("((α*)β)*?γ", "αβγ"),
    ("(α)|(β)", "εβ"),                        # unset group
    ("(^α|β$)+", "αβ"),
    ("(α*)+$", "βαα"),
    ("(?:α|αβ)*γ", "αβαγ"),
    ("(αβ){2}|(α|β){2,}γ", "αβαββγ"),
    ("α{2,3}?β|α{2,}", "αααα"),
    ("(α{0,2}){2}", "ααα"),
    ("(α|αα)+?$", "ααα"),
    ("(?:β|(α))*?(α)", "αα"),
    ("(α|β)*δ", "αβαβ"),                      # no match
    (".{3}(.*?)β", "εαββαβ"),
    ("$", "αβ"),
    ("^β", "αβ"),
    ("(α*)(β?)(α*)", "εεε"),                  # empty match at 0 with empty groups
    ("([^α\\d ]+)(\\.)?", "12 βγ3.α"),
    ("[\\w\\-]+@[a-zA-Z]{2,3}?\\.", "!! x-y_z@βγδ.e"),
    ("\\s(\\S+)\\W", "αβ γδ!εα"),
    ("\\(\\$\\)\\*\\.\\[\\\\\\]", "x($)*.[\\]"),
    ("[.*$(]+[^\\]\\-]", "a.*$(-]x"),
    ("\\d{2,}\\D|\\d", "a1b22c333d"),
    ("(α|β|)*?γ{1,2}?", "βαγ"),
    ("((α)|(β))*", "αβα"),
    ("(|α){1,2}β", "αβ"),                     # empty MANDATORY iteration does not block the next one
    ("(α*?){1,2}$", "αα"),
]


def catastrophic(rng, A, B, C, D):
    n = lambda lo, hi: rng.randint(lo, hi)
    k1, k5 = n(40, 60), n(40, 50)
    word = "".join(rng.choice([A, B, C, D]) for _ in range(n(40, 55)))
    return [
        (f"({A}*)*{B}", A * n(40, 60)),
        (f"({A}|{A}{A})*{C}", A * n(56, 64)),
        (f"^({A}+)+$", A * n(40, 60) + B),
        (f"({A}|{A})*{B}", A * n(40, 60)),
        (f"({A}?){{{k5}}}{A}{{{k5}}}", A * k5),                  # matches: all (a?) empty
        (f"(?:{A}+?)+?{B}", A * n(40, 60)),
        (f"(({A}|{B})+)+{C}", "".join(rng.choice([A, B]) for _ in range(n(40, 60)))),
        ("(\\w+\\s?)+$", f"{A}{B} {C}{D} " + word + "!"),
        (f"({A}*)*$", A * k1 + B),                                # matches the empty string at the end
        (f"(?:{A}|{A}?)+?{B}", A * n(40, 60)),
        (f"({A}+{A}+)+{B}", A * n(40, 60)),
        (f"(?:{A}|{B}|{A}{B})*{C}", (A + B) * n(32, 40)),            # 2^n ways to split (ab)^n
    ]


def rand_pattern(rng, letters, depth=0):
    A, B, C = letters[:3]

    def atom():
        r = rng.random()
        if depth < 2 and r < 0.3:
            return rng.choice(["(", "(", "(?:"]) + rand_pattern(rng, letters, depth + 1) + ")"
        if r < 0.36:
            return "."
        if r < 0.46:
            lo, hi = sorted([A, B])
            return rng.choice([f"[{A}{B}]", f"[^{A}]", f"[{lo}-{hi}]", "\\w", "\\d", "\\s", "\\W",
                               f"[^{B}\\d]", f"[\\-{A}]", "\\.", "[ .]"])
        if r < 0.5:
            return rng.choice(["^", "$"])
        return rng.choice([A, A, B, B, C, "1", " "])

    def quant():
        if rng.random() < 0.5:
            return ""
        q = rng.choice(["*", "+", "?", "{2}", "{0,2}", "{1,3}", "{2,}", "{0,1}", "{3}", "{1,}"])
        return q + ("?" if rng.random() < 0.35 else "")

    branches = []
    for _ in range(1 if rng.random() < 0.6 else rng.randint(2, 3)):
        items = []
        for _ in range(rng.randint(0 if rng.random() < .15 else 1, 4)):
            a = atom()
            items.append(a + (quant() if a not in ("^", "$") else ""))
        branches.append("".join(items))
    return "|".join(branches)


def make_lines(seed):
    rng = random.Random(18018 + seed)
    pool = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    A, B, C, D, E = rng.sample(pool, 5)
    sub = lambda t: t.translate(str.maketrans("αβγδε", A + B + C + D + E))
    cases = []
    for p, s in TRAPS:
        pre = rng.choice(["", "", E, E + E, "1 "])
        cases.append((sub(p), pre + sub(s)))
    cases += catastrophic(rng, A, B, C, D)
    letters = [A, B, C]
    tries = 0
    while len(cases) < len(TRAPS) + 12 + 32:
        tries += 1
        p = rand_pattern(rng, letters)
        if not p or len(p) > 60:
            continue
        s = "".join(rng.choice([A, A, B, B, C, "1", " ", "."]) for _ in range(rng.randint(0, 16)))
        try:
            comp = compile_regex(p)
            search(comp, s, budget=200000)
        except (ParseError, OverflowError):
            continue
        cases.append((p, s))
    rng.shuffle(cases)
    for p, s in cases:
        assert 0 < len(p) <= 60 and len(s) <= 80 and "\t" not in p + s, (p, s)
        assert all(" " <= ch <= "~" for ch in p + s)
        assert expanded_atoms(p) <= 3000
    return cases


def case_text(seed):
    return "".join(f"{p}\t{s}\n" for p, s in make_lines(seed))


class Challenge(H.Challenge):
    name = "regex_engine"
    title = "Regex search with backtracking semantics in polynomial time (captures, lazy, empty loops)"
    category = "lang"
    timeout = 10

    def prompt(self, ctx):
        return prompt_text()

    def case(self, seed, ctx):
        d = ctx.data_dir(self, seed)
        text = case_text(seed)
        (d / "cases.txt").write_text(text)
        return H.Case(args=[str(d)], expected=expected_output(text))


# ── author tool: cross-check against Python's re ─────────────────────────────
def _py_re(p, s):
    import re
    m = re.search(p, s)
    if not m:
        return "no match"
    return " ".join("-" if m.span(g)[0] == -1 else "%d-%d" % m.span(g) for g in range(m.re.groups + 1))


def check_re(seeds=(0, 1, 2), slow_secs=15):
    """Non-catastrophic cases must equal re.search; catastrophic ones must make re time out."""
    import subprocess
    import warnings
    warnings.simplefilter("error")
    texts = [("example", "".join(f"{p}\t{s}\n" for p, s in EXAMPLE))]
    texts += [(f"seed {sd}", case_text(sd)) for sd in seeds]
    for label, text in texts:
        bad = slow = 0
        rows = [ln.split("\t") for ln in text.split("\n")[:-1]]
        for p, s in rows:
            ours = answer(p, s)
            code = f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); import challenge as c; " \
                   f"print(c._py_re({p!r}, {s!r}))"
            try:
                r = subprocess.run([sys.executable, "-W", "error", "-c", code], capture_output=True, text=True,
                                   timeout=slow_secs)
                theirs = r.stdout.strip() or r.stderr.strip().splitlines()[-1]
                if theirs != ours:
                    bad += 1
                    print(f"  DIFF {p!r} {s!r}: re={theirs} ours={ours}")
            except subprocess.TimeoutExpired:
                slow += 1
                print(f"  re timed out (> {slow_secs}s): {p!r} on {len(s)} chars -> ours: {ours}")
        print(f"{label}: {len(rows)} cases, {bad} differences from re, {slow} where re timed out")


if __name__ == "__main__" and "--check-re" in sys.argv:
    check_re()
