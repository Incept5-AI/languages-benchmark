"""mini_interpreter — a tree-walking interpreter for "Tern", a small language fully specified in the prompt.

Hard parts: 64-bit integers with overflow detection (BigInt in TypeScript, explicit checks in Python), division
that truncates toward zero, value-returning `and`/`or`, truthiness of 0 / "" / [], closures that capture scopes
by reference with a fresh variable per `for` iteration, `for` re-reading the list length (pushes during the loop),
list aliasing, deep list equality, the display form (quoted/escaped strings inside lists, `[...]` for cycles,
args displayed only after all are evaluated), exact runtime-error messages and lines, and one compute-heavy
program (millions of evaluated nodes) that a sane interpreter finishes in seconds.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = r"""
Write a {LANG} program that interprets programs written in **Tern**, a small dynamically typed language
specified below.

The program is run with one argument: a directory containing `program.tern`. Run that Tern program; its output
is your program's standard output. Programs are always lexically and syntactically valid and use only what is
described here; only the runtime errors listed at the end can occur.
Some test programs execute a few million loop iterations: a direct tree-walking interpreter is fast enough,
but avoid needlessly slow designs (e.g. re-scanning source text or copying scopes on every call).

## Lexical structure
Source is ASCII. Spaces, tabs, `\r` and newlines separate tokens; `#` starts a comment that runs to the end of the
line. Lines are numbered from 1 (each newline starts the next line).
- Integer literal: decimal digits, value at most 9223372036854775807 (there are no negative literals; `-` is an
  operator).
- String literal: `"` … `"` on one line, with the escapes `\n` `\t` `\"` `\\` (no others).
- Identifier: `[A-Za-z_][A-Za-z0-9_]*`, except the keywords
  `let fn if else while for in return break continue and or not true false nil`.
- Operators and punctuation (longest match first): `== != <= >= < > = + - * / % ( ) [ ] { } , ;`

## Grammar
```
program := stmt*
block   := "{" stmt* "}"
stmt    := "let" IDENT "=" expr ";"
         | "fn" IDENT "(" params? ")" block
         | ifstmt
         | "while" expr block
         | "for" IDENT "in" expr block
         | "return" expr? ";"  |  "break" ";"  |  "continue" ";"
         | IDENT "=" expr ";"
         | postfix "[" expr "]" "=" expr ";"
         | expr ";"
ifstmt  := "if" expr block ( "else" ( ifstmt | block ) )?
params  := IDENT ( "," IDENT )*
postfix := primary ( "(" ( expr ( "," expr )* )? ")"  |  "[" expr "]" )*
primary := INT | STRING | "true" | "false" | "nil" | IDENT | "(" expr ")"
         | "[" ( expr ( "," expr )* )? "]"  |  "fn" "(" params? ")" block
```
Expression precedence, lowest first: `or` (left-assoc.) · `and` (left) · prefix `not` · `== != < <= > >=` (one
level, left) · `+ -` (left) · `* / %` (left) · prefix `-` · postfix call/index. So `not a == b` is
`not (a == b)` and `-a * b` is `(-a) * b`.

## Values and operators
Types: `int` (64-bit signed), `bool`, `string`, `list` (mutable, held by reference: assignment and argument
passing share the same list), `function`, `nil`. These type names are used by `type()` and in error messages.
- **Truthiness:** `false`, `nil`, `0`, `""` and `[]` (any empty list) are falsy; every other value is truthy.
  `if` and `while` test truthiness.
- `a and b`: evaluate `a`; if it is falsy the result is `a` (and `b` is not evaluated), otherwise the result is
  `b`. `a or b`: if `a` is truthy the result is `a`, otherwise `b`. `not a` is `true` or `false`.
- `+` adds two ints, concatenates two strings, or concatenates two lists into a **new** list. `-` `*` `/` `%`
  take two ints. `/` truncates toward zero; `a % b` is `a - (a / b) * b` (sign of `a`); `x % -1` is `0`.
  Any int result outside [-2^63, 2^63-1] is an error, including `-x` and `/`.
- `<` `<=` `>` `>=` compare two ints, or two strings (bytewise lexicographic).
- `==` / `!=` never fail. Values of different types are unequal (`1 == true` is false). Ints, bools and strings
  compare by value; `nil == nil`; lists are equal when they have the same length and pairwise equal elements
  (recursively); a function is equal only to itself. (Programs never compare lists that contain themselves.)
- `x[i]`: `x` is a list (the element) or a string (a one-character string); `i` is an int with
  `0 <= i < length` (negative indices are errors). `x[i] = v` works only on lists.
