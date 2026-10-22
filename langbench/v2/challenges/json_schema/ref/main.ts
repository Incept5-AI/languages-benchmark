import * as fs from "fs";
import * as path from "path";

type J = null | boolean | number | string | J[] | Map<string, J>;

function conv(v: any): J {
  if (v === null || typeof v !== "object") return v;
  if (Array.isArray(v)) return v.map(conv);
  const m = new Map<string, J>();
  for (const k of Object.keys(v)) m.set(k, conv(v[k]));
  return m;
}

function typeOf(v: J): string {
  if (v === null) return "null";
  if (typeof v === "boolean") return "boolean";
  if (typeof v === "number") return "number";
  if (typeof v === "string") return "string";
  if (Array.isArray(v)) return "array";
  return "object";
}

function deepEq(a: J, b: J): boolean {
  const ta = typeOf(a), tb = typeOf(b);
  if (ta !== tb) return false;
  if (ta === "array") {
    const x = a as J[], y = b as J[];
    if (x.length !== y.length) return false;
    for (let i = 0; i < x.length; i++) if (!deepEq(x[i], y[i])) return false;
    return true;
  }
  if (ta === "object") {
    const x = a as Map<string, J>, y = b as Map<string, J>;
    if (x.size !== y.size) return false;
    for (const [k, v] of x) {
      if (!y.has(k)) return false;
      if (!deepEq(v, y.get(k)!)) return false;
    }
    return true;
  }
  return a === b; // numbers: -0 === 0 true
}

