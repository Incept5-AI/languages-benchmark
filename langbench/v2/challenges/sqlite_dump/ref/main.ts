import * as fs from "fs";
import * as path from "path";

const db: Buffer = fs.readFileSync(path.join(process.argv[2], "db.sqlite"));
let pageSize = db.readUInt16BE(16);
if (pageSize === 1) pageSize = 65536;
const usable = pageSize - db[20];

function varint(p: Buffer, off: number): [bigint, number] {
  let v = 0n;
  for (let i = 0; i < 8; i++) {
    const b = p[off + i];
    v = (v << 7n) | BigInt(b & 0x7f);
    if ((b & 0x80) === 0) return [v, i + 1];
  }
  v = (v << 8n) | BigInt(p[off + 8]);
  return [BigInt.asUintN(64, v), 9];
}

interface Row { rowid: bigint; payload: Buffer; }

function pageOff(n: number): number { return (n - 1) * pageSize; }

function walk(pg: number, rows: Row[], depth: number): void {
  if (depth > 64) throw new Error("too deep");
  const base = pageOff(pg);
  const h = base + (pg === 1 ? 100 : 0);
  const type = db[h];
  const ncell = db.readUInt16BE(h + 3);
  if (type === 0x05) {
    for (let i = 0; i < ncell; i++) {
      const c = base + db.readUInt16BE(h + 12 + 2 * i);
      walk(db.readUInt32BE(c), rows, depth + 1);
    }
    walk(db.readUInt32BE(h + 8), rows, depth + 1);
  } else if (type === 0x0d) {
    for (let i = 0; i < ncell; i++) {
      const c = base + db.readUInt16BE(h + 8 + 2 * i);
      const [plenB, l1] = varint(db, c);
      const [rowidU, l2] = varint(db, c + l1);
      const start = c + l1 + l2;
      const plen = Number(plenB);
      const x = usable - 35;
      let out: Buffer;
      if (plen <= x) {
        out = Buffer.from(db.subarray(start, start + plen));
      } else {
        out = Buffer.alloc(plen);
        const m = Math.floor(((usable - 12) * 32) / 255) - 23;
        const k = m + ((plen - m) % (usable - 4));
        const local = k <= x ? k : m;
        db.copy(out, 0, start, start + local);
        let ov = db.readUInt32BE(start + local);
        let got = local;
        while (got < plen && ov !== 0) {
          const op = pageOff(ov);
          const take = Math.min(plen - got, usable - 4);
          db.copy(out, got, op + 4, op + 4 + take);
          got += take;
          ov = db.readUInt32BE(op);
        }
      }
      rows.push({ rowid: BigInt.asIntN(64, rowidU), payload: out });
    }
  } else {
    throw new Error(`unexpected page type ${type} at ${pg}`);
  }
}

type Val =
  | { k: "null" }
  | { k: "int"; v: bigint }
  | { k: "real"; v: Buffer }
  | { k: "text"; v: Buffer }
  | { k: "blob"; v: Buffer };

function decode(p: Buffer): Val[] {
  const [hlenB, l] = varint(p, 0);
  const hlen = Number(hlenB);
  let hp = l;
  let dp = hlen;
  const vals: Val[] = [];
  const sizes = [0, 1, 2, 3, 4, 6, 8];
  while (hp < hlen) {
    const [stB, sl] = varint(p, hp);
    hp += sl;
    const st = Number(stB);
    if (st === 0) vals.push({ k: "null" });
    else if (st >= 1 && st <= 6) {
      const n = sizes[st];
      let u = 0n;
      for (let i = 0; i < n; i++) u = (u << 8n) | BigInt(p[dp + i]);
      vals.push({ k: "int", v: BigInt.asIntN(n * 8, u) });
      dp += n;
    } else if (st === 7) {
      vals.push({ k: "real", v: p.subarray(dp, dp + 8) });
      dp += 8;
    } else if (st === 8) vals.push({ k: "int", v: 0n });
    else if (st === 9) vals.push({ k: "int", v: 1n });
    else if (st >= 12) {
      const n = Math.floor((st - 12) / 2);
      const s = p.subarray(dp, dp + n);
      dp += n;
      vals.push(st % 2 === 1 ? { k: "text", v: s } : { k: "blob", v: s });
    } else vals.push({ k: "null" });
  }
  return vals;
}