- Evaluation is left to right. Both operands of a binary operator (other than `and`/`or`), the callee and all
  arguments of a call, and the object and index of `x[i]` are evaluated before anything is checked. In
  `x[i] = v` the order is `x`, `i`, `v`, then the checks; in `name = v` and `let name = v`, `v` is evaluated
  first.

## Scopes, functions, loops
- Scopes nest; a name resolves to the innermost scope that declares it. `let` declares a new variable in the
  current scope (a program never declares a name twice in the same scope). `name = v` assigns the innermost
  existing variable and never declares. The global scope's parent is a scope holding the builtins.
- Every execution of a block (`if`/`else`/`while`/`for` body) creates a new scope, except a function body.
- Functions are closures: they capture the scope they are created in **by reference**, so they see later
  assignments to those variables and their own assignments are seen by everyone else. `fn name(…) {…}` declares
  `name` in the current scope and assigns the function to it (so it can call itself). Names are looked up when the expression is evaluated, so a function also sees variables declared in its captured scope after it was created.
- A call evaluates the callee and the arguments, then creates a new scope (whose parent is the captured scope)
  binding the parameters to the arguments, and runs the body's statements directly in that scope.
  `return;` or reaching the end of the body returns `nil`. Recursion is never deeper than 300 calls.
- `for x in e { … }`: evaluate `e` once (it must be a list). With an index `i` starting at 0: while `i` is less
  than the list's *current* length, run the body in a fresh scope in which a new variable `x` holds `list[i]`,
  then add 1 to `i` (also after `continue`). So elements pushed during the loop are visited, and closures made
  in different iterations see different `x` variables.
- `break` / `continue` act on the innermost loop. They never appear outside a loop, nor `return` outside a
  function.

## Builtins
Builtins are ordinary function values (they can be stored and passed around).
- `print(v, …)` — any number of arguments: their display forms separated by one space, then a newline.
- `str(v)` — the display form as a string. `type(v)` — the type name as a string.
- `len(x)` — length of a string or list. `push(list, v)` — append `v`, return `nil`.
  `pop(list)` — remove and return the last element (the list must not be empty).
- `range(a, b)` — a new list `[a, a+1, …, b-1]` (empty if `b <= a`); both ints.
- `slice(x, a, b)` — `x` a string or list, ints with `0 <= a <= b <= len(x)`: a new string/list of elements
  `a … b-1`.

**Display form:** an int in decimal (`-5`); `true`, `false`, `nil`; any function (builtins too) `<fn>`. A string
is its raw characters — but inside a list it is written in double quotes, escaping `\` as `\\`, `"` as `\"`,
newline as `\n` and tab as `\t`. A list is `[`, its elements' display forms joined by `, `, then `]`. A list that
is reached again while it is still being displayed (it contains itself, directly or indirectly) is shown as
`[...]`; a list that merely appears more than once without being its own ancestor is shown in full each time.

## Runtime errors
On a runtime error the Tern program stops: everything printed so far stays, then print
`error at line <L>: <message>`, and exit with status 0. `<L>` is the line of the token in the last column.

| condition | message | token |
|---|---|---|
| binary operator applied to types it does not accept | `cannot apply <op> to <type> and <type>` | operator |
| prefix `-` on a non-int | `cannot apply - to <type>` | `-` |
| int result out of range | `integer overflow` | operator |
| `/` or `%` with right operand 0 (two ints) | `division by zero` | operator |
| reading or assigning an undeclared name | `undefined variable <name>` | the name |
| calling a value that is not a function | `cannot call <type>` | `(` |
| wrong number of arguments (user function or builtin) | `expected <n> arguments, got <m>` | `(` |
| invalid argument to a builtin (checked after the count) | `bad argument to <builtin's own name>` | `(` |
| `x[i]` where `x` is not a list/string or `i` is not an int | `cannot index <type of x> with <type of i>` | `[` |
| `x[i] = v` where `x` is not a list | `cannot assign into <type of x>` | `[` |
| `x[i] = v` on a list where `i` is not an int | `cannot index list with <type of i>` | `[` |
| index outside `0 … length-1` (read or write) | `index <i> out of range for length <n>` | `[` |
| `for` over a non-list | `cannot iterate <type>` | `for` |

