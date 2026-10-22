import * as fs from "fs";
import * as path from "path";

const isWs = (c: number) => c === 0x20 || c === 0x09;

function trimWs(s: string): string {
  let a = 0, b = s.length;
  while (a < b && (s[a] === " " || s[a] === "\t")) a++;
  while (b > a && (s[b - 1] === " " || s[b - 1] === "\t")) b--;
  return s.substring(a, b);
}

function find(h: Buffer, n: Buffer, from: number): number {
  return h.indexOf(n, from);
}

function hexv(c: number): number {
  if (c >= 48 && c <= 57) return c - 48;
  if (c >= 97 && c <= 102) return c - 87;
  if (c >= 65 && c <= 70) return c - 55;
  return -1;
}

function b64val(c: number): number {
  if (c >= 65 && c <= 90) return c - 65;
  if (c >= 97 && c <= 122) return c - 97 + 26;
  if (c >= 48 && c <= 57) return c - 48 + 52;
  if (c === 43) return 62;
  if (c === 47) return 63;
  return -1;
}

function b64decode(d: Uint8Array): Buffer {
  const out: number[] = [];
  let acc = 0, n = 0;
  for (const c of d) {
    if (c === 61) break;
    const v = b64val(c);
    if (v < 0) continue;
    acc = ((acc << 6) | v) & 0xffffff;
    n++;
    if (n === 4) {
      out.push((acc >> 16) & 255, (acc >> 8) & 255, acc & 255);
      acc = 0; n = 0;
    }
  }
  if (n === 2) out.push((acc >> 4) & 255);
  else if (n === 3) out.push((acc >> 10) & 255, (acc >> 2) & 255);
  return Buffer.from(out);
}

function qpdecode(d: Buffer, nl: Buffer): Buffer {
  const joined: number[] = [];
  let pos = 0;
  for (;;) {
    const i = find(d, nl, pos);
    const hasNl = i >= 0;
    const le = hasNl ? i : d.length;
    let e = le;
    while (e > pos && isWs(d[e - 1])) e--;
    if (e > pos && d[e - 1] === 61) {
      for (let k = pos; k < e - 1; k++) joined.push(d[k]);
    } else {
      for (let k = pos; k < e; k++) joined.push(d[k]);
      if (hasNl) for (const c of nl) joined.push(c);
    }
    if (!hasNl) break;
    pos = i + nl.length;
  }
  const out: number[] = [];
  let i = 0;
  while (i < joined.length) {
    const c = joined[i];
    if (c === 61 && i + 2 < joined.length) {
      const a = hexv(joined[i + 1]), b = hexv(joined[i + 2]);
      if (a >= 0 && b >= 0) { out.push(a * 16 + b); i += 3; continue; }
    }
    out.push(c);
    i++;
  }
  return Buffer.from(out);
}

const CP1252 = [
  0x20ac, 0x0081, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039,
  0x0152, 0x008d, 0x017d, 0x008f, 0x0090, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014,
  0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0x009d, 0x017e, 0x0178,
];

function decodeCharset(b: Uint8Array, cs0: string): string {
  const cs = cs0.toLowerCase();
  if (cs === "iso-8859-1" || cs === "latin1" || cs === "iso8859-1") {
    let s = "";
    for (const x of b) s += String.fromCharCode(x);
    return s;
  }
  if (cs === "windows-1252" || cs === "cp1252") {
    let s = "";
    for (const x of b) s += String.fromCharCode(x >= 0x80 && x < 0xa0 ? CP1252[x - 0x80] : x);
    return s;
  }
  return Buffer.from(b).toString("utf8");
}

function stripLang(cs: string): string {
  const i = cs.indexOf("*");
  return i >= 0 ? cs.substring(0, i) : cs;
}

type Seg = { word: boolean; text: string; cs: string; bytes: number[] };

