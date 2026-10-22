import os
import sys

sys.setrecursionlimit(100000)

LO, HI = -(2 ** 63), 2 ** 63 - 1
KEYWORDS = set("let fn if else while for in return break continue and or not true false nil".split())


class RtErr(Exception):
    pass


class BreakSig(Exception):
    pass


class ContinueSig(Exception):
    pass


class ReturnSig(Exception):
    def __init__(self, value):
        self.value = value


def fail(line, msg):
    e = RtErr(msg)
    e.line = line
    raise e


class Env:
    __slots__ = ("vars", "parent")

    def __init__(self, parent):
        self.vars = {}
        self.parent = parent

    def find(self, name):
        e = self
        while e is not None:
            if name in e.vars:
                return e
            e = e.parent
        return None


class Closure:
    def __init__(self, params, body, env):
        self.params, self.body, self.env = params, body, env


class Native:
    def __init__(self, name, nargs, fn):
        self.name, self.nargs, self.fn = name, nargs, fn


def typename(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    return "function"


def is_true(v):
    if v is None or v is False:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, int):
        return v != 0
    if isinstance(v, (str, list)):
        return len(v) > 0
    return True


def equal(a, b):
    ta, tb = typename(a), typename(b)
    if ta != tb:
        return False
    if ta == "list":
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if not equal(x, y):
                return False
        return True
    if ta == "function":
        return a is b
    return a == b


def display(v, inside=False, path=()):
    t = typename(v)
    if t == "string":
        if not inside:
            return v
        out = ['"']
        for ch in v:
            out.append({"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t"}.get(ch, ch))
        out.append('"')
        return "".join(out)
    if t == "int":
        return str(v)
    if t == "bool":
        return "true" if v else "false"
    if t == "nil":
        return "nil"
    if t == "function":
        return "<fn>"
    for p in path:
        if p is v:
            return "[...]"
    path = path + (v,)
    return "[" + ", ".join(display(x, True, path) for x in v) + "]"


# ── lexer ──
def tokenize(src):
    toks = []
    i, line = 0, 1
    while i < len(src):
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == "#":
            while i < len(src) and src[i] != "\n":
                i += 1
            continue
        if "0" <= c <= "9":
            j = i
            while j < len(src) and "0" <= src[j] <= "9":
                j += 1
            toks.append(("num", int(src[i:j]), line))
            i = j
            continue
        if c == "_" or c.isalpha():
            j = i
            while j < len(src) and (src[j] == "_" or src[j].isalnum()):
                j += 1
            word = src[i:j]
            toks.append(("kw" if word in KEYWORDS else "name", word, line))
            i = j
            continue
        if c == '"':
            i += 1
            s = []
            while src[i] != '"':
                if src[i] == "\\":
                    s.append({"n": "\n", "t": "\t"}.get(src[i + 1], src[i + 1]))
                    i += 2
                else:
                    s.append(src[i])
                    i += 1
            i += 1
            toks.append(("string", "".join(s), line))
            continue
        two = src[i:i + 2]
        if two in ("==", "!=", "<=", ">="):
            toks.append(("sym", two, line))
            i += 2
            continue
        toks.append(("sym", c, line))
        i += 1
    toks.append(("end", None, line))
    return toks


