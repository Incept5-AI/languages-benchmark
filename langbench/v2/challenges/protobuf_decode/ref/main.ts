import * as fs from "fs";
import * as path from "path";

type Label = "single" | "optional" | "repeated" | "map";
interface FieldDesc {
  name: string;
  json: string;
  num: number;
  type: string; // scalar keyword or type name
  label: Label;
  oneof: number; // -1 if none
  keyType?: string;
  valType?: string;
  entry?: MsgDesc;
}
interface MsgDesc {
  name: string;
  fields: FieldDesc[];
  byNum: Map<number, FieldDesc>;
  oneofCount: number;
}
interface EnumDesc {
  name: string;
  byNum: Map<number, string>;
}

const SCALARS = new Set([
  "int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool", "string", "bytes",
  "fixed32", "fixed64", "sfixed32", "sfixed64",
]);

const messages = new Map<string, MsgDesc>();
const enums = new Map<string, EnumDesc>();
let rootName = "";

// ---------- tokenizer ----------
function tokenize(src: string): string[] {
  const toks: string[] = [];
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === " " || c === "\t" || c === "\n" || c === "\r" || c === "\f" || c === "\v") { i++; continue; }
    if (c === "/" && src[i + 1] === "/") {
      while (i < n && src[i] !== "\n") i++;
      continue;
    }
    if (c === "/" && src[i + 1] === "*") {
      const e = src.indexOf("*/", i + 2);
      i = e < 0 ? n : e + 2;
      continue;
    }
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < n && src[j] !== c) { if (src[j] === "\\") j++; j++; }
      toks.push(src.slice(i, j + 1));
      i = j + 1;
      continue;
    }
    if (/[A-Za-z0-9_.+\-]/.test(c)) {
      let j = i;
      while (j < n && /[A-Za-z0-9_.+\-]/.test(src[j])) j++;
      toks.push(src.slice(i, j));
      i = j;
      continue;
    }
    toks.push(c);
    i++;
  }
  return toks;
}

let toks: string[] = [];
let pos = 0;
function peek(): string { return pos < toks.length ? toks[pos] : ""; }
function next(): string { return pos < toks.length ? toks[pos++] : ""; }
function expect(t: string) { const x = next(); if (x !== t) throw new Error(`expected ${t} got ${x}`); }

function skipStatement() {
  // skip to ';' at depth 0 (tracking braces/brackets)
  let depth = 0;
  while (pos < toks.length) {
    const t = next();
    if (t === "{" || t === "[" || t === "(") depth++;
    else if (t === "}" || t === "]" || t === ")") depth--;
    else if (t === ";" && depth <= 0) return;
  }
}

function skipFieldOptions() {
  if (peek() === "[") {
    let depth = 0;
    while (pos < toks.length) {
      const t = next();
      if (t === "[") depth++;
      else if (t === "]") { depth--; if (depth === 0) break; }
    }
  }
}

function parseInt2(s: string): number {
  let neg = false;
  if (s.startsWith("-")) { neg = true; s = s.slice(1); } else if (s.startsWith("+")) s = s.slice(1);
  let v: number;
  if (/^0[xX]/.test(s)) v = parseInt(s.slice(2), 16);
  else if (/^0[0-7]+$/.test(s)) v = parseInt(s, 8);
  else v = parseInt(s, 10);
  return neg ? -v : v;
}

function camel(name: string): string {
  let out = "";
  let up = false;
  for (const ch of name) {
    if (ch === "_") { up = true; continue; }
    if (up && ch >= "a" && ch <= "z") out += ch.toUpperCase();
    else out += ch;
    up = false;
  }
  return out;
}

function parseEnum() {
  const name = next();
  const ed: EnumDesc = { name, byNum: new Map() };
  enums.set(name, ed);
  expect("{");
  while (pos < toks.length && peek() !== "}") {
    const t = peek();
    if (t === ";") { next(); continue; }
    if (t === "option" || t === "reserved") { skipStatement(); continue; }
    const vname = next();
    expect("=");
    const num = parseInt2(next());
    skipFieldOptions();
    expect(";");
    if (!ed.byNum.has(num)) ed.byNum.set(num, vname);
  }
  expect("}");
}

