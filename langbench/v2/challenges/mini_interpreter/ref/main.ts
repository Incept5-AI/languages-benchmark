import * as fs from "fs";
import * as path from "path";

// ---------- values ----------
// int: number (safe integer) or bigint (outside safe range, within i64); bool: boolean; string; nil: null
class TList { constructor(public a: Value[]) {} }
class Closure { constructor(public def: FnDef, public env: Scope) {} }
class Builtin { constructor(public name: string, public id: number) {} }
type Value = number | bigint | boolean | string | null | TList | Closure | Builtin;

class TErr { constructor(public line: number, public msg: string) {} }

const I64_MIN = -(2n ** 63n);
const I64_MAX = 2n ** 63n - 1n;

function isInt(v: Value): v is number | bigint { return typeof v === "number" || typeof v === "bigint"; }
function normBig(b: bigint, line: number): number | bigint {
  if (b < I64_MIN || b > I64_MAX) throw new TErr(line, "integer overflow");
  if (b >= BigInt(Number.MIN_SAFE_INTEGER) && b <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(b);
  return b;
}
function big(v: number | bigint): bigint { return typeof v === "bigint" ? v : BigInt(v); }

// ---------- scopes ----------
class Scope {
  ids: number[] = [];
  vals: Value[] = [];
  constructor(public parent: Scope | null) {}
  declare(id: number, v: Value) { this.ids.push(id); this.vals.push(v); }
}

const nameIds = new Map<string, number>();
const names: string[] = [];
function intern(s: string): number {
  let i = nameIds.get(s);
  if (i === undefined) { i = names.length; nameIds.set(s, i); names.push(s); }
  return i;
}

// ---------- lexer ----------
const T_INT = 0, T_STR = 1, T_ID = 2, T_KW = 3, T_OP = 4, T_EOF = 5;
interface Tok { kind: number; text: string; ival?: number | bigint; line: number; }
const KW = new Set(["let", "fn", "if", "else", "while", "for", "in", "return", "break", "continue", "and", "or", "not", "true", "false", "nil"]);

function lex(s: string): Tok[] {
  const out: Tok[] = [];
  let i = 0, line = 1;
  const n = s.length;
  const isDigit = (c: string) => c >= "0" && c <= "9";
  const isAlpha = (c: string) => (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_";
  while (i < n) {
    const c = s[i];
    if (c === "\n") { line++; i++; continue; }
    if (c === " " || c === "\t" || c === "\r") { i++; continue; }
    if (c === "#") { while (i < n && s[i] !== "\n") i++; continue; }
    if (isDigit(c)) {
      let j = i; while (j < n && isDigit(s[j])) j++;
      const text = s.substring(i, j);
      out.push({ kind: T_INT, text, ival: normBig(BigInt(text), line), line });
      i = j; continue;
    }
    if (isAlpha(c)) {
      let j = i; while (j < n && (isAlpha(s[j]) || isDigit(s[j]))) j++;
      const w = s.substring(i, j);
      out.push({ kind: KW.has(w) ? T_KW : T_ID, text: w, line });
      i = j; continue;
    }
    if (c === '"') {
      let sb = ""; i++;
      while (i < n && s[i] !== '"') {
        if (s[i] === "\\" && i + 1 < n) {
          const e = s[i + 1];
          sb += e === "n" ? "\n" : e === "t" ? "\t" : e;
          i += 2;
        } else { sb += s[i]; i++; }
      }
      i++;
      out.push({ kind: T_STR, text: sb, line });
      continue;
    }
    const two = s.substring(i, i + 2);
    if (two === "==" || two === "!=" || two === "<=" || two === ">=") { out.push({ kind: T_OP, text: two, line }); i += 2; continue; }
    out.push({ kind: T_OP, text: c, line }); i++;
  }
  out.push({ kind: T_EOF, text: "", line });
  return out;
}

// ---------- AST ----------
const NORMAL = 0, BRK = 1, CONT = 2, RET = 3;
let retVal: Value = null;

interface FnDef { params: number[]; body: Stmt[]; }
abstract class Expr { abstract eval(s: Scope): Value; }
abstract class Stmt { abstract exec(s: Scope): number; }

class Lit extends Expr { constructor(public v: Value) { super(); } eval(_s: Scope): Value { return this.v; } }
class Var extends Expr {
  constructor(public id: number, public line: number) { super(); }
  eval(s: Scope): Value {
    const id = this.id;
    for (let c: Scope | null = s; c !== null; c = c.parent) {
      const ids = c.ids;
      for (let i = ids.length - 1; i >= 0; i--) if (ids[i] === id) return c.vals[i];
    }
    throw new TErr(this.line, "undefined variable " + names[id]);
  }
}
class ListLit extends Expr {
  constructor(public items: Expr[]) { super(); }
  eval(s: Scope): Value { const a: Value[] = []; for (const e of this.items) a.push(e.eval(s)); return new TList(a); }
}
class FnExpr extends Expr { constructor(public d: FnDef) { super(); } eval(s: Scope): Value { return new Closure(this.d, s); } }
class Call extends Expr {
  constructor(public callee: Expr, public args: Expr[], public line: number) { super(); }
  eval(s: Scope): Value {
    const f = this.callee.eval(s);
    const av: Value[] = new Array(this.args.length);
    for (let i = 0; i < this.args.length; i++) av[i] = this.args[i].eval(s);
    return call(f, av, this.line);
  }
}
class Index extends Expr {
  constructor(public obj: Expr, public idx: Expr, public line: number) { super(); }
  eval(s: Scope): Value {
    const o = this.obj.eval(s), i = this.idx.eval(s);
    if (isInt(i)) {
      if (o instanceof TList) {
        const a = o.a;
        if (typeof i !== "number" || i < 0 || i >= a.length) throw new TErr(this.line, "index " + String(i) + " out of range for length " + a.length);
        return a[i];
      }
      if (typeof o === "string") {
        if (typeof i !== "number" || i < 0 || i >= o.length) throw new TErr(this.line, "index " + String(i) + " out of range for length " + o.length);
        return o[i];
      }
    }
    throw new TErr(this.line, "cannot index " + typeName(o) + " with " + typeName(i));
  }
}
class And extends Expr { constructor(public l: Expr, public r: Expr) { super(); } eval(s: Scope): Value { const a = this.l.eval(s); if (!truthy(a)) return a; return this.r.eval(s); } }
class Or extends Expr { constructor(public l: Expr, public r: Expr) { super(); } eval(s: Scope): Value { const a = this.l.eval(s); if (truthy(a)) return a; return this.r.eval(s); } }
class Not extends Expr { constructor(public e: Expr) { super(); } eval(s: Scope): Value { return !truthy(this.e.eval(s)); } }
class Neg extends Expr {
  constructor(public e: Expr, public line: number) { super(); }
  eval(s: Scope): Value {
    const v = this.e.eval(s);
    if (typeof v === "number") return v === 0 ? 0 : -v;
    if (typeof v === "bigint") return normBig(-v, this.line);
    throw new TErr(this.line, "cannot apply - to " + typeName(v));
  }
}
const OP_ADD = 0, OP_SUB = 1, OP_MUL = 2, OP_DIV = 3, OP_MOD = 4, OP_EQ = 5, OP_NE = 6, OP_LT = 7, OP_LE = 8, OP_GT = 9, OP_GE = 10;
const OPS = ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">="];
class Bin extends Expr {
  constructor(public op: number, public l: Expr, public r: Expr, public line: number) { super(); }
  bad(a: Value, b: Value): TErr { return new TErr(this.line, "cannot apply " + OPS[this.op] + " to " + typeName(a) + " and " + typeName(b)); }
  eval(s: Scope): Value {
    const a = this.l.eval(s), b = this.r.eval(s);
    const op = this.op, line = this.line;
    switch (op) {
      case OP_EQ: return valEq(a, b);
      case OP_NE: return !valEq(a, b);
      case OP_ADD:
        if (typeof a === "number" && typeof b === "number") {
          const z = a + b; if (Number.isSafeInteger(z)) return z;
          return normBig(BigInt(a) + BigInt(b), line);
        }
        if (isInt(a) && isInt(b)) return normBig(big(a) + big(b), line);
        if (typeof a === "string" && typeof b === "string") return a + b;
        if (a instanceof TList && b instanceof TList) return new TList(a.a.concat(b.a));
        throw this.bad(a, b);
      case OP_LT: case OP_LE: case OP_GT: case OP_GE: {
        let c: number;
        if (isInt(a) && isInt(b)) c = a < b ? -1 : a > b ? 1 : 0;
        else if (typeof a === "string" && typeof b === "string") c = a < b ? -1 : a > b ? 1 : 0;
        else throw this.bad(a, b);
        switch (op) { case OP_LT: return c < 0; case OP_LE: return c <= 0; case OP_GT: return c > 0; default: return c >= 0; }
      }
      default: {
        if (!(isInt(a) && isInt(b))) throw this.bad(a, b);
        switch (op) {
          case OP_SUB:
            if (typeof a === "number" && typeof b === "number") { const z = a - b; if (Number.isSafeInteger(z)) return z; }
            return normBig(big(a) - big(b), line);
          case OP_MUL:
            if (typeof a === "number" && typeof b === "number") { const z = a * b; if (Number.isSafeInteger(z)) return z === 0 ? 0 : z; }
            return normBig(big(a) * big(b), line);
          case OP_DIV: {
            if (b === 0) throw new TErr(line, "division by zero");
            if (typeof a === "number" && typeof b === "number") { const q = (a - (a % b)) / b; return q === 0 ? 0 : q; }
            return normBig(big(a) / big(b), line);
          }
          default: {
            if (b === 0) throw new TErr(line, "division by zero");
            if (typeof a === "number" && typeof b === "number") { const m = a % b; return m === 0 ? 0 : m; }
            return normBig(big(a) % big(b), line);
          }
        }
      }
    }
  }
}

class Block extends Stmt {
  constructor(public body: Stmt[]) { super(); }
  exec(s: Scope): number { return runIn(this.body, new Scope(s)); }
}
function runIn(body: Stmt[], sc: Scope): number {
  for (let i = 0; i < body.length; i++) { const r = body[i].exec(sc); if (r !== NORMAL) return r; }
  return NORMAL;
}
class Let extends Stmt { constructor(public id: number, public e: Expr) { super(); } exec(s: Scope): number { const v = this.e.eval(s); s.declare(this.id, v); return NORMAL; } }
class FnDecl extends Stmt { constructor(public id: number, public d: FnDef) { super(); } exec(s: Scope): number { s.declare(this.id, new Closure(this.d, s)); return NORMAL; } }
class If extends Stmt {
  constructor(public c: Expr, public t: Stmt, public e: Stmt | null) { super(); }
  exec(s: Scope): number { if (truthy(this.c.eval(s))) return this.t.exec(s); if (this.e !== null) return this.e.exec(s); return NORMAL; }
}
class While extends Stmt {
  constructor(public c: Expr, public b: Block) { super(); }
  exec(s: Scope): number {
    while (truthy(this.c.eval(s))) {
      const r = this.b.exec(s);
      if (r === BRK) break;
      if (r === RET) return r;
    }
    return NORMAL;
  }
}
class For extends Stmt {
  constructor(public id: number, public e: Expr, public b: Block, public line: number) { super(); }
  exec(s: Scope): number {
    const v = this.e.eval(s);
    if (!(v instanceof TList)) throw new TErr(this.line, "cannot iterate " + typeName(v));
    const a = v.a;
    for (let i = 0; i < a.length; i++) {
      const sc = new Scope(s);
      sc.declare(this.id, a[i]);
      const r = runIn(this.b.body, sc);
      if (r === BRK) break;
      if (r === RET) return r;
    }
    return NORMAL;
  }
}
class Return extends Stmt { constructor(public e: Expr | null) { super(); } exec(s: Scope): number { retVal = this.e === null ? null : this.e.eval(s); return RET; } }
class Break extends Stmt { exec(_s: Scope): number { return BRK; } }
class Continue extends Stmt { exec(_s: Scope): number { return CONT; } }
class Assign extends Stmt {
  constructor(public id: number, public line: number, public e: Expr) { super(); }
  exec(s: Scope): number {
    const v = this.e.eval(s);
    const id = this.id;
    for (let c: Scope | null = s; c !== null; c = c.parent) {
      const ids = c.ids;
      for (let i = ids.length - 1; i >= 0; i--) if (ids[i] === id) { c.vals[i] = v; return NORMAL; }
    }
    throw new TErr(this.line, "undefined variable " + names[id]);
  }
}
class IndexAssign extends Stmt {
  constructor(public obj: Expr, public idx: Expr, public val: Expr, public line: number) { super(); }
  exec(s: Scope): number {
    const o = this.obj.eval(s), i = this.idx.eval(s), v = this.val.eval(s);
    if (!(o instanceof TList)) throw new TErr(this.line, "cannot assign into " + typeName(o));
    if (!isInt(i)) throw new TErr(this.line, "cannot index list with " + typeName(i));
    const a = o.a;
    if (typeof i !== "number" || i < 0 || i >= a.length) throw new TErr(this.line, "index " + String(i) + " out of range for length " + a.length);
    a[i] = v;
    return NORMAL;
  }
}
class ExprStmt extends Stmt { constructor(public e: Expr) { super(); } exec(s: Scope): number { this.e.eval(s); return NORMAL; } }

// ---------- parser ----------
let toks: Tok[] = [];
let pos = 0;
const peek = () => toks[pos];
const next = () => toks[pos++];
const isOp = (t: string) => { const k = toks[pos]; return k.kind === T_OP && k.text === t; };
const isKw = (t: string) => { const k = toks[pos]; return k.kind === T_KW && k.text === t; };
function expect(t: string): Tok { const k = next(); if (k.text !== t) throw new Error("parse error line " + k.line + ": expected " + t + " got " + k.text); return k; }

function parseBlockBody(): Stmt[] {
  expect("{");
  const l: Stmt[] = [];
  while (!isOp("}")) l.push(parseStmt());
  expect("}");
  return l;
}
function parseFnRest(): FnDef {
  expect("(");
  const ps: number[] = [];
  if (!isOp(")")) {
    ps.push(intern(next().text));
    while (isOp(",")) { next(); ps.push(intern(next().text)); }
  }
  expect(")");
  return { params: ps, body: parseBlockBody() };
}
function parseIf(): Stmt {
  expect("if");
  const c = parseExpr();
  const t = new Block(parseBlockBody());
  let e: Stmt | null = null;
  if (isKw("else")) { next(); e = isKw("if") ? parseIf() : new Block(parseBlockBody()); }
  return new If(c, t, e);
}
function parseStmt(): Stmt {
  const t = peek();
  if (t.kind === T_KW) {
    switch (t.text) {
      case "let": { next(); const id = next(); expect("="); const e = parseExpr(); expect(";"); return new Let(intern(id.text), e); }
      case "fn":
        if (toks[pos + 1].kind === T_ID) { next(); const id = next(); return new FnDecl(intern(id.text), parseFnRest()); }
        break;
      case "if": return parseIf();
      case "while": { next(); const c = parseExpr(); return new While(c, new Block(parseBlockBody())); }
      case "for": { const f = next(); const id = next(); expect("in"); const e = parseExpr(); return new For(intern(id.text), e, new Block(parseBlockBody()), f.line); }
      case "return": { next(); let e: Expr | null = null; if (!isOp(";")) e = parseExpr(); expect(";"); return new Return(e); }
      case "break": next(); expect(";"); return new Break();
      case "continue": next(); expect(";"); return new Continue();
    }
  }
  const e = parseExpr();
  if (isOp("=")) {
    next();
    const v = parseExpr(); expect(";");
    if (e instanceof Var) return new Assign(e.id, e.line, v);
    const ix = e as Index;
    return new IndexAssign(ix.obj, ix.idx, v, ix.line);
  }
  expect(";");
  return new ExprStmt(e);
}
function parseExpr(): Expr { let l = parseAnd(); while (isKw("or")) { next(); l = new Or(l, parseAnd()); } return l; }
function parseAnd(): Expr { let l = parseNot(); while (isKw("and")) { next(); l = new And(l, parseNot()); } return l; }
function parseNot(): Expr { if (isKw("not")) { next(); return new Not(parseNot()); } return parseCmp(); }
function opIndex(t: Tok): number { return t.kind === T_OP ? OPS.indexOf(t.text) : -1; }
function parseCmp(): Expr {
  let l = parseAdd();
  for (;;) { const t = peek(); const o = opIndex(t); if (o < OP_EQ) break; next(); l = new Bin(o, l, parseAdd(), t.line); }
  return l;
}
function parseAdd(): Expr {
  let l = parseMul();
  for (;;) { const t = peek(); const o = opIndex(t); if (o !== OP_ADD && o !== OP_SUB) break; next(); l = new Bin(o, l, parseMul(), t.line); }
  return l;
}
function parseMul(): Expr {
  let l = parseUnary();
  for (;;) { const t = peek(); const o = opIndex(t); if (o !== OP_MUL && o !== OP_DIV && o !== OP_MOD) break; next(); l = new Bin(o, l, parseUnary(), t.line); }
  return l;
}
function parseUnary(): Expr { if (isOp("-")) { const t = next(); return new Neg(parseUnary(), t.line); } return parsePostfix(); }
function parseList(close: string): Expr[] {
  const a: Expr[] = [];
  if (!isOp(close)) { a.push(parseExpr()); while (isOp(",")) { next(); a.push(parseExpr()); } }
  expect(close);
  return a;
}
function parsePostfix(): Expr {
  let e = parsePrimary();
  for (;;) {
    if (isOp("(")) { const t = next(); e = new Call(e, parseList(")"), t.line); }
    else if (isOp("[")) { const t = next(); const i = parseExpr(); expect("]"); e = new Index(e, i, t.line); }
    else break;
  }
  return e;
}
function parsePrimary(): Expr {
  const t = next();
  switch (t.kind) {
    case T_INT: return new Lit(t.ival!);
    case T_STR: return new Lit(t.text);
    case T_ID: return new Var(intern(t.text), t.line);
    case T_KW:
      if (t.text === "true") return new Lit(true);
      if (t.text === "false") return new Lit(false);
      if (t.text === "nil") return new Lit(null);
      if (t.text === "fn") return new FnExpr(parseFnRest());
      break;
    case T_OP:
      if (t.text === "(") { const e = parseExpr(); expect(")"); return e; }
      if (t.text === "[") return new ListLit(parseList("]"));
  }
  throw new Error("parse error line " + t.line + " at " + t.text);
}

// ---------- runtime helpers ----------
function truthy(v: Value): boolean {
  if (v === null || v === false) return false;
  if (v === true) return true;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "bigint") return true;
  if (typeof v === "string") return v.length > 0;
  if (v instanceof TList) return v.a.length > 0;
  return true;
}
function typeName(v: Value): string {
  if (v === null) return "nil";
  if (typeof v === "number" || typeof v === "bigint") return "int";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "string") return "string";
  if (v instanceof TList) return "list";
  return "function";
}
function valEq(a: Value, b: Value): boolean {
  if (a === b) return true;
  if (a instanceof TList && b instanceof TList) {
    const x = a.a, y = b.a;
    if (x.length !== y.length) return false;
    for (let i = 0; i < x.length; i++) if (!valEq(x[i], y[i])) return false;
    return true;
  }
  return false;
}
function display(v: Value, parts: string[], inList: boolean, stack: TList[]): void {
  if (typeof v === "string") {
    if (!inList) { parts.push(v); return; }
    let r = '"';
    for (let i = 0; i < v.length; i++) {
      const c = v[i];
      if (c === "\\") r += "\\\\";
      else if (c === '"') r += '\\"';
      else if (c === "\n") r += "\\n";
      else if (c === "\t") r += "\\t";
      else r += c;
    }
    parts.push(r + '"');
  } else if (v instanceof TList) {
    if (stack.indexOf(v) >= 0) { parts.push("[...]"); return; }
    stack.push(v);
    parts.push("[");
    for (let i = 0; i < v.a.length; i++) { if (i > 0) parts.push(", "); display(v.a[i], parts, true, stack); }
    parts.push("]");
    stack.pop();
  } else if (typeof v === "number" || typeof v === "bigint") parts.push(String(v));
  else if (typeof v === "boolean") parts.push(v ? "true" : "false");
  else if (v === null) parts.push("nil");
  else parts.push("<fn>");
}
function disp(v: Value): string { const p: string[] = []; display(v, p, false, []); return p.join(""); }

