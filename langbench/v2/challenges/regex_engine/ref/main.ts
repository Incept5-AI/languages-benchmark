import * as fs from "fs";

type Node =
  | { t: "char"; c: number }
  | { t: "any" }
  | { t: "class"; i: number }
  | { t: "bol" }
  | { t: "eol" }
  | { t: "cat"; k: Node[] }
  | { t: "alt"; k: Node[] }
  | { t: "group"; g: number; k: Node }
  | { t: "rep"; k: Node; min: number; max: number; greedy: boolean };

function inset(e: string, c: number): boolean {
  switch (e) {
    case "d": return c >= 48 && c <= 57;
    case "w": return (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95;
    case "s": return c === 32 || c === 9 || c === 10 || c === 13 || c === 12 || c === 11;
    case "D": return !inset("d", c);
    case "W": return !inset("w", c);
    case "S": return !inset("s", c);
  }
  return false;
}
const isClsEsc = (e: string) => e.length === 1 && "dwsDWS".includes(e);

class Parser {
  i = 0;
  ngroups = 0;
  classes: Uint8Array[] = [];
  constructor(public p: string) {}
  peek(k: number): string { return this.i + k < this.p.length ? this.p[this.i + k] : ""; }
  regex(): Node {
    const v: Node[] = [this.branch()];
    while (this.peek(0) === "|") { this.i++; v.push(this.branch()); }
    return { t: "alt", k: v };
  }
  readint(): number {
    let v = 0;
    while (this.peek(0) >= "0" && this.peek(0) <= "9" && this.peek(0) !== "") { v = v * 10 + (this.p.charCodeAt(this.i) - 48); this.i++; }
    return v;
  }
  branch(): Node {
    const v: Node[] = [];
    while (this.i < this.p.length && this.peek(0) !== "|" && this.peek(0) !== ")") {
      let a = this.atom();
      const q = this.peek(0);
      let mn = -1, mx = -1;
      if (q === "*") { mn = 0; this.i++; }
      else if (q === "+") { mn = 1; this.i++; }
      else if (q === "?") { mn = 0; mx = 1; this.i++; }
      else if (q === "{") {
        this.i++; mn = this.readint();
        if (this.peek(0) === "}") { mx = mn; this.i++; }
        else { this.i++; if (this.peek(0) === "}") { mx = -1; this.i++; } else { mx = this.readint(); this.i++; } }
      }
      if (mn >= 0) {
        let greedy = true;
        if (this.peek(0) === "?") { greedy = false; this.i++; }
        a = { t: "rep", k: a, min: mn, max: mx, greedy };
      }
      v.push(a);
    }
    return { t: "cat", k: v };
  }
  atom(): Node {
    const ch = this.peek(0);
    if (ch === "(") {
      this.i++;
      if (this.peek(0) === "?" && this.peek(1) === ":") { this.i += 2; const r = this.regex(); this.i++; return r; }
      const g = ++this.ngroups;
      const r = this.regex(); this.i++;
      return { t: "group", g, k: r };
    }
    if (ch === ".") { this.i++; return { t: "any" }; }
    if (ch === "^") { this.i++; return { t: "bol" }; }
    if (ch === "$") { this.i++; return { t: "eol" }; }
    if (ch === "[") {
      this.i++;
      let neg = false;
      if (this.peek(0) === "^") { neg = true; this.i++; }
      const t = new Uint8Array(128);
      while (this.i < this.p.length && this.peek(0) !== "]") {
        let lo: number;
        if (this.peek(0) === "\\") {
          const e = this.peek(1); this.i += 2;
          if (isClsEsc(e)) { for (let c = 0; c < 128; c++) if (inset(e, c)) t[c] = 1; continue; }
          lo = e.charCodeAt(0);
        } else { lo = this.p.charCodeAt(this.i); this.i++; }
        let hi = lo;
        if (this.peek(0) === "-" && this.peek(1) !== "]") {
          this.i++;
          if (this.peek(0) === "\\") { hi = this.p.charCodeAt(this.i + 1); this.i += 2; }
          else { hi = this.p.charCodeAt(this.i); this.i++; }
        }
        for (let c = lo; c <= hi && c < 128; c++) t[c] = 1;
      }
      this.i++;
      if (neg) for (let c = 0; c < 128; c++) t[c] = t[c] ? 0 : 1;
      this.classes.push(t);
      return { t: "class", i: this.classes.length - 1 };
    }
    if (ch === "\\") {
      const e = this.peek(1); this.i += 2;
      if (isClsEsc(e)) {
        const t = new Uint8Array(128);
        for (let c = 0; c < 128; c++) t[c] = inset(e, c) ? 1 : 0;
        this.classes.push(t);
        return { t: "class", i: this.classes.length - 1 };
      }
      return { t: "char", c: e.charCodeAt(0) };
    }
    this.i++;
    return { t: "char", c: ch.charCodeAt(0) };
  }
}

const CHAR = 0, ANY = 1, CLASS = 2, BOL = 3, EOL = 4, SAVE = 5, SPLIT = 6, JMP = 7, LOOP = 8, ITEREND = 9, MATCH = 10;
let OP: number[] = [], A: number[] = [], B: number[] = [], C: number[] = [];
let maxlevel = 0;
function emit(op: number, a: number, b: number, c: number): number {
  OP.push(op); A.push(a); B.push(b); C.push(c); return OP.length - 1;
}
function compile(n: Node, level: number): void {
  if (level > maxlevel) maxlevel = level;
  switch (n.t) {
    case "char": emit(CHAR, n.c, 0, 0); break;
    case "any": emit(ANY, 0, 0, 0); break;
    case "class": emit(CLASS, n.i, 0, 0); break;
    case "bol": emit(BOL, 0, 0, 0); break;
    case "eol": emit(EOL, 0, 0, 0); break;
    case "cat": for (const k of n.k) compile(k, level); break;
    case "group": emit(SAVE, 2 * n.g, 0, 0); compile(n.k, level); emit(SAVE, 2 * n.g + 1, 0, 0); break;
    case "alt": {
      if (n.k.length === 1) { compile(n.k[0], level); break; }
      const jmps: number[] = [];
      n.k.forEach((k, i) => {
        if (i + 1 < n.k.length) {
          const s = emit(SPLIT, 0, 0, 0);
          A[s] = OP.length;
          compile(k, level);
          jmps.push(emit(JMP, 0, 0, 0));
          B[s] = OP.length;
        } else compile(k, level);
      });
      for (const j of jmps) A[j] = OP.length;
      break;
    }
    case "rep": {
      for (let i = 0; i < n.min; i++) compile(n.k, level);
      if (n.max < 0) {
        const l = emit(LOOP, 0, 0, n.greedy ? 1 : 0);
        A[l] = OP.length;
        compile(n.k, level + 1);
        const e = emit(ITEREND, l, 0, 0);
        B[l] = OP.length; B[e] = OP.length;
      } else if (n.max > n.min) {
        const loops: number[] = [], ends: number[] = [];
        for (let j = 0; j < n.max - n.min; j++) {
          const l = emit(LOOP, 0, 0, n.greedy ? 1 : 0);
          A[l] = OP.length;
          compile(n.k, level + 1);
          const e = emit(ITEREND, 0, 0, 0);
          A[e] = OP.length;
          loops.push(l); ends.push(e);
        }
        for (const l of loops) B[l] = OP.length;
        for (const e of ends) B[e] = OP.length;
      }
      break;
    }
  }
}

const dir = process.argv[2];
const data = fs.readFileSync(dir + "/cases.txt", "latin1");
const lines = data.split("\n");
if (lines.length && lines[lines.length - 1] === "") lines.pop();
const out: string[] = [];
lines.forEach((line, idx) => {
  const lineno = idx + 1;
  const tab = line.indexOf("\t");
  if (tab < 0) return;
  const pat = line.slice(0, tab);
  const s = line.slice(tab + 1);
  const n = s.length;
  const sc = new Uint8Array(n);
  for (let i = 0; i < n; i++) sc[i] = s.charCodeAt(i) & 127;
  const ps = new Parser(pat);
  const root = ps.regex();
  OP = []; A = []; B = []; C = []; maxlevel = 0;
  compile(root, 0);
  emit(MATCH, 0, 0, 0);
  const classes = ps.classes;
  const ng = ps.ngroups;
  const D = maxlevel + 1;
  const memo = new Uint8Array(OP.length * (n + 1) * D);
  const ncap = 2 * (ng + 1);
  const caps = new Array<number>(ncap).fill(-1);
  let found = false, ms = 0, me = 0;
  const logs: number[] = [];
  const stk: number[] = [];
  for (let st = 0; st <= n && !found; st++) {
    caps.fill(-1);
    logs.length = 0; stk.length = 0;
    let pc = 0, pos = st, d = 0;
    for (;;) {
      const key = (pc * (n + 1) + pos) * D + d;
      let ok = true;
      if (memo[key]) ok = false;
      else {
        memo[key] = 1;
        const op = OP[pc];
        switch (op) {
          case CHAR: if (pos < n && sc[pos] === A[pc]) { pos++; d = 0; pc++; } else ok = false; break;
          case ANY: if (pos < n) { pos++; d = 0; pc++; } else ok = false; break;
          case CLASS: if (pos < n && classes[A[pc]][sc[pos]]) { pos++; d = 0; pc++; } else ok = false; break;
          case BOL: if (pos === 0) pc++; else ok = false; break;
          case EOL: if (pos === n) pc++; else ok = false; break;
          case SAVE: logs.push(A[pc], caps[A[pc]]); caps[A[pc]] = pos; pc++; break;
          case JMP: pc = A[pc]; break;
          case SPLIT: stk.push(B[pc], pos, d, logs.length); pc = A[pc]; break;
          case LOOP:
            if (C[pc]) { stk.push(B[pc], pos, d, logs.length); pc = A[pc]; d++; }
            else { stk.push(A[pc], pos, d + 1, logs.length); pc = B[pc]; }
            break;
          case ITEREND: if (d >= 1) { d--; pc = B[pc]; } else pc = A[pc]; break;
          case MATCH: found = true; ms = st; me = pos; break;
        }
      }
      if (found) break;
      if (!ok) {
        if (stk.length === 0) break;
        const lh = stk.pop()!; d = stk.pop()!; pos = stk.pop()!; pc = stk.pop()!;
        while (logs.length > lh) { const old = logs.pop()!; const slot = logs.pop()!; caps[slot] = old; }
      }
    }
  }
  if (!found) out.push(`${lineno}: no match`);
  else {
    let r = `${lineno}: ${ms}-${me}`;
    for (let g = 1; g <= ng; g++) {
      if (caps[2 * g] < 0 || caps[2 * g + 1] < 0) r += " -";
      else r += ` ${caps[2 * g]}-${caps[2 * g + 1]}`;
    }
    out.push(r);
  }
});
process.stdout.write(out.length ? out.join("\n") + "\n" : "");
