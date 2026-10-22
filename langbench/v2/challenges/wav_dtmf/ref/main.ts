import * as fs from "fs";
import * as path from "path";

const LOWF = [697, 770, 852, 941];
const HIGHF = [1209, 1336, 1477, 1633];
const KEYS = ["123A", "456B", "789C", "*0#D"];

function processFile(name: string, buf: Buffer): string {
  const len = buf.length;
  let fmt: Buffer | null = null;
  let data: Buffer | null = null;
  let pos = 12;
  while (pos + 8 <= len) {
    const id = buf.toString("latin1", pos, pos + 4);
    const n = buf.readUInt32LE(pos + 4);
    const start = pos + 8;
    const end = Math.min(start + n, len);
    if (id === "fmt ") fmt = buf.subarray(start, end);
    else if (id === "data") data = buf.subarray(start, end);
    pos = start + n + (n & 1);
  }
  if (!fmt || fmt.length < 16) return `${name}: unsupported\n`;
  const tag = fmt.readUInt16LE(0), ch = fmt.readUInt16LE(2), rate = fmt.readUInt32LE(4), bits = fmt.readUInt16LE(14);
  let eff = tag;
  if (tag === 0xfffe) eff = fmt.length >= 26 ? fmt.readUInt16LE(24) : 0;
  if (eff !== 1 || bits !== 16) return `${name}: unsupported\n`;
  const d = data ?? Buffer.alloc(0);
  const frames = Math.floor(d.length / (2 * ch));
  const N = Math.floor(rate / 50);
  const nb = Math.floor(frames / N);
  let out = `${name}: ${rate} Hz, ${ch} ch, ${nb} blocks\n`;
  const lab: string[] = new Array(nb).fill("");
  const x = new Float64Array(N);
  for (let b = 0; b < nb; b++) {
    let E = 0;
    for (let i = 0; i < N; i++) {
      const s = d.readInt16LE((b * N + i) * 2 * ch);
      x[i] = s;
      E += s * s;
    }
    if (E < 1000000 * N) continue;
    const r: number[] = [];
    for (let k = 0; k < 8; k++) {
      const fq = k < 4 ? LOWF[k] : HIGHF[k - 4];
      const c = 2 * Math.cos((2 * Math.PI * fq) / rate);
      let s1 = 0, s2 = 0;
      for (let i = 0; i < N; i++) { const s = x[i] + c * s1 - s2; s2 = s1; s1 = s; }
      const P = s1 * s1 + s2 * s2 - c * s1 * s2;
      r.push((2 * P) / (N * E));
    }
    let bl = 0, bh = 0;
    for (let k = 1; k < 4; k++) { if (r[k] > r[bl]) bl = k; if (r[4 + k] > r[4 + bh]) bh = k; }
    if (r[bl] >= 0.1 && r[4 + bh] >= 0.1 && r[bl] + r[4 + bh] >= 0.6) lab[b] = KEYS[bl][bh];
  }
  const br = lab.slice();
  for (let b = 1; b + 1 < nb; b++)
    if (lab[b] === "" && lab[b - 1] !== "" && lab[b - 1] === lab[b + 1]) br[b] = lab[b - 1];
  let digits = "";
  let i = 0;
  while (i < nb) {
    let j = i;
    while (j + 1 < nb && br[j + 1] === br[i]) j++;
    if (br[i] !== "" && j - i + 1 >= 2) {
      out += `${br[i]} ${i * 20} ${(j + 1) * 20}\n`;
      digits += br[i];
    }
    i = j + 1;
  }
  out += `digits: ${digits}\n`;
  return out;
}

const dir = process.argv[2];
const names = fs.readdirSync(dir).filter((s) => s.endsWith(".wav"));
names.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
const parts: string[] = [];
for (const nm of names) parts.push(processFile(nm, fs.readFileSync(path.join(dir, nm))));
process.stdout.write(parts.join("\n"));
