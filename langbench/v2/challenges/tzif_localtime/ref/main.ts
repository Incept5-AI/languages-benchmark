import * as fs from "fs";
import * as path from "path";

interface TType { off: number; abbr: string; }
interface Rule { m: number; w: number; d: number; t: number; }
interface Zone {
  times: number[];
  idx: number[];
  types: TType[];
  hasFooter: boolean;
  fstd: TType;
  fdst: TType | null;
  start: Rule;
  end: Rule;
}

function fdiv(a: number, b: number): number { return Math.floor(a / b); }
function fmod(a: number, b: number): number { return a - Math.floor(a / b) * b; }

function daysFromCivil(y: number, m: number, d: number): number {
  if (m <= 2) y -= 1;
  const era = fdiv(y, 400);
  const yoe = y - era * 400;
  const mp = m > 2 ? m - 3 : m + 9;
  const doy = Math.floor((153 * mp + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}
function civilFromDays(z: number): [number, number, number] {
  z += 719468;
  const era = fdiv(z, 146097);
  const doe = z - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp < 10 ? mp + 3 : mp - 9;
  return [m <= 2 ? y + 1 : y, m, d];
}
function monthLen(y: number, m: number): number {
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  if (m === 2) return leap ? 29 : 28;
  return [4, 6, 9, 11].includes(m) ? 30 : 31;
}
function ruleLocal(y: number, r: Rule): number {
  const first = daysFromCivil(y, r.m, 1);
  const dow = fmod(first + 4, 7);
  let day = 1 + ((r.d - dow + 7) % 7) + (r.w - 1) * 7;
  const ml = monthLen(y, r.m);
  while (day > ml) day -= 7;
  return (first + day - 1) * 86400 + r.t;
}
function yearOf(t: number): number { return civilFromDays(fdiv(t, 86400))[0]; }

class P {
  i = 0;
  constructor(public s: string) {}
  peek(): string { return this.i < this.s.length ? this.s[this.i] : ""; }
  name(): string {
    let out = "";
    if (this.peek() === "<") {
      this.i++;
      while (this.peek() !== "" && this.peek() !== ">") out += this.s[this.i++];
      if (this.peek() === ">") this.i++;
    } else {
      while (/[A-Za-z]/.test(this.peek())) out += this.s[this.i++];
    }
    return out;
  }
  num(): number {
    let v = 0;
    while (/[0-9]/.test(this.peek())) v = v * 10 + Number(this.s[this.i++]);
    return v;
  }
  hms(): number {
    let sign = 1;
    if (this.peek() === "+") this.i++;
    else if (this.peek() === "-") { sign = -1; this.i++; }
    const h = this.num();
    let m = 0, s = 0;
    if (this.peek() === ":") {
      this.i++; m = this.num();
      if (this.peek() === ":") { this.i++; s = this.num(); }
    }
    return sign * (h * 3600 + m * 60 + s);
  }
  rule(r: Rule): void {
    r.t = 7200;
    if (this.peek() === "M") {
      this.i++;
      r.m = this.num(); if (this.peek() === ".") this.i++;
      r.w = this.num(); if (this.peek() === ".") this.i++;
      r.d = this.num();
    }
    if (this.peek() === "/") { this.i++; r.t = this.hms(); }
  }
}

function loadZone(file: string): Zone | null {
  let b: Buffer;
  try { b = fs.readFileSync(file); } catch { return null; }
  const cnt = (p: number) => [0, 1, 2, 3, 4, 5].map((i) => b.readInt32BE(p + 20 + 4 * i));
  let c = cnt(0);
  const v1 = c[3] * 5 + c[4] * 6 + c[5] + c[2] * 8 + c[1] + c[0];
  let p = 44 + v1;
  c = cnt(p);
  const [isut, isstd, leap, tc, ty, ch] = c;
  p += 44;
  const times: number[] = [];
  for (let i = 0; i < tc; i++) times.push(Number(b.readBigInt64BE(p + 8 * i)));
  p += 8 * tc;
  const idx: number[] = [];
  for (let i = 0; i < tc; i++) idx.push(b[p + i]);
  p += tc;
  const tp = p;
  p += 6 * ty;
  const cp = p;
  const types: TType[] = [];
  for (let i = 0; i < ty; i++) {
    const off = b.readInt32BE(tp + 6 * i);
    const ai = b[tp + 6 * i + 5];
    let e = ai;
    while (e < ch && b[cp + e] !== 0) e++;
    types.push({ off, abbr: b.subarray(cp + Math.min(ai, ch), cp + e).toString("latin1") });
  }
  p += ch + leap * 12 + isstd + isut;
  let foot = "";
  if (p < b.length && b[p] === 10) {
    p++;
    let e = p;
    while (e < b.length && b[e] !== 10) e++;
    foot = b.subarray(p, e).toString("latin1");
  }
  const z: Zone = {
    times, idx, types,
    hasFooter: foot.length > 0,
    fstd: { off: 0, abbr: "" },
    fdst: null,
    start: { m: 3, w: 2, d: 0, t: 7200 },
    end: { m: 11, w: 1, d: 0, t: 7200 },
  };
  if (z.hasFooter) {
    const ps = new P(foot);
    const n = ps.name();
    const o = ps.hms();
    z.fstd = { off: -o, abbr: n };
    if (ps.peek() !== "" && ps.peek() !== ",") {
      const dn = ps.name();
      let doff: number;
      if (ps.peek() !== "" && ps.peek() !== ",") doff = -ps.hms();
      else doff = z.fstd.off + 3600;
      z.fdst = { off: doff, abbr: dn };
      if (ps.peek() === ",") {
        ps.i++;
        ps.rule(z.start);
        if (ps.peek() === ",") { ps.i++; ps.rule(z.end); }
      }
    }
  }
  return z;
}

function events(z: Zone, y: number): [number, number] {
  return [ruleLocal(y, z.start) - z.fstd.off, ruleLocal(y, z.end) - z.fdst!.off];
}

function footerType(z: Zone, t: number): TType {
  if (!z.fdst) return z.fstd;
  const Y = yearOf(t);
  let best: number | null = null;
  let bestDst = false;
  for (let y = Y - 2; y <= Y + 2; y++) {
    const [st, en] = events(z, y);
    if (st <= t && (best === null || st > best)) { best = st; bestDst = true; }
    if (en <= t && (best === null || en > best)) { best = en; bestDst = false; }
  }
  return bestDst ? z.fdst : z.fstd;
}

function typeAt(z: Zone, t: number): TType {
  const n = z.times.length;
  if (n === 0) return z.hasFooter ? footerType(z, t) : z.types[0];
  if (t < z.times[0]) return z.types[0];
  if (t > z.times[n - 1]) return z.hasFooter ? footerType(z, t) : z.types[z.idx[n - 1]];
  let lo = 0, hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (z.times[mid] <= t) lo = mid; else hi = mid - 1;
  }
  return z.types[z.idx[lo]];
}

function p2(n: number): string { return String(n).padStart(2, "0"); }
function fmtOff(off: number): string {
  const sign = off < 0 ? "-" : "+";
  const a = Math.abs(off);
  let r = sign + p2(Math.floor(a / 3600)) + ":" + p2(Math.floor(a / 60) % 60);
  if (a % 60 !== 0) r += ":" + p2(a % 60);
  return r;
}
function fmtLocal(t: number, off: number): string {
  const l = t + off;
  const [y, m, d] = civilFromDays(fdiv(l, 86400));
  const s = fmod(l, 86400);
  return String(y).padStart(4, "0") + "-" + p2(m) + "-" + p2(d) + "T" + p2(Math.floor(s / 3600)) + ":" +
    p2(Math.floor(s / 60) % 60) + ":" + p2(s % 60) + fmtOff(off);
}

const dir = process.argv[2];
const q = fs.readFileSync(path.join(dir, "queries.txt"), "utf8");
const zones = new Map<string, Zone | null>();
const out: string[] = [];
for (const raw of q.split("\n")) {
  const line = raw.replace(/\r$/, "");
  if (line.trim() === "") continue;
  const parts = line.split(" ");
  if (parts.length < 3) continue;
  if (!zones.has(parts[1])) zones.set(parts[1], loadZone(path.join(dir, parts[1])));
  const z = zones.get(parts[1]);
  if (!z) continue;
  if (parts[0] === "UTC") {
    const t = Number(parts[2]);
    const tt = typeAt(z, t);
    out.push(`${line} -> ${fmtLocal(t, tt.off)} ${tt.abbr}`);
  } else {
    const a = parts[2];
    const W = daysFromCivil(Number(a.slice(0, 4)), Number(a.slice(5, 7)), Number(a.slice(8, 10))) * 86400 +
      Number(a.slice(11, 13)) * 3600 + Number(a.slice(14, 16)) * 60 + Number(a.slice(17, 19));
    const offs = z.types.map((x) => x.off);
    if (z.hasFooter) { offs.push(z.fstd.off); if (z.fdst) offs.push(z.fdst.off); }
    const minoff = Math.min(...offs), maxoff = Math.max(...offs);
    const res: number[] = [];
    for (const o of offs) {
      const t = W - o;
      if (typeAt(z, t).off === o && !res.includes(t)) res.push(t);
    }
    if (res.length > 0) {
      res.sort((x, y) => x - y);
      out.push(`${line} -> ` + res.map((t) => { const tt = typeAt(z, t); return `${t} ${fmtOff(tt.off)} ${tt.abbr}`; }).join(" | "));
    } else {
      const lo = W - maxoff - 2, hi = W - minoff + 2;
      const c = z.times.filter((x) => x >= lo && x <= hi);
      const lastT = z.times.length ? z.times[z.times.length - 1] : -Infinity;
      if (z.times.length && lastT + 1 >= lo && lastT + 1 <= hi) c.push(lastT + 1);
      if (z.fdst) {
        for (let y = yearOf(lo) - 1; y <= yearOf(hi) + 1; y++) {
          for (const x of events(z, y)) if (x > lastT && x >= lo && x <= hi) c.push(x);
        }
      }
      c.sort((x, y) => x - y);
      let t = W - typeAt(z, W).off;
      for (const tr of c) {
        const ob = typeAt(z, tr - 1).off, oa = typeAt(z, tr).off;
        if (tr + ob <= W && W < tr + oa) { t = W - ob; break; }
      }
      const tt = typeAt(z, t);
      out.push(`${line} -> GAP ${t} ${fmtLocal(t, tt.off)} ${tt.abbr}`);
    }
  }
}
process.stdout.write(out.length ? out.join("\n") + "\n" : "");