## Example
```
fn make_counter() {
  let n = 0;
  return fn() { n = n + 1; return n; };
}
let c = make_counter();
c(); c();
let xs = [1, "t\"wo", [nil, true]];
let ys = xs;
push(ys, -7 / 2);
print(c(), xs, len(xs), -7 % 2);
print("a" + "b", 0 or "zero", 1 and "", xs == ys, [c] == [c]);
print(xs[4]);
```
prints
```
3 [1, "t\"wo", [nil, true], -3] 4 -1
ab zero  true true
error at line 12: index 4 out of range for length 4
```
"""

# ══ oracle: lexer → closure-compiling parser ═════════════════════════════════════════════════════════════
MIN, MAX = -(1 << 63), (1 << 63) - 1
KW = {"let", "fn", "if", "else", "while", "for", "in", "return", "break", "continue", "and", "or", "not",
      "true", "false", "nil"}


class TErr(Exception):
    def __init__(self, line, msg):
        self.line, self.msg = line, msg


class Brk(Exception):
    pass


class Cont(Exception):
    pass


class Ret(Exception):
    def __init__(self, v):
        self.v = v


class Scope:
    __slots__ = ("v", "p")

    def __init__(self, v, p):
        self.v, self.p = v, p


class Fn:
    __slots__ = ("params", "body", "scope")

    def __init__(self, params, body, scope):
        self.params, self.body, self.scope = params, body, scope


class Builtin:
    __slots__ = ("name", "arity", "impl")

    def __init__(self, name, arity, impl):
        self.name, self.arity, self.impl = name, arity, impl


def tn(v):
    t = type(v)
    if v is None:
        return "nil"
    if t is bool:
        return "bool"
    if t is int:
        return "int"
    if t is str:
        return "string"
    if t is list:
        return "list"
    return "function"


def truthy(v):
    if v is None or v is False:
        return False
    t = type(v)
    if t is int or t is str or t is list:
        return len(v) > 0 if t is not int else v != 0
    return True


def eq(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is list:
        return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b))
    if type(a) in (Fn, Builtin):
        return a is b
    return a == b


def show(v, nested=False, stack=None):
    t = type(v)
    if t is str:
        if not nested:
            return v
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'
    if v is None:
        return "nil"
    if t is bool:
        return "true" if v else "false"
    if t is int:
        return str(v)
    if t is list:
        stack = stack or []
        if any(s is v for s in stack):
            return "[...]"
        stack.append(v)
        r = "[" + ", ".join(show(x, True, stack) for x in v) + "]"
        stack.pop()
        return r
    return "<fn>"


def lex(src):
    toks, i, line, n = [], 0, 1, len(src)
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            while i < n and src[i] != "\n":
                i += 1
        elif c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            toks.append(("int", int(src[i:j]), line))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            w = src[i:j]
            toks.append(("kw" if w in KW else "id", w, line))
            i = j
        elif c == '"':
            j, buf = i + 1, []
            while src[j] != '"':
                if src[j] == "\\":
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}[src[j + 1]])
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            toks.append(("str", "".join(buf), line))
            i = j + 1
        elif src[i:i + 2] in ("==", "!=", "<=", ">="):
            toks.append(("op", src[i:i + 2], line))
            i += 2
        elif c in "<>=+-*/%()[]{},;":
            toks.append(("op", c, line))
            i += 1
        else:
            raise SyntaxError(f"bad char {c!r} at line {line}")
    toks.append(("eof", None, line))
    return toks


def ck(v, line):
    if v < MIN or v > MAX:
        raise TErr(line, "integer overflow")
    return v


def binop(op, a, b, line):
    ta, tb = type(a), type(b)
    if op == "==":
        return eq(a, b)
    if op == "!=":
        return not eq(a, b)
    if ta is int and tb is int:
        if op == "+":
            return ck(a + b, line)
        if op == "-":
            return ck(a - b, line)
        if op == "*":
            return ck(a * b, line)
        if op in ("/", "%"):
            if b == 0:
                raise TErr(line, "division by zero")
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            return ck(q, line) if op == "/" else a - q * b
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    if ta is str and tb is str and op in ("+", "<", "<=", ">", ">="):
        ab, bb = a.encode(), b.encode()
        return a + b if op == "+" else {"<": ab < bb, "<=": ab <= bb, ">": ab > bb, ">=": ab >= bb}[op]
    if ta is list and tb is list and op == "+":
        return a + b
    raise TErr(line, f"cannot apply {op} to {tn(a)} and {tn(b)}")


def invoke(f, args, line):
    if type(f) is Fn:
        if len(args) != len(f.params):
            raise TErr(line, f"expected {len(f.params)} arguments, got {len(args)}")
        sc = Scope(dict(zip(f.params, args)), f.scope)
        try:
            for st in f.body:
                st(sc)
        except Ret as r:
            return r.v
        return None
    if type(f) is Builtin:
        if f.arity is not None and len(args) != f.arity:
            raise TErr(line, f"expected {f.arity} arguments, got {len(args)}")
        return f.impl(args, line)
    raise TErr(line, f"cannot call {tn(f)}")


class Compiler:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def at(self, kind, val=None):
        tk = self.t[self.i]
        return tk[0] == kind and (val is None or tk[1] == val)

    def take(self, kind=None, val=None):
        tk = self.t[self.i]
        if (kind and tk[0] != kind) or (val is not None and tk[1] != val):
            raise SyntaxError(f"expected {kind} {val} got {tk}")
        self.i += 1
        return tk

    def program(self):
        out = []
        while not self.at("eof"):
            out.append(self.stmt())
        return out

    def block_stmts(self):
        self.take("op", "{")
        out = []
        while not self.at("op", "}"):
            out.append(self.stmt())
        self.take("op", "}")
        return out

    def block(self):
        stmts = self.block_stmts()

        def run(env):
            sc = Scope({}, env)
            for st in stmts:
                st(sc)
        return run

    def params(self):
        self.take("op", "(")
        ps = []
        if not self.at("op", ")"):
            ps.append(self.take("id")[1])
            while self.at("op", ","):
                self.take()
                ps.append(self.take("id")[1])
        self.take("op", ")")
        return ps

    def stmt(self):
        tk = self.peek()
        if tk[0] == "kw":
            w = tk[1]
            if w == "let":
                self.take()
                name = self.take("id")[1]
                self.take("op", "=")
                e = self.expr()
                self.take("op", ";")

                def s_let(env):
                    env.v[name] = e(env)
                return s_let
            if w == "fn" and self.peek(1)[0] == "id":
                self.take()
                name = self.take("id")[1]
                ps = self.params()
                body = self.block_stmts()

                def s_fn(env):
                    env.v[name] = Fn(ps, body, env)
                return s_fn
            if w == "if":
                return self.ifstmt()
            if w == "while":
                self.take()
                cond = self.expr()
                body = self.block()

                def s_while(env):
                    while truthy(cond(env)):
                        try:
                            body(env)
                        except Brk:
                            break
                        except Cont:
                            pass
                return s_while
            if w == "for":
                line = self.take()[2]
                name = self.take("id")[1]
                self.take("kw", "in")
                e = self.expr()
                body = self.block()

                def s_for(env):
                    lst = e(env)
                    if type(lst) is not list:
                        raise TErr(line, f"cannot iterate {tn(lst)}")
                    i = 0
                    while i < len(lst):
                        try:
                            body(Scope({name: lst[i]}, env))
                        except Brk:
                            break
                        except Cont:
                            pass
                        i += 1
                return s_for
            if w == "return":
                self.take()
                e = None if self.at("op", ";") else self.expr()
                self.take("op", ";")

                def s_ret(env):
                    raise Ret(None if e is None else e(env))
                return s_ret
            if w in ("break", "continue"):
                self.take()
                self.take("op", ";")
                exc = Brk if w == "break" else Cont

                def s_bc(env):
                    raise exc()
                return s_bc
        if tk[0] == "id" and self.peek(1)[:2] == ("op", "="):
            name, line = tk[1], tk[2]
            self.take()
            self.take()
            e = self.expr()
            self.take("op", ";")

            def s_assign(env):
                v = e(env)
                sc = env
                while sc is not None:
                    if name in sc.v:
                        sc.v[name] = v
                        return
                    sc = sc.p
                raise TErr(line, f"undefined variable {name}")
            return s_assign
        e = self.expr()
        if self.at("op", "="):
            if not hasattr(e, "index_parts"):
                raise SyntaxError("bad assignment target")
            obj, idx, line = e.index_parts
            self.take()
            ve = self.expr()
            self.take("op", ";")

            def s_iassign(env):
                o, i, v = obj(env), idx(env), ve(env)
                if type(o) is not list:
                    raise TErr(line, f"cannot assign into {tn(o)}")
                if type(i) is not int:
                    raise TErr(line, f"cannot index list with {tn(i)}")
                if not 0 <= i < len(o):
                    raise TErr(line, f"index {i} out of range for length {len(o)}")
                o[i] = v
            return s_iassign
        self.take("op", ";")

        def s_expr(env):
            e(env)
        return s_expr

    def ifstmt(self):
        self.take("kw", "if")
        cond = self.expr()
        then = self.block()
        other = None
        if self.at("kw", "else"):
            self.take()
            other = self.ifstmt() if self.at("kw", "if") else self.block()

        def s_if(env):
            if truthy(cond(env)):
                then(env)
            elif other is not None:
                other(env)
        return s_if

    # expressions
    def expr(self):
        return self.e_or()

    def e_or(self):
        a = self.e_and()
        while self.at("kw", "or"):
            self.take()
            b = self.e_and()
            a = (lambda a, b: lambda env: (lambda x: x if truthy(x) else b(env))(a(env)))(a, b)
        return a

    def e_and(self):
        a = self.e_not()
        while self.at("kw", "and"):
            self.take()
            b = self.e_not()
            a = (lambda a, b: lambda env: (lambda x: b(env) if truthy(x) else x)(a(env)))(a, b)
        return a

    def e_not(self):
        if self.at("kw", "not"):
            self.take()
            a = self.e_not()
            return lambda env: not truthy(a(env))
        return self.e_bin(0)

    LEVELS = [("==", "!=", "<", "<=", ">", ">="), ("+", "-"), ("*", "/", "%")]

    def e_bin(self, lvl):
        if lvl == 3:
            return self.e_unary()
        a = self.e_bin(lvl + 1)
        while self.peek()[0] == "op" and self.peek()[1] in self.LEVELS[lvl]:
            _, op, line = self.take()
            b = self.e_bin(lvl + 1)
            a = (lambda a, b, op, line: lambda env: binop(op, a(env), b(env), line))(a, b, op, line)
        return a

    def e_unary(self):
        if self.at("op", "-"):
            line = self.take()[2]
            a = self.e_unary()

            def neg(env):
                v = a(env)
                if type(v) is not int:
                    raise TErr(line, f"cannot apply - to {tn(v)}")
                return ck(-v, line)
            return neg
        return self.e_postfix()

    def e_postfix(self):
        a = self.primary()
        while True:
            if self.at("op", "("):
                line = self.take()[2]
                args = []
                if not self.at("op", ")"):
                    args.append(self.expr())
                    while self.at("op", ","):
                        self.take()
                        args.append(self.expr())
                self.take("op", ")")
                a = (lambda f, args, line: lambda env: invoke(f(env), [x(env) for x in args], line))(a, args, line)
            elif self.at("op", "["):
                line = self.take()[2]
                ie = self.expr()
                self.take("op", "]")

                def mk(obj, ie, line):
                    def index(env):
                        o, i = obj(env), ie(env)
                        to = type(o)
                        if (to is not list and to is not str) or type(i) is not int:
                            raise TErr(line, f"cannot index {tn(o)} with {tn(i)}")
                        if not 0 <= i < len(o):
                            raise TErr(line, f"index {i} out of range for length {len(o)}")
                        return o[i]
                    index.index_parts = (obj, ie, line)
                    return index
                a = mk(a, ie, line)
            else:
                return a

    def primary(self):
        kind, val, line = self.take()
        if kind in ("int", "str"):
            return lambda env: val
        if kind == "kw":
            if val in ("true", "false", "nil"):
                c = {"true": True, "false": False, "nil": None}[val]
                return lambda env: c
            if val == "fn":
                ps = self.params()
                body = self.block_stmts()
                return lambda env: Fn(ps, body, env)
        if kind == "id":
            def var(env):
                sc = env
                while sc is not None:
                    if val in sc.v:
                        return sc.v[val]
                    sc = sc.p
                raise TErr(line, f"undefined variable {val}")
            return var
        if (kind, val) == ("op", "("):
            e = self.expr()
            self.take("op", ")")
            return e
        if (kind, val) == ("op", "["):
            items = []
            if not self.at("op", "]"):
                items.append(self.expr())
                while self.at("op", ","):
                    self.take()
                    items.append(self.expr())
            self.take("op", "]")
            return lambda env: [x(env) for x in items]
        raise SyntaxError(f"unexpected {kind} {val!r} at line {line}")


def run_tern(src: str) -> str:
    out = []

    def b_print(a, line):
        out.append(" ".join(show(x) for x in a))

    def need(cond, name, line):
        if not cond:
            raise TErr(line, f"bad argument to {name}")

    def b_len(a, line):
        need(type(a[0]) in (str, list), "len", line)
        return len(a[0])

    def b_push(a, line):
        need(type(a[0]) is list, "push", line)
        a[0].append(a[1])

    def b_pop(a, line):
        need(type(a[0]) is list and a[0], "pop", line)
        return a[0].pop()

    def b_range(a, line):
        need(type(a[0]) is int and type(a[1]) is int, "range", line)
        return list(range(a[0], a[1]))

    def b_slice(a, line):
        x, s, e = a
        need(type(x) in (str, list) and type(s) is int and type(e) is int and 0 <= s <= e <= len(x), "slice", line)
        return x[s:e]

    builtins = {
        "print": Builtin("print", None, b_print),
        "str": Builtin("str", 1, lambda a, l: show(a[0])),
        "type": Builtin("type", 1, lambda a, l: tn(a[0])),
        "len": Builtin("len", 1, b_len),
        "push": Builtin("push", 2, b_push),
        "pop": Builtin("pop", 1, b_pop),
        "range": Builtin("range", 2, b_range),
        "slice": Builtin("slice", 3, b_slice),
    }
    prog = Compiler(lex(src)).program()
    glob = Scope({}, Scope(builtins, None))
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        for st in prog:
            st(glob)
    except TErr as e:
        out.append(f"error at line {e.line}: {e.msg}")
    finally:
        sys.setrecursionlimit(old)
    return "\n".join(out)


# ══ programs ═════════════════════════════════════════════════════════════════════════════════════════════
def fill(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace(f"@{k}@", str(v))
    assert "@" not in tpl, tpl
    return tpl.lstrip("\n")


PROG_A = r"""
# Tern: closures, higher-order functions, lists and strings
fn fib(n) {
  if n < 2 { return n; }
  return fib(n - 1) + fib(n - 2);
}