function decode2047(s: string): string {
  const b = Buffer.from(s, "latin1");
  const segs: Seg[] = [];
  let textStart = 0;
  let i = 0;
  const isB = (c: number) => c === 66 || c === 98 || c === 81 || c === 113;
  while (i + 1 < b.length) {
    if (b[i] === 61 && b[i + 1] === 63) {
      let j = i + 2;
      while (j < b.length && b[j] !== 63 && !isWs(b[j])) j++;
      if (j > i + 2 && j < b.length && b[j] === 63) {
        const cs = s.substring(i + 2, j);
        if (j + 2 < b.length && isB(b[j + 1]) && b[j + 2] === 63) {
          const enc = b[j + 1];
          const ts = j + 3;
          let k = ts;
          while (k < b.length && b[k] !== 63 && !isWs(b[k])) k++;
          if (k + 1 < b.length && b[k] === 63 && b[k + 1] === 61) {
            const et = b.subarray(ts, k);
            let bytes: number[];
            if (enc === 66 || enc === 98) bytes = Array.from(b64decode(et));
            else {
              bytes = [];
              let p = 0;
              while (p < et.length) {
                const c = et[p];
                if (c === 95) bytes.push(0x20);
                else if (c === 61 && p + 2 < et.length && hexv(et[p + 1]) >= 0 && hexv(et[p + 2]) >= 0) {
                  bytes.push(hexv(et[p + 1]) * 16 + hexv(et[p + 2]));
                  p += 3;
                  continue;
                } else bytes.push(c);
                p++;
              }
            }
            if (textStart < i) segs.push({ word: false, text: s.substring(textStart, i), cs: "", bytes: [] });
            segs.push({ word: true, text: "", cs: stripLang(cs).toLowerCase(), bytes });
            i = k + 2;
            textStart = i;
            continue;
          }
        }
      }
    }
    i++;
  }
  if (textStart < b.length) segs.push({ word: false, text: s.substring(textStart), cs: "", bytes: [] });
  const cleaned: Seg[] = [];
  let prevWord = false;
  for (let idx = 0; idx < segs.length; idx++) {
    const sg = segs[idx];
    const drop = !sg.word && prevWord && idx + 1 < segs.length && segs[idx + 1].word && /^[ \t]*$/.test(sg.text);
    if (!drop) { prevWord = sg.word; cleaned.push(sg); }
  }
  let out = "";
  let k = 0;
  while (k < cleaned.length) {
    const sg = cleaned[k];
    if (!sg.word) { out += sg.text; k++; continue; }
    const cs = sg.cs;
    const bytes: number[] = [];
    while (k < cleaned.length && cleaned[k].word && cleaned[k].cs === cs) {
      bytes.push(...cleaned[k].bytes);
      k++;
    }
    out += decodeCharset(Uint8Array.from(bytes), cs);
  }
  return out;
}

type Ctype = { main: string; params: [string, string][] };

function parseCtype(v: string, hasSub: boolean): Ctype {
  let i = 0;
  const n = v.length;
  const ws = (ch: string) => ch === " " || ch === "\t";
  const skip = () => { while (i < n && ws(v[i])) i++; };
  skip();
  let st = i;
  while (i < n && v[i] !== "/" && v[i] !== ";" && !ws(v[i])) i++;
  let main = v.substring(st, i).toLowerCase();
  if (hasSub) {
    skip();
    if (i < n && v[i] === "/") {
      i++;
      skip();
      const s2 = i;
      while (i < n && v[i] !== ";" && !ws(v[i])) i++;
      main = main + "/" + v.substring(s2, i).toLowerCase();
    }
  }
  const params: [string, string][] = [];
  for (;;) {
    skip();
    if (i >= n) break;
    if (v[i] !== ";") { i++; continue; }
    i++;
    skip();
    const a = i;
    while (i < n && v[i] !== "=" && v[i] !== ";" && !ws(v[i])) i++;
    const attr = v.substring(a, i).toLowerCase();
    skip();
    if (i < n && v[i] === "=") {
      i++;
      skip();
      let val = "";
      if (i < n && v[i] === '"') {
        i++;
        while (i < n && v[i] !== '"') {
          if (v[i] === "\\" && i + 1 < n) i++;
          val += v[i];
          i++;
        }
        if (i < n) i++;
      } else {
        while (i < n && v[i] !== ";" && !ws(v[i])) { val += v[i]; i++; }
      }
      if (attr.length > 0) params.push([attr, val]);
    }
  }
  return { main, params };
}

