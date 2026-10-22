import * as fs from "fs";
import * as path from "path";

function isInk(r: number, g: number, b: number): boolean {
  return Math.floor((299 * r + 587 * g + 114 * b) / 1000) < 128;
}

function chan(px: number, mask: number): number {
  if (mask === 0) return 0;
  let s = 0;
  while (((mask >>> s) & 1) === 0) s++;
  let n = 0;
  while (s + n < 32 && ((mask >>> (s + n)) & 1) === 1) n++;
  const v = ((px & mask) >>> 0) / Math.pow(2, s);
  const m = Math.pow(2, n) - 1;
  return Math.floor((Math.floor(v) * 255 + Math.floor(m / 2)) / m);
}

function processFile(dir: string, name: string, out: string[]): void {
  out.push(`== ${name}`);
  const d = fs.readFileSync(path.join(dir, name));
  const off = d.readUInt32LE(10);
  const hsz = d.readUInt32LE(14);
  const w = d.readInt32LE(18);
  const hraw = d.readInt32LE(22);
  const bpp = d.readUInt16LE(28);
  const comp = d.readUInt32LE(30);
  const clrUsed = d.readUInt32LE(46);
  if (comp !== 0 && comp !== 3) {
    out.push(`error: unsupported compression ${comp}`);
    return;
  }
  const bottomUp = hraw > 0;
  const h = Math.abs(hraw);
  let rm = 0, gm = 0, bm = 0;
  if (comp === 3) {
    const p = hsz >= 108 ? 54 : 14 + hsz;
    rm = d.readUInt32LE(p); gm = d.readUInt32LE(p + 4); bm = d.readUInt32LE(p + 8);
  }
  const palInk: boolean[] = [];
  if (bpp <= 8) {
    const n = clrUsed !== 0 ? clrUsed : 1 << bpp;
    const pal = 14 + hsz;
    for (let i = 0; i < n; i++) {
      const e = pal + 4 * i;
      palInk.push(isInk(d[e + 2], d[e + 1], d[e]));
    }
  }
  const stride = Math.floor((w * bpp + 31) / 32) * 4;
  const ink = new Uint8Array(w * h);
  let inkCount = 0;
  for (let y = 0; y < h; y++) {
    const srow = bottomUp ? h - 1 - y : y;
    const row = off + srow * stride;
    for (let x = 0; x < w; x++) {
      let is: boolean;
      if (bpp <= 8) {
        let idx: number;
        if (bpp === 8) idx = d[row + x];
        else if (bpp === 4) idx = (d[row + (x >> 1)] >> ((x & 1) ? 0 : 4)) & 15;
        else idx = (d[row + (x >> 3)] >> (7 - (x & 7))) & 1;
        is = idx < palInk.length && palInk[idx];
      } else if (bpp === 24) {
        const p = row + 3 * x;
        is = isInk(d[p + 2], d[p + 1], d[p]);
      } else if (comp === 0) {
        const p = row + 4 * x;
        is = isInk(d[p + 2], d[p + 1], d[p]);
      } else {
        const px = d.readUInt32LE(row + 4 * x);
        is = isInk(chan(px, rm), chan(px, gm), chan(px, bm));
      }
      if (is) { ink[y * w + x] = 1; inkCount++; }
    }
  }
  out.push(`size ${w}x${h} bpp ${bpp} header ${hsz} ${bottomUp ? "bottom-up" : "top-down"} ink ${inkCount}`);
  const lines: string[] = [];
  const stack: number[] = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i0 = y * w + x;
      if (ink[i0] !== 1) continue;
      ink[i0] = 2;
      stack.push(i0);
      let minx = x, maxx = x, miny = y, maxy = y, cnt = 0;
      while (stack.length) {
        const i = stack.pop()!;
        const cy = Math.floor(i / w), cx = i - cy * w;
        cnt++;
        if (cx < minx) minx = cx; if (cx > maxx) maxx = cx;
        if (cy < miny) miny = cy; if (cy > maxy) maxy = cy;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const nx = cx + dx, ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const j = ny * w + nx;
          if (ink[j] === 1) { ink[j] = 2; stack.push(j); }
        }
      }
      lines.push(`${x},${y} bbox ${minx},${miny}-${maxx},${maxy} pixels ${cnt}`);
    }
  }
  out.push(`components ${lines.length}`);
  for (const l of lines) out.push(l);
}

const dir = process.argv[2];
const names = fs.readdirSync(dir).filter((n) => n.endsWith(".bmp"));
names.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
const out: string[] = [];
for (const n of names) processFile(dir, n, out);
process.stdout.write(out.length ? out.join("\n") + "\n" : "");
