import * as fs from "fs";
import * as path from "path";
import * as zlib from "zlib";

const scene = JSON.parse(fs.readFileSync(path.join(process.argv[2], "scene.json"), "utf8"));
const W: number = scene.width;
const H: number = scene.height;
const bg: number[] = scene.background;
const img = new Uint8Array(W * H * 3);
for (let i = 0; i < W * H; i++) {
  img[i * 3] = bg[0];
  img[i * 3 + 1] = bg[1];
  img[i * 3 + 2] = bg[2];
}

// All products stay below 2^53 (max ~6.4e15), so plain numbers are exact.
const out: string[] = [];
const shapes: any[] = scene.shapes || [];
shapes.forEach((s, si) => {
  const type: string = s.type;
  const col: number[] = s.color;
  const A = col[3];
  let x0: number, y0: number, x1: number, y1: number;
  let cover: (x: number, y: number) => boolean;
  if (type === "rect") {
    x0 = s.x; y0 = s.y; x1 = s.x + s.w - 1; y1 = s.y + s.h - 1;
    cover = () => true;
  } else if (type === "circle") {
    const cx = s.cx, cy = s.cy, r2 = s.r * s.r;
    x0 = cx - s.r; x1 = cx + s.r; y0 = cy - s.r; y1 = cy + s.r;
    cover = (x, y) => (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2;
  } else if (type === "triangle") {
    const px = s.points.map((p: number[]) => p[0]);
    const py = s.points.map((p: number[]) => p[1]);
    x0 = Math.min(...px); x1 = Math.max(...px); y0 = Math.min(...py); y1 = Math.max(...py);
    cover = (x, y) => {
      let pos = true, neg = true;
      for (let i = 0; i < 3; i++) {
        const j = (i + 1) % 3;
        const e = (px[j] - px[i]) * (y - py[i]) - (py[j] - py[i]) * (x - px[i]);
        if (e < 0) pos = false;
        if (e > 0) neg = false;
      }
      return pos || neg;
    };
  } else {
    const ax = s.x1, ay = s.y1, bx = s.x2, by = s.y2, r = s.r, r2 = r * r;
    const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
    x0 = Math.min(ax, bx) - r; x1 = Math.max(ax, bx) + r;
    y0 = Math.min(ay, by) - r; y1 = Math.max(ay, by) + r;
    cover = (x, y) => {
      const t = (x - ax) * dx + (y - ay) * dy;
      if (t <= 0) return (x - ax) * (x - ax) + (y - ay) * (y - ay) <= r2;
      if (t >= L) return (x - bx) * (x - bx) + (y - by) * (y - by) <= r2;
      const c = (x - ax) * dy - (y - ay) * dx;
      return c * c <= r2 * L;
    };
  }
  x0 = Math.max(x0, 0); y0 = Math.max(y0, 0);
  x1 = Math.min(x1, W - 1); y1 = Math.min(y1, H - 1);
  let count = 0;
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      if (cover(x, y)) {
        count++;
        const o = (y * W + x) * 3;
        for (let c = 0; c < 3; c++) {
          img[o + c] = Math.floor((col[c] * A + img[o + c] * (255 - A) + 127) / 255);
        }
      }
    }
  }
  out.push(`${si + 1} ${type} ${count}`);
});

const seen = new Uint8Array(1 << 24);
let k = 0;
for (let i = 0; i < W * H; i++) {
  const v = (img[i * 3] << 16) | (img[i * 3 + 1] << 8) | img[i * 3 + 2];
  if (!seen[v]) { seen[v] = 1; k++; }
}
out.push(`colors ${k}`);

const crcTable = new Uint32Array(256);
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let j = 0; j < 8; j++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  crcTable[n] = c >>> 0;
}
function crc(buf: Buffer): number {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = crcTable[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const c = Buffer.alloc(4);
  c.writeUInt32BE(crc(td));
  return Buffer.concat([len, td, c]);
}
const stride = W * 3;
const raw = Buffer.alloc(H * (stride + 1));
for (let y = 0; y < H; y++) {
  raw[y * (stride + 1)] = 0;
  raw.set(img.subarray(y * stride, (y + 1) * stride), y * (stride + 1) + 1);
}
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(W, 0);
ihdr.writeUInt32BE(H, 4);
ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
const png = Buffer.concat([
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
  chunk("IHDR", ihdr),
  chunk("IDAT", zlib.deflateSync(raw)),
  chunk("IEND", Buffer.alloc(0)),
]);
fs.writeFileSync("out.png", png);
process.stdout.write(out.join("\n") + "\n");
