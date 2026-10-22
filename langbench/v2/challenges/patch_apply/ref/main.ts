import * as fs from "fs";
import * as path from "path";

interface Hunk { a: number; b: number; c: number; d: number; malformed: boolean; oldL: Buffer[]; newL: Buffer[]; }
interface Section { path: string; kind: string; hunks: Hunk[]; }

function splitLines(data: Buffer): Buffer[] {
  const out: Buffer[] = [];
  let start = 0;
  for (let i = 0; i < data.length; i++) {
    if (data[i] === 0x0a) { out.push(data.subarray(start, i + 1)); start = i + 1; }
  }
  if (start < data.length) out.push(data.subarray(start));
  return out;
}

function startsWith(l: Buffer, s: string): boolean {
  if (l.length < s.length) return false;
  for (let i = 0; i < s.length; i++) if (l[i] !== s.charCodeAt(i)) return false;
  return true;
}

function str(l: Buffer): string {
  let n = l.length;
  while (n > 0 && (l[n - 1] === 0x0a || l[n - 1] === 0x0d)) n--;
  return l.subarray(0, n).toString("latin1");
}

const HDR = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*$/;

const crcTable: number[] = [];
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
  crcTable.push(c >>> 0);
}

const root = process.argv[2];
const diff = fs.readFileSync(path.join(root, "changes.diff"));
const lines = splitLines(diff);
const sections: Section[] = [];
let cur: Section | null = null;
let hunk: Hunk | null = null;
let inHunk = false;
for (let i = 0; i < lines.length; i++) {
  const l = lines[i];
  if (startsWith(l, "diff --git ")) {
    const parts = str(l).split(" ");
    let p = parts[2];
    if (p.startsWith("a/")) p = p.substring(2);
    cur = { path: p, kind: "modify", hunks: [] };
    sections.push(cur);
    inHunk = false; hunk = null;
    continue;
  }
  if (cur === null) continue;
  if (startsWith(l, "@@ ")) {
    const m = HDR.exec(str(l));
    hunk = { a: 0, b: 0, c: 0, d: 0, malformed: false, oldL: [], newL: [] };
    if (m) {
      hunk.a = parseInt(m[1], 10);
      hunk.b = m[2] === undefined ? 1 : parseInt(m[2], 10);
      hunk.c = parseInt(m[3], 10);
      hunk.d = m[4] === undefined ? 1 : parseInt(m[4], 10);
    } else hunk.malformed = true;
    cur.hunks.push(hunk);
    inHunk = true;
    continue;
  }
  if (!inHunk) {
    if (startsWith(l, "new file mode")) cur.kind = "create";
    else if (startsWith(l, "deleted file mode")) cur.kind = "delete";
    continue;
  }
  if (l.length === 0 || hunk === null) continue;
  const mk = l[0];
  if (mk === 0x5c) continue;
  let content = l.subarray(1);
  const noNl = i + 1 < lines.length && lines[i + 1].length > 0 && lines[i + 1][0] === 0x5c;
  if (noNl && content.length > 0 && content[content.length - 1] === 0x0a) content = content.subarray(0, content.length - 1);
  if (mk === 0x20) { hunk.oldL.push(content); hunk.newL.push(content); }
  else if (mk === 0x2d) hunk.oldL.push(content);
  else if (mk === 0x2b) hunk.newL.push(content);
  else if (mk === 0x0a) { const nl = Buffer.from([0x0a]); hunk.oldL.push(nl); hunk.newL.push(nl); }
}
for (const s of sections) for (const h of s.hunks) if (h.oldL.length !== h.b || h.newL.length !== h.d) h.malformed = true;

sections.sort((x, y) => Buffer.compare(Buffer.from(x.path, "latin1"), Buffer.from(y.path, "latin1")));

const out: string[] = [];
for (const s of sections) {
  out.push(`${s.path}: ${s.kind}`);
  const fp = path.join(root, "tree", s.path);
  let exists = false;
  try { exists = fs.statSync(fp).isFile(); } catch { exists = false; }
  let content: Buffer[];
  if (s.kind === "create") {
    if (exists) { out.push("  error: already exists"); continue; }
    content = [];
  } else {
    if (!exists) { out.push("  error: not found"); continue; }
    content = splitLines(fs.readFileSync(fp));
  }
  let delta = 0;
  let allApplied = true;
  let n = 0;
  for (const h of s.hunks) {
    n++;
    if (h.malformed) { out.push(`  hunk ${n}: malformed`); allApplied = false; continue; }
    const P = h.a + delta;
    if (h.b === 0) {
      if (P >= 0 && P <= content.length) {
        content.splice(P, 0, ...h.newL);
        out.push(`  hunk ${n}: applied at ${P} (offset 0)`);
        delta += h.d - h.b;
      } else { out.push(`  hunk ${n}: rejected`); allApplied = false; }
      continue;
    }
    const maxP = content.length - h.b + 1;
    let found = -1;
    if (maxP >= 1) {
      const lim = Math.max(Math.abs(P - 1), Math.abs(P - maxP)) + 1;
      for (let k = 0; k <= lim && found < 0; k++) {
        const cands = k === 0 ? [P] : [P - k, P + k];
        for (const p of cands) {
          if (p < 1 || p > maxP) continue;
          let ok = true;
          for (let j = 0; j < h.b; j++) {
            if (!content[p - 1 + j].equals(h.oldL[j])) { ok = false; break; }
          }
          if (ok) { found = p; break; }
        }
      }
    }
    if (found < 0) { out.push(`  hunk ${n}: rejected`); allApplied = false; continue; }
    content.splice(found - 1, h.b, ...h.newL);
    const off = found - P;
    const os = off > 0 ? `+${off}` : `${off}`;
    out.push(`  hunk ${n}: applied at ${found} (offset ${os})`);
    delta += h.d - h.b;
  }
  if (s.kind === "delete" && allApplied && content.length === 0) { out.push("  result: deleted"); continue; }
  let c = 0xffffffff;
  for (const l of content) for (let i = 0; i < l.length; i++) c = crcTable[(c ^ l[i]) & 0xff] ^ (c >>> 8);
  c = (c ^ 0xffffffff) >>> 0;
  out.push(`  result: ${content.length} lines, crc32 ${c.toString(16).padStart(8, "0")}`);
}
process.stdout.write(out.length ? out.join("\n") + "\n" : "");