fn make_counter(start, step) {
  let count = start;
  fn next() {
    count = count + step;
    return count;
  }
  return next;
}

fn map(f, xs) {
  let out = [];
  for x in xs { push(out, f(x)); }
  return out;
}

fn filter(p, xs) {
  let out = [];
  for x in xs {
    if p(x) { push(out, x); }
  }
  return out;
}

fn fold(f, acc, xs) {
  for x in xs { acc = f(acc, x); }
  return acc;
}

print("fib:", map(fib, range(@F0@, @F1@)));
let c1 = make_counter(@C0@, @C1@);
let c2 = c1;
let c3 = make_counter(0, -1);
print(c1(), c2(), c1(), c3(), c3(), c1 == c2, c1 == c3);

# integer division truncates toward zero
for a in [@NUMS@] {
  print(a, a / @D@, a % @D@, a / -@D@, a % -@D@);
}

# lists are shared by reference
let xs = [@X0@, @X1@, @X2@];
let ys = xs;
let zs = xs + [];
push(ys, @X3@);
zs[0] = 99;
print(xs, ys, zs, xs == ys, xs == zs, len(xs));

let words = ["a\"b", "tab\there", "back\\slash", "nl\nx"];
let nested = [1, [2, ["three", nil]], true, words[@W@], print];
print(nested);
print(str(nested) + "!", len(str(nested)), words[@W@]);