# ── parser → tuple AST ──
class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def cur(self):
        return self.toks[self.pos]

    def is_(self, val, k=0):
        t = self.toks[self.pos + k]
        return t[0] in ("sym", "kw") and t[1] == val

    def expect(self, val):
        t = self.toks[self.pos]
        assert t[1] == val, (t, val)
        self.pos += 1
        return t

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def parse_program(self):
        stmts = []
        while self.cur()[0] != "end":
            stmts.append(self.statement())
        return stmts

    def block(self):
        self.expect("{")
        stmts = []
        while not self.is_("}"):
            stmts.append(self.statement())
        self.expect("}")
        return stmts

    def param_list(self):
        self.expect("(")
        names = []
        while not self.is_(")"):
            names.append(self.next()[1])
            if self.is_(","):
                self.next()
        self.expect(")")
        return names

    def statement(self):
        t = self.cur()
        if t[0] == "kw":
            if t[1] == "let":
                self.next()
                name = self.next()[1]
                self.expect("=")
                e = self.expression()
                self.expect(";")
                return ("let", name, e)
            if t[1] == "fn" and self.toks[self.pos + 1][0] == "name":
                self.next()
                name = self.next()[1]
                params = self.param_list()
                return ("fndecl", name, params, self.block())
            if t[1] == "if":
                return self.if_statement()
            if t[1] == "while":
                self.next()
                c = self.expression()
                return ("while", c, self.block())
            if t[1] == "for":
                line = self.next()[2]
                name = self.next()[1]
                self.expect("in")
                e = self.expression()
                return ("for", name, e, self.block(), line)
            if t[1] == "return":
                self.next()
                e = None
                if not self.is_(";"):
                    e = self.expression()
                self.expect(";")
                return ("return", e)
            if t[1] == "break":
                self.next()
                self.expect(";")
                return ("break",)
            if t[1] == "continue":
                self.next()
                self.expect(";")
                return ("continue",)
        if t[0] == "name" and self.is_("=", 1):
            self.next()
            self.next()
            e = self.expression()
            self.expect(";")
            return ("assign", t[1], e, t[2])
        e = self.expression()
        if self.is_("="):
            assert e[0] == "index"
            self.next()
            v = self.expression()
            self.expect(";")
            return ("setindex", e[1], e[2], v, e[3])
        self.expect(";")
        return ("expr", e)

    def if_statement(self):
        self.expect("if")
        c = self.expression()
        then = self.block()
        els = None
        if self.is_("else"):
            self.next()
            if self.is_("if"):
                els = [self.if_statement()]
            else:
                els = self.block()
        return ("if", c, then, els)

    def expression(self):
        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.is_("or"):
            self.next()
            left = ("or", left, self.and_expr())
        return left

    def and_expr(self):
        left = self.not_expr()
        while self.is_("and"):
            self.next()
            left = ("and", left, self.not_expr())
        return left

    def not_expr(self):
        if self.is_("not"):
            self.next()
            return ("not", self.not_expr())
        return self.cmp_expr()

    def binary_level(self, ops, sub):
        left = sub()
        while self.cur()[0] == "sym" and self.cur()[1] in ops:
            t = self.next()
            left = ("bin", t[1], left, sub(), t[2])
        return left

    def cmp_expr(self):
        return self.binary_level(("==", "!=", "<", "<=", ">", ">="), self.add_expr)

    def add_expr(self):
        return self.binary_level(("+", "-"), self.mul_expr)

    def mul_expr(self):
        return self.binary_level(("*", "/", "%"), self.unary)

    def unary(self):
        if self.is_("-"):
            line = self.next()[2]
            return ("neg", self.unary(), line)
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.is_("("):
                line = self.next()[2]
                args = []
                while not self.is_(")"):
                    args.append(self.expression())
                    if self.is_(","):
                        self.next()
                self.expect(")")
                e = ("call", e, args, line)
            elif self.is_("["):
                line = self.next()[2]
                idx = self.expression()
                self.expect("]")
                e = ("index", e, idx, line)
            else:
                return e

    def primary(self):
        t = self.next()
        kind, val, line = t
        if kind in ("num", "string"):
            return ("lit", val)
        if kind == "name":
            return ("var", val, line)
        if val == "true":
            return ("lit", True)
        if val == "false":
            return ("lit", False)
        if val == "nil":
            return ("lit", None)
        if val == "fn":
            params = self.param_list()
            return ("lambda", params, self.block())
        if val == "(":
            e = self.expression()
            self.expect(")")
            return e
        if val == "[":
            items = []
            while not self.is_("]"):
                items.append(self.expression())
                if self.is_(","):
                    self.next()
            self.expect("]")
            return ("list", items)
        raise SyntaxError(str(t))


# ── evaluator ──
def check_int(v, line):
    if v < LO or v > HI:
        fail(line, "integer overflow")
    return v


def tdiv(a, b):
    q = abs(a) // abs(b)
    return -q if (a < 0) ^ (b < 0) else q


def binary(op, a, b, line):
    if op == "==":
        return equal(a, b)
    if op == "!=":
        return not equal(a, b)
    ta, tb = typename(a), typename(b)
    if ta == "int" and tb == "int":
        if op == "+":
            return check_int(a + b, line)
        if op == "-":
            return check_int(a - b, line)
        if op == "*":
            return check_int(a * b, line)
        if op == "/":
            if b == 0:
                fail(line, "division by zero")
            return check_int(tdiv(a, b), line)
        if op == "%":
            if b == 0:
                fail(line, "division by zero")
            return a - tdiv(a, b) * b
    if ta == tb and ta in ("int", "string") and op in ("<", "<=", ">", ">="):
        if ta == "string":
            a, b = a.encode(), b.encode()
        return a < b if op == "<" else a <= b if op == "<=" else a > b if op == ">" else a >= b
    if op == "+" and ta == tb and ta in ("string", "list"):
        return a + b
    fail(line, f"cannot apply {op} to {ta} and {tb}")


def call_value(f, args, line):
    if isinstance(f, Closure):
        if len(args) != len(f.params):
            fail(line, f"expected {len(f.params)} arguments, got {len(args)}")
        env = Env(f.env)
        for p, a in zip(f.params, args):
            env.vars[p] = a
        try:
            exec_list(f.body, env)
        except ReturnSig as r:
            return r.value
        return None
    if isinstance(f, Native):
        if f.nargs >= 0 and len(args) != f.nargs:
            fail(line, f"expected {f.nargs} arguments, got {len(args)}")
        return f.fn(args, line)
    fail(line, f"cannot call {typename(f)}")