const crcTable = new Uint32Array(256);
for (let i = 0; i < 256; i++) {
  let c = i;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  crcTable[i] = c >>> 0;
}
function crc32(s: Buffer): string {
  let c = 0xffffffff;
  for (let i = 0; i < s.length; i++) c = crcTable[(c ^ s[i]) & 0xff] ^ (c >>> 8);
  return ((c ^ 0xffffffff) >>> 0).toString(16).padStart(8, "0");
}

function fmtReal(b: Buffer): string {
  const bits = b.readBigUInt64BE(0);
  const neg = (bits >> 63n) === 1n;
  const expBits = Number((bits >> 52n) & 0x7ffn);
  let mant = bits & ((1n << 52n) - 1n);
  let e: number;
  if (expBits === 0) e = -1074;
  else { mant |= 1n << 52n; e = expBits - 1075; }
  let s: string;
  if (e >= 0) {
    s = (mant << BigInt(e)).toString() + ".0";
  } else {
    const k = -e;
    const ip = mant >> BigInt(k);
    const rem = mant - (ip << BigInt(k));
    let frac = (rem * 5n ** BigInt(k)).toString().padStart(k, "0").replace(/0+$/, "");
    if (frac === "") frac = "0";
    s = ip.toString() + "." + frac;
  }
  return (neg ? "-" : "") + s;
}

function fmtVal(parts: Buffer[], v: Val): void {
  switch (v.k) {
    case "null": parts.push(Buffer.from("NULL")); break;
    case "int": parts.push(Buffer.from(v.v.toString())); break;
    case "real": parts.push(Buffer.from(fmtReal(v.v))); break;
    case "text":
      if (v.v.length > 100) parts.push(Buffer.from(`<TEXT len=${v.v.length} crc32=${crc32(v.v)}>`));
      else {
        const out: number[] = [0x22];
        const push = (s: string) => { for (const ch of s) out.push(ch.charCodeAt(0)); };
        for (const c of v.v) {
          if (c === 0x5c) push("\\\\");
          else if (c === 0x22) push('\\"');
          else if (c === 0x0a) push("\\n");
          else if (c === 0x0d) push("\\r");
          else if (c === 0x09) push("\\t");
          else if (c < 0x20 || c === 0x7f) push("\\x" + c.toString(16).toUpperCase().padStart(2, "0"));
          else out.push(c);
        }
        out.push(0x22);
        parts.push(Buffer.from(out));
      }
      break;
    case "blob":
      if (v.v.length > 100) parts.push(Buffer.from(`<BLOB len=${v.v.length} crc32=${crc32(v.v)}>`));
      else parts.push(Buffer.from("X'" + v.v.toString("hex").toUpperCase() + "'"));
      break;
  }
}

function isId(c: string): boolean { return /[A-Za-z0-9_$]/.test(c) || c.charCodeAt(0) >= 0x80; }

interface Col { name: string; alias: boolean; }