# truthiness and short-circuit evaluation
fn noisy(tag, v) {
  print("eval", tag);
  return v;
}
print(0 or "zero-falsy", "" or nil, [] or [0], nil and boom, @X1@ and @X2@);
print(noisy("a", false) and noisy("b", true));
print(noisy("c", @X0@) or noisy("d", 8));
print(not 0, not [0], 1 == true, nil == nil, "ab" < "b", [1, [2, "x"]] == [1, [2, "x"]], [1] != [1, 1]);

# each loop iteration has its own variable
let fs = [];
for i in range(0, @NF@) {
  let sq = i * i;
  push(fs, fn() { return [i, sq]; });
}
print(map(fn(f) { return f(); }, fs));

# the loop sees elements pushed while it runs
let q = [1];
for v in q {
  if v < @QLIM@ { push(q, v * 3); }
}
print(q, filter(fn(v) { return v % 2 == 1 and v > 3; }, q));

let loop = [1, "two"];
push(loop, loop);
let pair = [loop, [loop]];
print(loop, pair);

let s = "";
let i = 0;
while i < @SN@ {
  s = s + str(i) + ",";
  i = i + 1;
}
print(s, len(s), slice(s, 2, 6), type(s), type(loop), type(fib), type(nil), type(i > 2));
let max = 9223372036854775807;
print(max, -max - 1, (-max - 1) / 2, -(max / 3));