function parseField(md: MsgDesc, oneof: number) {
  let label: Label = "single";
  let t = next();
  if (t === "repeated") { label = "repeated"; t = next(); }
  else if (t === "optional") { label = "optional"; t = next(); }
  if (t === "map" && peek() === "<") {
    next();
    const k = next();
    expect(",");
    const v = next();
    expect(">");
    const name = next();
    expect("=");
    const num = parseInt2(next());
    skipFieldOptions();
    expect(";");
    const entry: MsgDesc = { name: "", fields: [], byNum: new Map(), oneofCount: 0 };
    const kf: FieldDesc = { name: "key", json: "key", num: 1, type: k, label: "single", oneof: -1 };
    const vf: FieldDesc = { name: "value", json: "value", num: 2, type: v, label: "single", oneof: -1 };
    entry.fields.push(kf, vf);
    entry.byNum.set(1, kf);
    entry.byNum.set(2, vf);
    const f: FieldDesc = { name, json: camel(name), num, type: v, label: "map", oneof, keyType: k, valType: v, entry };
    md.fields.push(f);
    md.byNum.set(num, f);
    return;
  }
  const name = next();
  expect("=");
  const num = parseInt2(next());
  skipFieldOptions();
  expect(";");
  const f: FieldDesc = { name, json: camel(name), num, type: t, label, oneof };
  md.fields.push(f);
  md.byNum.set(num, f);
}

function parseMessage(): MsgDesc {
  const name = next();
  const md: MsgDesc = { name, fields: [], byNum: new Map(), oneofCount: 0 };
  messages.set(name, md);
  expect("{");
  while (pos < toks.length && peek() !== "}") {
    const t = peek();
    if (t === ";") { next(); continue; }
    if (t === "message") { next(); parseMessage(); continue; }
    if (t === "enum") { next(); parseEnum(); continue; }
    if (t === "option" || t === "reserved" || t === "extensions") { skipStatement(); continue; }
    if (t === "oneof") {
      next();
      next(); // name
      const idx = md.oneofCount++;
      expect("{");
      while (pos < toks.length && peek() !== "}") {
        const u = peek();
        if (u === ";") { next(); continue; }
        if (u === "option") { skipStatement(); continue; }
        parseField(md, idx);
      }
      expect("}");
      continue;
    }
    parseField(md, -1);
  }
  expect("}");
  md.fields.sort((a, b) => a.num - b.num);
  return md;
}

function parseSchema(src: string) {
  toks = tokenize(src);
  pos = 0;
  while (pos < toks.length) {
    const t = peek();
    if (t === "message") {
      next();
      const md = parseMessage();
      if (rootName === "") rootName = md.name;
    } else if (t === "enum") {
      next();
      parseEnum();
    } else if (t === ";") {
      next();
    } else {
      skipStatement();
    }
  }
}

// ---------- decoding ----------
interface MsgVal {
  fields: Map<number, any>;
  oneofSet: number[]; // per oneof index, current field num or 0
}
interface MapVal {
  entries: Map<string | number, any>; // key -> value
}

function newMsg(md: MsgDesc): MsgVal {
  return { fields: new Map(), oneofSet: new Array(md.oneofCount).fill(0) };
}

class Reader {
  constructor(public buf: Uint8Array, public pos: number, public end: number) {}
  varint(): bigint {
    let result = 0n;
    let shift = 0n;
    let count = 0;
    while (true) {
      if (this.pos >= this.end) throw new Error("truncated varint");
      const b = this.buf[this.pos++];
      if (count < 10) result |= BigInt(b & 0x7f) << shift;
      shift += 7n;
      count++;
      if ((b & 0x80) === 0) break;
    }
    return BigInt.asUintN(64, result);
  }
  varintNum(): number {
    // for lengths / keys
    let result = 0;
    let mul = 1;
    while (true) {
      if (this.pos >= this.end) throw new Error("truncated varint");
      const b = this.buf[this.pos++];
      result += (b & 0x7f) * mul;
      mul *= 128;
      if ((b & 0x80) === 0) break;
    }
    return result;
  }
  fixed32(): bigint {
    if (this.pos + 4 > this.end) throw new Error("truncated");
    const b = this.buf, p = this.pos;
    const v = (b[p] | (b[p + 1] << 8) | (b[p + 2] << 16) | (b[p + 3] << 24)) >>> 0;
    this.pos += 4;
    return BigInt(v);
  }
  fixed64(): bigint {
    const lo = this.fixed32();
    const hi = this.fixed32();
    return (hi << 32n) | lo;
  }
}