function getParam(c: Ctype, n: string): string | undefined {
  const p = c.params.find((x) => x[0] === n);
  return p ? p[1] : undefined;
}

function pctDecode(s: string): number[] {
  const b = Buffer.from(s, "latin1");
  const o: number[] = [];
  let i = 0;
  while (i < b.length) {
    if (b[i] === 37 && i + 2 < b.length && hexv(b[i + 1]) >= 0 && hexv(b[i + 2]) >= 0) {
      o.push(hexv(b[i + 1]) * 16 + hexv(b[i + 2]));
      i += 3;
    } else { o.push(b[i]); i++; }
  }
  return o;
}

function splitCs(v: string): [string, string] {
  const p1 = v.indexOf("'");
  if (p1 >= 0) {
    const p2 = v.indexOf("'", p1 + 1);
    if (p2 >= 0) return [v.substring(0, p1), v.substring(p2 + 1)];
  }
  return ["", v];
}

function rfc2231(c: Ctype, a: string): string | undefined {
  const single = getParam(c, a + "*");
  if (single !== undefined) {
    const [cs, rest] = splitCs(single);
    return decodeCharset(Uint8Array.from(pctDecode(rest)), cs === "" ? "us-ascii" : stripLang(cs));
  }
  const segs: [number, boolean, string][] = [];
  const pre = a + "*";
  for (const [n, v] of c.params) {
    if (!n.startsWith(pre)) continue;
    let rest = n.substring(pre.length);
    let enc = false;
    if (rest.endsWith("*")) { enc = true; rest = rest.substring(0, rest.length - 1); }
    if (/^[0-9]+$/.test(rest)) segs.push([parseInt(rest, 10), enc, v]);
  }
  if (segs.length === 0) return undefined;
  segs.sort((x, y) => x[0] - y[0]);
  let cs = "";
  const bytes: number[] = [];
  let firstEnc = true;
  for (const [, enc, v] of segs) {
    if (enc) {
      if (firstEnc) {
        firstEnc = false;
        const [c2, rest] = splitCs(v);
        cs = c2;
        bytes.push(...pctDecode(rest));
      } else bytes.push(...pctDecode(v));
    } else bytes.push(...Buffer.from(v, "latin1"));
  }
  return decodeCharset(Uint8Array.from(bytes), cs === "" ? "us-ascii" : stripLang(cs));
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[i] = c >>> 0;
  }
  return t;
})();

function crc32(d: Uint8Array): number {
  let c = 0xffffffff;
  for (const b of d) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function quote(s: string): string {
  let o = '"';
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (ch === "\\") o += "\\\\";
    else if (ch === '"') o += '\\"';
    else if (ch === "\n") o += "\\n";
    else if (ch === "\t") o += "\\t";
    else if (cp < 0x20 || (cp >= 0x7f && cp <= 0x9f)) o += "\\u" + cp.toString(16).padStart(4, "0");
    else o += ch;
  }
  return o + '"';
}

type Entity = { headers: Map<string, string>; body: Buffer };

function parseEntity(d: Buffer, nl: Buffer): Entity {
  const lines: string[] = [];
  let pos = 0;
  let body = d.subarray(d.length);
  while (pos < d.length) {
    const i = find(d, nl, pos);
    const le = i >= 0 ? i : d.length;
    const next = i >= 0 ? i + nl.length : d.length;
    if (le === pos) { body = d.subarray(Math.min(next, d.length)); break; }
    const line = d.subarray(pos, le).toString("latin1");
    if ((line[0] === " " || line[0] === "\t") && lines.length > 0) lines[lines.length - 1] += line;
    else lines.push(line);
    pos = next;
  }
  const headers = new Map<string, string>();
  for (const l of lines) {
    const c = l.indexOf(":");
    if (c < 0) continue;
    const name = trimWs(l.substring(0, c)).toLowerCase();
    if (!headers.has(name)) headers.set(name, trimWs(l.substring(c + 1)));
  }
  return { headers, body };
}