def evaluate(n, env):
    k = n[0]
    if k == "lit":
        return n[1]
    if k == "var":
        e = env.find(n[1])
        if e is None:
            fail(n[2], f"undefined variable {n[1]}")
        return e.vars[n[1]]
    if k == "bin":
        return binary(n[1], evaluate(n[2], env), evaluate(n[3], env), n[4])
    if k == "call":
        f = evaluate(n[1], env)
        args = [evaluate(a, env) for a in n[2]]
        return call_value(f, args, n[3])
    if k == "index":
        obj = evaluate(n[1], env)
        i = evaluate(n[2], env)
        to, ti = typename(obj), typename(i)
        if to not in ("list", "string") or ti != "int":
            fail(n[3], f"cannot index {to} with {ti}")
        if i < 0 or i >= len(obj):
            fail(n[3], f"index {i} out of range for length {len(obj)}")
        return obj[i]
    if k == "and":
        a = evaluate(n[1], env)
        return evaluate(n[2], env) if is_true(a) else a
    if k == "or":
        a = evaluate(n[1], env)
        return a if is_true(a) else evaluate(n[2], env)
    if k == "not":
        return not is_true(evaluate(n[1], env))
    if k == "neg":
        v = evaluate(n[1], env)
        if typename(v) != "int":
            fail(n[2], f"cannot apply - to {typename(v)}")
        return check_int(-v, n[2])
    if k == "list":
        return [evaluate(x, env) for x in n[1]]
    if k == "lambda":
        return Closure(n[1], n[2], env)
    raise AssertionError(k)


def exec_block(stmts, parent):
    exec_list(stmts, Env(parent))


def exec_list(stmts, env):
    for s in stmts:
        execute(s, env)


def execute(s, env):
    k = s[0]
    if k == "expr":
        evaluate(s[1], env)
    elif k == "let":
        env.vars[s[1]] = evaluate(s[2], env)
    elif k == "assign":
        v = evaluate(s[2], env)
        e = env.find(s[1])
        if e is None:
            fail(s[3], f"undefined variable {s[1]}")
        e.vars[s[1]] = v
    elif k == "setindex":
        obj = evaluate(s[1], env)
        i = evaluate(s[2], env)
        v = evaluate(s[3], env)
        if typename(obj) != "list":
            fail(s[4], f"cannot assign into {typename(obj)}")
        if typename(i) != "int":
            fail(s[4], f"cannot index list with {typename(i)}")
        if i < 0 or i >= len(obj):
            fail(s[4], f"index {i} out of range for length {len(obj)}")
        obj[i] = v
    elif k == "if":
        if is_true(evaluate(s[1], env)):
            exec_block(s[2], env)
        elif s[3] is not None:
            exec_block(s[3], env)
    elif k == "while":
        while is_true(evaluate(s[1], env)):
            try:
                exec_block(s[2], env)
            except BreakSig:
                break
            except ContinueSig:
                continue
    elif k == "for":
        seq = evaluate(s[2], env)
        if typename(seq) != "list":
            fail(s[4], f"cannot iterate {typename(seq)}")
        i = 0
        while i < len(seq):
            loop_env = Env(env)
            loop_env.vars[s[1]] = seq[i]
            i += 1
            try:
                exec_block(s[3], loop_env)
            except BreakSig:
                break
            except ContinueSig:
                continue
    elif k == "fndecl":
        env.vars[s[1]] = Closure(s[2], s[3], env)
    elif k == "return":
        raise ReturnSig(None if s[1] is None else evaluate(s[1], env))
    elif k == "break":
        raise BreakSig()
    elif k == "continue":
        raise ContinueSig()


def main():
    src = open(os.path.join(sys.argv[1], "program.tern"), encoding="ascii").read()
    lines = []

    def bad(name, line):
        fail(line, f"bad argument to {name}")

    def n_print(a, line):
        lines.append(" ".join(display(x) for x in a))

    def n_len(a, line):
        if typename(a[0]) not in ("string", "list"):
            bad("len", line)
        return len(a[0])

    def n_push(a, line):
        if typename(a[0]) != "list":
            bad("push", line)
        a[0].append(a[1])
        return None

    def n_pop(a, line):
        if typename(a[0]) != "list" or len(a[0]) == 0:
            bad("pop", line)
        return a[0].pop()

    def n_range(a, line):
        if typename(a[0]) != "int" or typename(a[1]) != "int":
            bad("range", line)
        return list(range(a[0], a[1]))

    def n_slice(a, line):
        x, lo, hi = a
        if typename(x) not in ("string", "list") or typename(lo) != "int" or typename(hi) != "int" \
                or not (0 <= lo <= hi <= len(x)):
            bad("slice", line)
        return x[lo:hi]

    root = Env(None)
    for nm, na, fn in [("print", -1, n_print), ("str", 1, lambda a, l: display(a[0])),
                       ("type", 1, lambda a, l: typename(a[0])), ("len", 1, n_len), ("push", 2, n_push),
                       ("pop", 1, n_pop), ("range", 2, n_range), ("slice", 3, n_slice)]:
        root.vars[nm] = Native(nm, na, fn)
    prog = Parser(tokenize(src)).parse_program()
    try:
        exec_list(prog, Env(root))
    except RtErr as e:
        lines.append(f"error at line {e.line}: {e}")
    sys.stdout.write("".join(l + "\n" for l in lines))


main()