function parseCols(sql: string): Col[] {
  const n = sql.length;
  let i = 0;
  while (i < n && sql[i] !== "(") {
    if (sql[i] === '"') { i++; while (i < n && sql[i] !== '"') i++; }
    i++;
  }
  const start = i + 1;
  let depth = 1, end = n;
  for (let j = start; j < n; j++) {
    const c = sql[j];
    if (c === '"') { j++; while (j < n && sql[j] !== '"') j++; continue; }
    if (c === "(") depth++;
    else if (c === ")") { depth--; if (depth === 0) { end = j; break; } }
  }
  const defs: string[] = [];
  let ds = start;
  depth = 0;
  for (let j = start; j <= end; j++) {
    const c = j === end ? "," : sql[j];
    if (c === '"' && j < end) { j++; while (j < end && sql[j] !== '"') j++; continue; }
    if (c === "(") depth++;
    else if (c === ")") depth--;
    else if (c === "," && depth === 0) { defs.push(sql.substring(ds, j)); ds = j + 1; }
  }
  const kws = new Set(["CONSTRAINT", "PRIMARY", "NOT", "NULL", "UNIQUE", "CHECK", "DEFAULT", "COLLATE", "REFERENCES", "GENERATED", "AS"]);
  const cols: Col[] = [];
  for (const d of defs) {
    const toks: string[] = [];
    let k = 0;
    while (k < d.length) {
      const c = d[k];
      if (/\s/.test(c)) k++;
      else if (c === '"') {
        const s = k; k++;
        while (k < d.length && d[k] !== '"') k++;
        k++;
        toks.push(d.substring(s, Math.min(k, d.length)));
      } else if (isId(c)) {
        const s = k;
        while (k < d.length && isId(d[k])) k++;
        toks.push(d.substring(s, k));
      } else { toks.push(c); k++; }
    }
    if (toks.length === 0) continue;
    const t0 = toks[0];
    const name = t0.startsWith('"') ? t0.substring(1, t0.length - 1) : t0;
    let t = 1, words = 0, isInt = false;
    while (t < toks.length) {
      const tk = toks[t];
      if (!(isId(tk[0]) || tk[0] === '"')) break;
      if (kws.has(tk.toUpperCase())) break;
      words++;
      if (words === 1 && tk.toUpperCase() === "INTEGER") isInt = true;
      t++;
    }
    const paren = t < toks.length && toks[t] === "(" && words > 0;
    let pk = false;
    for (let q = 0; q + 1 < toks.length; q++)
      if (toks[q].toUpperCase() === "PRIMARY" && toks[q + 1].toUpperCase() === "KEY") pk = true;
    cols.push({ name, alias: words === 1 && isInt && !paren && pk });
  }
  return cols;
}

const schema: Row[] = [];
walk(1, schema, 0);
const tabs: { name: Buffer; root: number; sql: string }[] = [];
for (const r of schema) {
  const v = decode(r.payload);
  if (v.length < 5) continue;
  const t = v[0], nm = v[1];
  if (t.k !== "text" || nm.k !== "text") continue;
  if (t.v.toString("latin1") !== "table") continue;
  if (nm.v.length >= 7 && nm.v.subarray(0, 7).toString("latin1") === "sqlite_") continue;
  const rt = v[3];
  const sq = v[4];
  tabs.push({
    name: Buffer.from(nm.v),
    root: rt.k === "int" ? Number(rt.v) : 0,
    sql: sq.k === "text" ? sq.v.toString("utf8") : "",
  });
}
tabs.sort((a, b) => Buffer.compare(a.name, b.name));

const parts: Buffer[] = [];
tabs.forEach((tab, ti) => {
  const cols = parseCols(tab.sql);
  const rows: Row[] = [];
  if (tab.root !== 0) walk(tab.root, rows, 0);
  if (ti > 0) parts.push(Buffer.from("\n"));
  parts.push(Buffer.from("TABLE "), tab.name);
  parts.push(Buffer.from(` rows=${rows.length}\nCOLUMNS ` + cols.map((c) => c.name).join(", ") + "\n"));
  for (const r of rows) {
    const v = decode(r.payload);
    parts.push(Buffer.from(`${r.rowid}: `));
    cols.forEach((c, i) => {
      if (i > 0) parts.push(Buffer.from(", "));
      if (c.alias) parts.push(Buffer.from(r.rowid.toString()));
      else if (i < v.length) fmtVal(parts, v[i]);
      else parts.push(Buffer.from("NULL"));
    });
    parts.push(Buffer.from("\n"));
  }
});
process.stdout.write(Buffer.concat(parts));