const esc = (t: string) => t.replace(/~/g, "~0").replace(/\//g, "~1");
const unesc = (t: string) => t.replace(/~1/g, "/").replace(/~0/g, "~");
const cpLen = (s: string) => { let n = 0; for (const _ of s) n++; return n; };

let root: J;

function resolve(ref: string): [J, string] {
  if (ref === "#") return [root, "#"];
  const name = unesc(ref.substring("#/$defs/".length));
  const defs = (root as Map<string, J>).get("$defs") as Map<string, J>;
  return [defs.get(name)!, "#/$defs/" + esc(name)];
}

function accepts(inst: J, L: string, s: J, P: string): boolean {
  const e = new Map<string, [string, string]>();
  validate(inst, L, s, P, e);
  return e.size === 0;
}

function validate(inst: J, L: string, s: J, P: string, out: Map<string, [string, string]>): void {
  const add = (l: string, p: string) => out.set(l + "\u0000" + p, [l, p]);
  if (typeof s === "boolean") {
    if (!s) add(L, P);
    return;
  }
  const m = s as Map<string, J>;
  const it = typeOf(inst);
  if (m.has("type")) {
    const t = m.get("type")!;
    const ts = Array.isArray(t) ? t : [t];
    let ok = false;
    for (const n of ts) {
      if (n === it) ok = true;
      else if (n === "integer" && it === "number" && Number.isInteger(inst as number)) ok = true;
    }
    if (!ok) add(L, P + "/type");
  }
  if (m.has("enum")) {
    if (!(m.get("enum") as J[]).some((o) => deepEq(inst, o))) add(L, P + "/enum");
  }
  if (m.has("const")) {
    if (!deepEq(inst, m.get("const")!)) add(L, P + "/const");
  }
  if (it === "number") {
    const d = inst as number;
    if (m.has("minimum") && !(d >= (m.get("minimum") as number))) add(L, P + "/minimum");
    if (m.has("maximum") && !(d <= (m.get("maximum") as number))) add(L, P + "/maximum");
    if (m.has("exclusiveMinimum") && !(d > (m.get("exclusiveMinimum") as number))) add(L, P + "/exclusiveMinimum");
    if (m.has("exclusiveMaximum") && !(d < (m.get("exclusiveMaximum") as number))) add(L, P + "/exclusiveMaximum");
  }
  if (it === "string") {
    const len = cpLen(inst as string);
    if (m.has("minLength") && !(len >= (m.get("minLength") as number))) add(L, P + "/minLength");
    if (m.has("maxLength") && !(len <= (m.get("maxLength") as number))) add(L, P + "/maxLength");
  }
  if (it === "array") {
    const a = inst as J[];
    const n = a.length;
    if (m.has("minItems") && !(n >= (m.get("minItems") as number))) add(L, P + "/minItems");
    if (m.has("maxItems") && !(n <= (m.get("maxItems") as number))) add(L, P + "/maxItems");
    if (m.get("uniqueItems") === true) {
      let dup = false;
      outer: for (let x = 0; x < n; x++) for (let y = x + 1; y < n; y++) if (deepEq(a[x], a[y])) { dup = true; break outer; }
      if (dup) add(L, P + "/uniqueItems");
    }
    let pl = 0;
    if (m.has("prefixItems")) {
      const pi = m.get("prefixItems") as J[];
      pl = pi.length;
      for (let k = 0; k < Math.min(pl, n); k++) validate(a[k], L + "/" + k, pi[k], P + "/prefixItems/" + k, out);
    }
    if (m.has("items")) {
      for (let k = pl; k < n; k++) validate(a[k], L + "/" + k, m.get("items")!, P + "/items", out);
    }
  }
  if (it === "object") {
    const o = inst as Map<string, J>;
    const props = m.has("properties") ? (m.get("properties") as Map<string, J>) : new Map<string, J>();
    for (const [k, v] of o) {
      if (props.has(k)) validate(v, L + "/" + esc(k), props.get(k)!, P + "/properties/" + esc(k), out);
    }
    if (m.has("required")) {
      const r = m.get("required") as string[];
      r.forEach((name, j) => { if (!o.has(name)) add(L, P + "/required/" + j); });
    }
    if (m.has("additionalProperties")) {
      for (const [k, v] of o) {
        if (!props.has(k)) validate(v, L + "/" + esc(k), m.get("additionalProperties")!, P + "/additionalProperties", out);
      }
    }
    const n = o.size;
    if (m.has("minProperties") && !(n >= (m.get("minProperties") as number))) add(L, P + "/minProperties");
    if (m.has("maxProperties") && !(n <= (m.get("maxProperties") as number))) add(L, P + "/maxProperties");
  }
  if (m.has("allOf")) {
    (m.get("allOf") as J[]).forEach((sub, k) => validate(inst, L, sub, P + "/allOf/" + k, out));
  }
  if (m.has("anyOf")) {
    const l = m.get("anyOf") as J[];
    let ok = false;
    for (let k = 0; k < l.length; k++) if (accepts(inst, L, l[k], P + "/anyOf/" + k)) { ok = true; break; }
    if (!ok) add(L, P + "/anyOf");
  }
  if (m.has("oneOf")) {
    const l = m.get("oneOf") as J[];
    let c = 0;
    for (let k = 0; k < l.length; k++) if (accepts(inst, L, l[k], P + "/oneOf/" + k)) c++;
    if (c !== 1) add(L, P + "/oneOf");
  }
  if (m.has("not")) {
    if (accepts(inst, L, m.get("not")!, P + "/not")) add(L, P + "/not");
  }
  if (m.has("if")) {
    if (accepts(inst, L, m.get("if")!, P + "/if")) {
      if (m.has("then")) validate(inst, L, m.get("then")!, P + "/then", out);
    } else {
      if (m.has("else")) validate(inst, L, m.get("else")!, P + "/else", out);
    }
  }
  if (m.has("$ref")) {
    const [rs, rp] = resolve(m.get("$ref") as string);
    validate(inst, L, rs, rp, out);
  }
}

function cmp(a: string, b: string): number {
  return Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

const dir = process.argv[2];
root = conv(JSON.parse(fs.readFileSync(path.join(dir, "schema.json"), "utf8")));
const insts = conv(JSON.parse(fs.readFileSync(path.join(dir, "instances.json"), "utf8"))) as J[];
const lines: string[] = [];
let valid = 0, invalid = 0;
insts.forEach((inst, n) => {
  const errs = new Map<string, [string, string]>();
  validate(inst, "#", root, "#", errs);
  if (errs.size === 0) { valid++; lines.push(`${n}: valid`); return; }
  invalid++;
  const l = [...errs.values()];
  l.sort((a, b) => cmp(a[0], b[0]) || cmp(a[1], b[1]));
  lines.push(`${n}: invalid (${l.length})`);
  for (const [x, y] of l) lines.push(`  ${x} ${y}`);
});
lines.push(`valid=${valid} invalid=${invalid}`);
process.stdout.write(lines.join("\n") + "\n");