function skipField(r: Reader, wt: number, fnum: number) {
  switch (wt) {
    case 0: r.varint(); break;
    case 1: r.pos += 8; break;
    case 5: r.pos += 4; break;
    case 2: { const l = r.varintNum(); r.pos += l; break; }
    case 3: {
      while (true) {
        const key = r.varintNum();
        const w = key % 8;
        const n = Math.floor(key / 8);
        if (w === 4) { if (n !== fnum) throw new Error("group mismatch"); break; }
        skipField(r, w, n);
      }
      break;
    }
    case 4: break;
    default: throw new Error("bad wire type " + wt);
  }
  if (r.pos > r.end) throw new Error("truncated");
}

function wireTypeOf(type: string): number {
  switch (type) {
    case "int32": case "int64": case "uint32": case "uint64": case "sint32": case "sint64": case "bool":
      return 0;
    case "fixed32": case "sfixed32": return 5;
    case "fixed64": case "sfixed64": return 1;
    case "string": case "bytes": return 2;
  }
  if (enums.has(type)) return 0;
  return 2; // message
}

function isPackable(type: string): boolean {
  return wireTypeOf(type) !== 2;
}

function readScalar(r: Reader, wt: number): any {
  if (wt === 0) return r.varint();
  if (wt === 5) return r.fixed32();
  if (wt === 1) return r.fixed64();
  const l = r.varintNum();
  if (r.pos + l > r.end) throw new Error("truncated");
  const v = r.buf.subarray(r.pos, r.pos + l);
  r.pos += l;
  return v;
}

function readValue(r: Reader, type: string, wt: number, existing: any): any {
  const md = messages.get(type);
  if (md) {
    const l = r.varintNum();
    if (r.pos + l > r.end) throw new Error("truncated");
    const m: MsgVal = existing ?? newMsg(md);
    decodeInto(md, m, r.buf, r.pos, r.pos + l);
    r.pos += l;
    return m;
  }
  return readScalar(r, wt);
}

function defaultValue(type: string): any {
  const md = messages.get(type);
  if (md) return newMsg(md);
  if (type === "string" || type === "bytes") return new Uint8Array(0);
  return 0n;
}

function mapKeyOf(type: string, raw: any): string | number {
  if (type === "string") return Buffer.from(raw).toString("latin1");
  // int32 (or other integer types)
  return Number(convInt(type, raw));
}

function decodeInto(md: MsgDesc, msg: MsgVal, buf: Uint8Array, start: number, end: number) {
  const r = new Reader(buf, start, end);
  while (r.pos < r.end) {
    const key = r.varintNum();
    const wt = key % 8;
    const fnum = Math.floor(key / 8);
    const f = md.byNum.get(fnum);
    if (!f) { skipField(r, wt, fnum); continue; }
    if (f.label === "map") {
      if (wt !== 2) { skipField(r, wt, fnum); continue; }
      const l = r.varintNum();
      if (r.pos + l > r.end) throw new Error("truncated");
      const em = newMsg(f.entry!);
      decodeInto(f.entry!, em, buf, r.pos, r.pos + l);
      r.pos += l;
      const kraw = em.fields.has(1) ? em.fields.get(1) : defaultValue(f.keyType!);
      const vraw = em.fields.has(2) ? em.fields.get(2) : defaultValue(f.valType!);
      let mv: MapVal = msg.fields.get(fnum);
      if (!mv) { mv = { entries: new Map() }; msg.fields.set(fnum, mv); }
      mv.entries.set(mapKeyOf(f.keyType!, kraw), vraw);
      continue;
    }
    const ewt = wireTypeOf(f.type);
    if (f.label === "repeated") {
      let arr: any[] = msg.fields.get(fnum);
      if (wt === 2 && isPackable(f.type)) {
        if (!arr) { arr = []; msg.fields.set(fnum, arr); }
        const l = r.varintNum();
        if (r.pos + l > r.end) throw new Error("truncated");
        const sub = new Reader(buf, r.pos, r.pos + l);
        while (sub.pos < sub.end) arr.push(readScalar(sub, ewt));
        r.pos += l;
        continue;
      }
      if (wt !== ewt) { skipField(r, wt, fnum); continue; }
      if (!arr) { arr = []; msg.fields.set(fnum, arr); }
      arr.push(readValue(r, f.type, wt, undefined));
      continue;
    }
    if (wt !== ewt) { skipField(r, wt, fnum); continue; }
    if (f.oneof >= 0) {
      const cur = msg.oneofSet[f.oneof];
      if (cur !== 0 && cur !== fnum) msg.fields.delete(cur);
      msg.oneofSet[f.oneof] = fnum;
    }
    const existing = msg.fields.get(fnum);
    const isMsg = messages.has(f.type);
    msg.fields.set(fnum, readValue(r, f.type, wt, isMsg ? existing : undefined));
  }
}