let outBuf: string[] = [];
let outLen = 0;
function flushOut() { if (outBuf.length) { fs.writeSync(1, outBuf.join("")); outBuf = []; outLen = 0; } }
function emit(s: string) { outBuf.push(s); outLen += s.length; if (outLen > 1 << 16) flushOut(); }

const BUILTINS = ["print", "str", "type", "len", "push", "pop", "range", "slice"];
const ARITY = [-1, 1, 1, 1, 2, 1, 2, 3];

function call(f: Value, av: Value[], line: number): Value {
  if (f instanceof Closure) {
    const ps = f.def.params;
    if (ps.length !== av.length) throw new TErr(line, "expected " + ps.length + " arguments, got " + av.length);
    const sc = new Scope(f.env);
    for (let i = 0; i < ps.length; i++) sc.declare(ps[i], av[i]);
    const body = f.def.body;
    for (let i = 0; i < body.length; i++) {
      if (body[i].exec(sc) === RET) { const v = retVal; retVal = null; return v; }
    }
    return null;
  }
  if (f instanceof Builtin) {
    const ar = ARITY[f.id];
    if (ar >= 0 && av.length !== ar) throw new TErr(line, "expected " + ar + " arguments, got " + av.length);
    switch (f.id) {
      case 0: {
        const parts: string[] = [];
        for (let i = 0; i < av.length; i++) { if (i > 0) parts.push(" "); display(av[i], parts, false, []); }
        parts.push("\n");
        emit(parts.join(""));
        return null;
      }
      case 1: return disp(av[0]);
      case 2: return typeName(av[0]);
      case 3: {
        const x = av[0];
        if (typeof x === "string") return x.length;
        if (x instanceof TList) return x.a.length;
        break;
      }
      case 4: { const x = av[0]; if (x instanceof TList) { x.a.push(av[1]); return null; } break; }
      case 5: { const x = av[0]; if (x instanceof TList && x.a.length > 0) return x.a.pop()!; break; }
      case 6: {
        const a = av[0], b = av[1];
        if (isInt(a) && isInt(b)) {
          const l: Value[] = [];
          if (typeof a === "number" && typeof b === "number") { for (let k = a; k < b; k++) l.push(k); }
          else { for (let k = big(a); k < big(b); k++) l.push(normBig(k, line)); }
          return new TList(l);
        }
        break;
      }
      case 7: {
        const x = av[0], a = av[1], b = av[2];
        if (typeof a === "number" && typeof b === "number") {
          if (typeof x === "string") { if (0 <= a && a <= b && b <= x.length) return x.substring(a, b); }
          else if (x instanceof TList) { if (0 <= a && a <= b && b <= x.a.length) return new TList(x.a.slice(a, b)); }
        }
        break;
      }
    }
    throw new TErr(line, "bad argument to " + f.name);
  }
  throw new TErr(line, "cannot call " + typeName(f));
}

function main() {
  const src = fs.readFileSync(path.join(process.argv[2], "program.tern"), "latin1");
  toks = lex(src); pos = 0;
  const prog: Stmt[] = [];
  while (peek().kind !== T_EOF) prog.push(parseStmt());
  const builtins = new Scope(null);
  for (let i = 0; i < BUILTINS.length; i++) builtins.declare(intern(BUILTINS[i]), new Builtin(BUILTINS[i], i));
  const global = new Scope(builtins);
  try {
    for (const st of prog) st.exec(global);
  } catch (e) {
    if (e instanceof TErr) emit("error at line " + e.line + ": " + e.msg + "\n");
    else { flushOut(); throw e; }
  }
  flushOut();
}

main();