let total = 0;
let k = 0;
while k < @LOOP@ {
  total = total + k * k % 7;
  k = k + 1;
}
print("total", total, fold(fn(a, b) { return a * b; }, 1, [@X0@, @X1@, 5]));
print("last", words[-1]);
print("not reached");
"""

PROG_B = r"""
# Tern: objects as closures, sorting, recursion
fn make_account(owner, balance) {
  let history = [];
  fn deposit(amount) {
    if amount <= 0 { return false; }
    balance = balance + amount;
    push(history, "+" + str(amount));
    return true;
  }
  fn withdraw(amount) {
    if amount > balance {
      push(history, "!" + str(amount));
      return false;
    }
    balance = balance - amount;
    push(history, "-" + str(amount));
    return balance;
  }
  fn get(field) {
    if field == "owner" { return owner; }
    if field == "balance" { return balance; }
    if field == "history" { return history; }
  }
  return [deposit, withdraw, get];
}

let acct = make_account("Ann \"the\" Bank", @B0@);
let dep = acct[0];
let wd = acct[1];
let get = acct[2];
print(dep(@A1@), wd(@A2@), wd(@A3@), dep(0), dep(@A4@));
let h = get("history");
push(h, "manual\tnote");
print(get("owner"), get("balance"), get("history"), get("nothing"));
print(acct, len(acct));

