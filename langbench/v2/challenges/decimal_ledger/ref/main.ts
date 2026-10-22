import * as fs from "fs";
import * as path from "path";

type Dec = { v: bigint; s: number };
function parse(t: string): Dec {
  let neg = false;
  if (t.startsWith("-")) { neg = true; t = t.slice(1); }
  const dot = t.indexOf(".");
  let ip = t, fp = "";
  if (dot >= 0) { ip = t.slice(0, dot); fp = t.slice(dot + 1); }
  let v = BigInt((ip + fp) || "0");
  if (neg) v = -v;
  return { v, s: fp.length };
}
function pow10(n: number): bigint { return 10n ** BigInt(n); }
function rnd(d: Dec, m: number): bigint {
  if (d.s <= m) return d.v * pow10(m - d.s);
  const div = pow10(d.s - m);
  const neg = d.v < 0n;
  const a = neg ? -d.v : d.v;
  let q = a / div;
  const r = a % div;
  const twice = r * 2n;
  if (twice > div || (twice === div && q % 2n === 1n)) q += 1n;
  return neg ? -q : q;
}
function fmt(v: bigint, m: number): string {
  const neg = v < 0n;
  let s = (neg ? -v : v).toString();
  while (s.length <= m) s = "0" + s;
  const ip = s.slice(0, s.length - m);
  const fp = s.slice(s.length - m);
  let g = "";
  for (let i = 0; i < ip.length; i++) {
    if (i > 0 && (ip.length - i) % 3 === 0) g += ",";
    g += ip[i];
  }
  return (neg ? "-" : "") + g + (m > 0 ? "." + fp : "");
}

const curs = new Map<string, number>();
type Acct = { cur: string; bal: bigint; limit: bigint };
const accts = new Map<string, Acct>();

function unknown(names: string[]): string | null {
  for (const n of names) if (!accts.has(n)) return "unknown account " + n;
  return null;
}
function insufficient(a: Acct, amt: bigint): boolean { return a.bal - amt < -a.limit; }

function exec(f: string[]): string | null {
  const cmd = f[0];
  switch (cmd) {
    case "currency": curs.set(f[1], parseInt(f[2], 10)); return null;
    case "open": {
      if (accts.has(f[1])) return "account exists " + f[1];
      const m = curs.get(f[2])!;
      accts.set(f[1], { cur: f[2], bal: 0n, limit: f.length > 3 ? rnd(parse(f[3]), m) : 0n });
      return null;
    }
    case "deposit": case "withdraw": {
      const u = unknown([f[1]]); if (u) return u;
      const a = accts.get(f[1])!;
      const amt = rnd(parse(f[2]), curs.get(a.cur)!);
      if (amt <= 0n) return "non-positive amount";
      if (cmd === "withdraw") {
        if (insufficient(a, amt)) return "insufficient funds " + f[1];
        a.bal -= amt;
      } else a.bal += amt;
      return null;
    }
    case "transfer": {
      const u = unknown([f[1], f[2]]); if (u) return u;
      const a = accts.get(f[1])!, b = accts.get(f[2])!;
      if (a.cur !== b.cur) return "currency mismatch";
      const amt = rnd(parse(f[3]), curs.get(a.cur)!);
      if (amt <= 0n) return "non-positive amount";
      if (insufficient(a, amt)) return "insufficient funds " + f[1];
      a.bal -= amt; b.bal += amt;
      return null;
    }
    case "convert": {
      const u = unknown([f[1], f[2]]); if (u) return u;
      const a = accts.get(f[1])!, b = accts.get(f[2])!;
      const ma = curs.get(a.cur)!, mb = curs.get(b.cur)!;
      const amt = rnd(parse(f[3]), ma);
      if (amt <= 0n) return "non-positive amount";
      if (insufficient(a, amt)) return "insufficient funds " + f[1];
      const r = parse(f[4]);
      const got = rnd({ v: amt * r.v, s: ma + r.s }, mb);
      a.bal -= amt; b.bal += got;
      return null;
    }
    case "interest": {
      const u = unknown([f[1]]); if (u) return u;
      const a = accts.get(f[1])!;
      const m = curs.get(a.cur)!;
      const r = parse(f[2]);
      a.bal += rnd({ v: a.bal * r.v, s: m + r.s }, m);
      return null;
    }
    case "split": {
      const names = [f[1]];
      const w: bigint[] = [];
      for (let i = 3; i < f.length; i++) {
        const c = f[i].lastIndexOf(":");
        names.push(f[i].slice(0, c));
        w.push(BigInt(f[i].slice(c + 1)));
      }
      const u = unknown(names); if (u) return u;
      const a = accts.get(f[1])!;
      for (let i = 1; i < names.length; i++) if (accts.get(names[i])!.cur !== a.cur) return "currency mismatch";
      const A = rnd(parse(f[2]), curs.get(a.cur)!);
      if (A <= 0n) return "non-positive amount";
      if (insufficient(a, A)) return "insufficient funds " + f[1];
      let W = 0n; for (const x of w) W += x;
      const q: bigint[] = [], rem: bigint[] = [];
      let left = A;
      for (const x of w) { q.push(A * x / W); rem.push(A * x % W); left -= q[q.length - 1]; }
      const idx = w.map((_, i) => i);
      idx.sort((x, y) => (rem[y] > rem[x] ? 1 : rem[y] < rem[x] ? -1 : x - y));
      for (let j = 0; j < Number(left); j++) q[idx[j]] += 1n;
      a.bal -= A;
      for (let i = 0; i < w.length; i++) accts.get(names[i + 1])!.bal += q[i];
      return null;
    }
  }
  return null;
}

const text = fs.readFileSync(path.join(process.argv[2], "ledger.txt"), "utf8");
const lines = text.split("\n");
if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
const out: string[] = [];
lines.forEach((raw, i) => {
  const line = raw.replace(/\r$/, "").trim();
  if (line === "" || line.startsWith("#")) return;
  const r = exec(line.split(/ +/));
  if (r !== null) out.push(`line ${i + 1}: REJECT ${r}`);
});
out.push("");
const names = [...accts.keys()].sort((x, y) => (Buffer.compare(Buffer.from(x), Buffer.from(y))));
const tot = new Map<string, bigint>(), cnt = new Map<string, number>();
for (const n of names) {
  const a = accts.get(n)!;
  out.push(`${n} ${a.cur} ${fmt(a.bal, curs.get(a.cur)!)}`);
  tot.set(a.cur, (tot.get(a.cur) ?? 0n) + a.bal);
  cnt.set(a.cur, (cnt.get(a.cur) ?? 0) + 1);
}
out.push("");
const cs = [...tot.keys()].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0));
for (const c of cs) out.push(`total ${c} ${fmt(tot.get(c)!, curs.get(c)!)} (${cnt.get(c)} accounts)`);
process.stdout.write(out.join("\n") + "\n");