function splitMultipart(body: Buffer, bnd: string, nl: Buffer): Buffer[] {
  const db = Buffer.from("--" + bnd, "latin1");
  const parts: Buffer[] = [];
  let pos = 0;
  let cur = -1;
  let closed = false;
  const allWs = (b: Buffer) => { for (const c of b) if (!isWs(c)) return false; return true; };
  for (;;) {
    const i = find(body, nl, pos);
    const has = i >= 0;
    const le = has ? i : body.length;
    const next = has ? i + nl.length : body.length;
    const line = body.subarray(pos, le);
    let kind = 0;
    if (line.length >= db.length && line.subarray(0, db.length).equals(db)) {
      const rest = line.subarray(db.length);
      if (allWs(rest)) kind = 1;
      else if (rest.length >= 2 && rest[0] === 45 && rest[1] === 45 && allWs(rest.subarray(2))) kind = 2;
    }
    if (kind !== 0) {
      if (cur >= 0) {
        const end = Math.max(cur, pos - nl.length);
        parts.push(body.subarray(cur, end));
      }
      if (kind === 2) { closed = true; break; }
      cur = next;
    }
    if (!has) break;
    pos = next;
  }
  if (!closed && cur >= 0 && cur <= body.length) parts.push(body.subarray(cur));
  return parts;
}

function render(d: Buffer, nl: Buffer, depth: number, out: string[]): void {
  const e = parseEntity(d, nl);
  const ind = "  ".repeat(depth);
  const ctv = e.headers.get("content-type");
  const ct: Ctype = ctv !== undefined ? parseCtype(ctv, true) : { main: "text/plain", params: [] };
  const cte = (e.headers.get("content-transfer-encoding") ?? "7bit").toLowerCase();
  const cdv = e.headers.get("content-disposition");
  const cd = cdv !== undefined ? parseCtype(cdv, false) : undefined;
  const ty = ct.main.split("/")[0];
  if (ty === "multipart") {
    const parts = splitMultipart(e.body, getParam(ct, "boundary") ?? "", nl);
    out.push(`${ind}${ct.main} parts=${parts.length}\n`);
    for (const p of parts) render(p, nl, depth + 1, out);
    return;
  }
  let raw: Buffer;
  if (cte === "base64") raw = b64decode(e.body);
  else if (cte === "quoted-printable") raw = qpdecode(e.body, nl);
  else raw = e.body;
  const disp = cd ? cd.main : undefined;
  if (ty === "text" && disp !== "attachment") {
    const cs = (getParam(ct, "charset") ?? "us-ascii").toLowerCase();
    let txt = decodeCharset(raw, cs);
    txt = txt.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    out.push(`${ind}${ct.main} charset=${cs} cte=${cte} chars=${Array.from(txt).length}\n`);
    out.push(`${ind}  ${quote(txt)}\n`);
  } else {
    let name: string | undefined;
    if (cd) {
      name = rfc2231(cd, "filename");
      if (name === undefined) { const f = getParam(cd, "filename"); if (f !== undefined) name = decode2047(f); }
    }
    if (name === undefined) name = rfc2231(ct, "name");
    if (name === undefined) { const f = getParam(ct, "name"); if (f !== undefined) name = decode2047(f); }
    out.push(
      `${ind}${ct.main} cte=${cte} disposition=${disp ?? "-"} name=${name !== undefined ? quote(name) : "-"} size=${raw.length} crc32=${crc32(raw).toString(16).padStart(8, "0")}\n`,
    );
  }
}

function main(): void {
  const dir = process.argv[2];
  const names = fs.readdirSync(dir).filter((n) => n.endsWith(".eml"));
  names.sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  const out: string[] = [];
  names.forEach((n, idx) => {
    if (idx > 0) out.push("\n");
    const d = fs.readFileSync(path.join(dir, n));
    const p = d.indexOf(10);
    const nl = Buffer.from(p > 0 && d[p - 1] === 13 ? "\r\n" : "\n", "latin1");
    const e = parseEntity(d, nl);
    out.push(`== ${n}\n`);
    for (const [h, label] of [["from", "From"], ["subject", "Subject"]]) {
      const v = e.headers.get(h);
      out.push(v !== undefined ? `${label}: ${quote(decode2047(v))}\n` : `${label}: -\n`);
    }
    render(d, nl, 0, out);
  });
  process.stdout.write(Buffer.from(out.join(""), "utf8"));
}

main();