fn qsort(xs) {
  if len(xs) <= 1 { return xs; }
  let pivot = xs[0];
  let less = [];
  let more = [];
  for x in slice(xs, 1, len(xs)) {
    if x < pivot { push(less, x); } else { push(more, x); }
  }
  return qsort(less) + [pivot] + qsort(more);
}
let data = [@DATA@];
let sorted = qsort(data);
print(sorted, data == sorted, qsort(["pear", "Apple", "fig", "apple", "", "Fig"]));

let row = "";
for a in [@DIVS@] {
  row = row + str(a / @DV@) + ":" + str(a % @DV@) + " ";
}
print(row);

fn hanoi(n, from, to, via, moves) {
  if n == 0 { return 0; }
  let c = hanoi(n - 1, from, via, to, moves);
  push(moves, from + to);
  return c + 1 + hanoi(n - 1, via, to, from, moves);
}
let moves = [];
print(hanoi(@HN@, "A", "C", "B", moves), len(moves), slice(moves, 0, 5));

let found = nil;
let n = @START@;
while true {
  n = n + 1;
  if n % 3 == 0 { continue; }
  if n % 7 == 0 and n % 2 == 1 {
    found = n;
    break;
  }
}
print("found", found);

fn compose(f, g) {
  return fn(x) { return f(g(x)); };
}
let adders = [];
for k in range(1, 4) {
  push(adders, fn(x) { return x + k * @KM@; });
}
let add_all = compose(adders[0], compose(adders[1], adders[2]));
print(add_all(10), adders[2](0));

let log = [];
fn t(name, v) {
  push(log, name);
  return v;
}
let r = t("a", 0) or t("b", "") or t("c", [nil]) or t("d", 1);
print(r, log, t("e", nil) and t("f", 1), log);
print(not "", not "x", [] == [], [[]] == [[nil]], "abc" <= "abd", "Z" < "a", 2 == 2 == true);

let zero_row = [0, 0, 0];
let grid = [zero_row, zero_row, [0, 0, 0]];
grid[0][1] = @GV@;
let cyc = [grid];
push(grid, cyc);
print(cyc, grid[1]);
let popped = pop(grid);
print(popped == cyc, grid);

let acc = 0;
let j = 0;
while j < @LOOP@ {
  if j % 5 == 0 { acc = acc - j; } else { acc = acc + j / 3; }
  j = j + 1;
}
print("acc", acc);

fn fact(n) {
  if n == 0 { return 1; }
  return n * fact(n - 1);
}
let m = @FS@;
while true {
  print(m, fact(m));
  m = m + 1;
}
"""

PROG_C = r"""
# Tern: number crunching
fn sieve(n) {
  let flags = [];
  let i = 0;
  while i <= n {
    push(flags, true);
    i = i + 1;
  }
  flags[0] = false;
  flags[1] = false;
  let p = 2;
  while p * p <= n {
    if flags[p] {
      let m = p * p;
      while m <= n {
        flags[m] = false;
        m = m + p;
      }
    }
    p = p + 1;
  }
  let primes = [];
  i = 0;
  for f in flags {
    if f { push(primes, i); }
    i = i + 1;
  }
  return primes;
}
let primes = sieve(@N@);
print(len(primes), slice(primes, 0, 10), primes[len(primes) - 1]);

fn collatz_len(n) {
  let c = 0;
  while n != 1 {
    if n % 2 == 0 { n = n / 2; } else { n = 3 * n + 1; }
    c = c + 1;
  }
  return c;
}
let best = 0;
let arg = 0;
let k = 1;
while k < @M@ {
  let c = collatz_len(k);
  if c > best {
    best = c;
    arg = k;
  }
  k = k + 1;
}
print("collatz", arg, best);