// ---------- output ----------
function convInt(type: string, v: bigint): bigint {
  switch (type) {
    case "int32": case "sfixed32": return BigInt.asIntN(32, v);
    case "uint32": case "fixed32": return BigInt.asUintN(32, v);
    case "sint32": {
      const u = BigInt.asUintN(32, v);
      return BigInt.asIntN(32, (u >> 1n) ^ -(u & 1n));
    }
    case "int64": case "sfixed64": return BigInt.asIntN(64, v);
    case "uint64": case "fixed64": return BigInt.asUintN(64, v);
    case "sint64": {
      const u = BigInt.asUintN(64, v);
      return BigInt.asIntN(64, (u >> 1n) ^ -(u & 1n));
    }
  }
  // enum
  return BigInt.asIntN(32, v);
}

function escapeStr(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += "\\\\";
    else if (c < 0x20) {
      if (c === 8) out += "\\b";
      else if (c === 9) out += "\\t";
      else if (c === 10) out += "\\n";
      else if (c === 12) out += "\\f";
      else if (c === 13) out += "\\r";
      else out += "\\u00" + c.toString(16).padStart(2, "0");
    } else out += s[i];
  }
  return out + '"';
}

function isDefault(type: string, v: any): boolean {
  if (type === "string" || type === "bytes") return (v as Uint8Array).length === 0;
  if (type === "bool") return v === 0n;
  return convInt(type, v) === 0n;
}

function fmtValue(type: string, v: any): string {
  const md = messages.get(type);
  if (md) return fmtMsg(md, v);
  switch (type) {
    case "string": return escapeStr(Buffer.from(v).toString("utf8"));
    case "bytes": return '"' + Buffer.from(v).toString("base64") + '"';
    case "bool": return v !== 0n ? "true" : "false";
    case "int32": case "uint32": case "sint32": case "fixed32": case "sfixed32":
      return convInt(type, v).toString();
    case "int64": case "uint64": case "sint64": case "fixed64": case "sfixed64":
      return '"' + convInt(type, v).toString() + '"';
  }
  const ed = enums.get(type);
  const n = Number(convInt(type, v));
  if (ed && ed.byNum.has(n)) return escapeStr(ed.byNum.get(n)!);
  return n.toString();
}

function fmtMsg(md: MsgDesc, msg: MsgVal): string {
  const parts: string[] = [];
  for (const f of md.fields) {
    if (!msg.fields.has(f.num)) continue;
    const v = msg.fields.get(f.num);
    const name = escapeStr(f.json) + ":";
    if (f.label === "map") {
      const mv = v as MapVal;
      if (mv.entries.size === 0) continue;
      const keys = [...mv.entries.keys()];
      if (f.keyType === "string") keys.sort((a, b) => ((a as string) < (b as string) ? -1 : (a as string) > (b as string) ? 1 : 0));
      else keys.sort((a, b) => (a as number) - (b as number));
      const items = keys.map((k) => {
        const ks = f.keyType === "string" ? escapeStr(Buffer.from(k as string, "latin1").toString("utf8")) : '"' + String(k) + '"';
        return ks + ":" + fmtValue(f.valType!, mv.entries.get(k));
      });
      parts.push(name + "{" + items.join(",") + "}");
    } else if (f.label === "repeated") {
      const arr = v as any[];
      if (arr.length === 0) continue;
      parts.push(name + "[" + arr.map((e) => fmtValue(f.type, e)).join(",") + "]");
    } else {
      const isMsg = messages.has(f.type);
      if (!isMsg && f.label !== "optional" && f.oneof < 0 && isDefault(f.type, v)) continue;
      parts.push(name + fmtValue(f.type, v));
    }
  }
  return "{" + parts.join(",") + "}";
}

function main() {
  const dir = process.argv[2] ?? ".";
  parseSchema(fs.readFileSync(path.join(dir, "schema.proto"), "utf8"));
  const data = new Uint8Array(fs.readFileSync(path.join(dir, "messages.bin")));
  const root = messages.get(rootName)!;
  const r = new Reader(data, 0, data.length);
  const out: string[] = [];
  while (r.pos < r.end) {
    const l = r.varintNum();
    const m = newMsg(root);
    decodeInto(root, m, data, r.pos, r.pos + l);
    r.pos += l;
    out.push(fmtMsg(root, m));
  }
  process.stdout.write(out.length ? out.join("\n") + "\n" : "");
}

main();