let h = @H0@;
for p in slice(primes, 0, @HP@) { h = (h * 31 + p) % 1000000007; }
print("hash", h, -h % 1000, -h / 1000);

fn make_fib() {
  let cache = [0, 1];
  fn fib(n) {
    while len(cache) <= n { push(cache, nil); }
    if cache[n] != nil { return cache[n]; }
    let v = fib(n - 1) + fib(n - 2);
    cache[n] = v;
    return v;
  }
  return fib;
}
let fib = make_fib();
print(fib(@FB@), fib(92));

fn reverse(s) {
  let r = "";
  let i = len(s) - 1;
  while i >= 0 {
    r = r + s[i];
    i = i - 1;
  }
  return r;
}
let pals = [];
for p in primes {
  if p > @PMIN@ and str(p) == reverse(str(p)) { push(pals, str(p)); }
  if len(pals) >= 6 { break; }
}
print(pals, reverse("a\"b\\c"), [reverse("x\ty")]);

let stack = [];
let ops = [3, 4, "+", 2, "*", 7, "-", 10, "%", @RP@, "/"];
for tok in ops {
  if type(tok) == "int" {
    push(stack, tok);
    continue;
  }
  let b = pop(stack);
  let a = pop(stack);
  if tok == "+" { push(stack, a + b); }
  if tok == "-" { push(stack, a - b); }
  if tok == "*" { push(stack, a * b); }
  if tok == "%" { push(stack, a % b); }
  if tok == "/" { push(stack, a / b); }
}
print("rpn", stack, stack[0] or "zero", not stack);

let makers = [];
let x = 0;
while x < 3 {
  let y = x * 10;
  push(makers, fn() { y = y + 1; return [x, y]; });
  x = x + 1;
}
print(makers[0](), makers[0](), makers[2](), x);
let al = [makers, 5];
push(al[0], al);
print(len(makers), al, "" == [] , nil or 0 or false);
let label = "count: " + len(pals);
print(label);
"""


def make_program(seed: int) -> str:
    rng = random.Random(4400 + seed)
    kind = seed % 3
    if kind == 0:
        d = rng.choice([3, 4, 5])
        nums = sorted(rng.sample(range(-20, 21), 5)) + [0]
        return fill(PROG_A, F0=0, F1=rng.choice([10, 11, 12]), C0=rng.randrange(-5, 5), C1=rng.choice([2, 3, 7]),
                    NUMS=", ".join(str(n) if n >= 0 else f"-{-n}" for n in nums), D=d,
                    X0=rng.randrange(1, 9), X1=rng.randrange(10, 99), X2=rng.randrange(100, 999),
                    X3=rng.randrange(1000, 9999), W=rng.randrange(4), NF=rng.choice([3, 4]),
                    QLIM=rng.choice([20, 50, 100]), SN=rng.choice([5, 6, 7]), LOOP=rng.choice([20000, 25000]))
    if kind == 1:
        b0 = rng.randrange(50, 150)
        data = [rng.randrange(-60, 60) for _ in range(12)]
        divs = sorted(rng.sample(range(-15, 16), 7))
        return fill(PROG_B, B0=b0, A1=rng.randrange(10, 50), A2=rng.randrange(20, 60), A3=rng.randrange(500, 900),
                    A4=rng.randrange(1, 20), DATA=", ".join(str(x) if x >= 0 else f"-{-x}" for x in data),
                    DIVS=", ".join(str(x) if x >= 0 else f"-{-x}" for x in divs),
                    DV=rng.choice(["4", "-4", "6", "-5"]), HN=rng.choice([5, 6, 7]), START=rng.randrange(20, 60),
                    KM=rng.choice([1, 10, 100]), GV=rng.randrange(1, 9), LOOP=rng.choice([30000, 40000]),
                    FS=rng.choice([17, 18]))
    return fill(PROG_C, N=rng.choice([120000, 130000]), M=rng.choice([18000, 20000]), H0=rng.randrange(1, 100),
                HP=rng.choice([1500, 2000]), FB=rng.choice([60, 70, 80]), PMIN=rng.choice([100, 1000]),
                RP=rng.choice([-2, 3, -4]))


class Challenge(H.Challenge):
    name = "mini_interpreter"
    title = "Interpret Tern: closures, recursion, lists, 64-bit ints, exact runtime errors"
    category = "lang"
    timeout = 30

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        src = make_program(seed)
        d = ctx.data_dir(self, seed)
        (d / "program.tern").write_text(src)
        return H.Case(args=[str(d)], expected=run_tern(src))
