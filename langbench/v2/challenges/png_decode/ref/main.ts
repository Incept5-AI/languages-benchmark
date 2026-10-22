import * as fs from "fs";
import * as path from "path";

const CRCT = new Uint32Array(256);
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  CRCT[n] = c >>> 0;
}
function crcUpdate(c: number, b: Uint8Array, off: number, len: number): number {
  for (let i = 0; i < len; i++) c = CRCT[(c ^ b[off + i]) & 0xff] ^ (c >>> 8);
  return c >>> 0;
}
function be32(b: Uint8Array, p: number): number {
  return ((b[p] << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3]) >>> 0;
}

const LBASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
const LEXT = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
const DBASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
const DEXT = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];

interface Huff { count: Int32Array; sym: Int32Array; }
function build(len: ArrayLike<number>, off: number, n: number): Huff {
  const count = new Int32Array(16);
  for (let i = 0; i < n; i++) count[len[off + i]]++;
  count[0] = 0;
  const offs = new Int32Array(16);
  for (let i = 1; i < 15; i++) offs[i + 1] = offs[i] + count[i];
  const sym = new Int32Array(Math.max(n, 1));
  for (let i = 0; i < n; i++) if (len[off + i]) sym[offs[len[off + i]]++] = i;
  return { count, sym };
}

class Inf {
  pos = 2; bitbuf = 0; bitcnt = 0;
  out = new Uint8Array(65536); outlen = 0;
  nstored = 0; nfixed = 0; ndynamic = 0;
  constructor(public inp: Uint8Array) {}
  bit(): number {
    if (this.bitcnt === 0) {
      this.bitbuf = this.pos < this.inp.length ? this.inp[this.pos] : 0;
      this.pos++; this.bitcnt = 8;
    }
    const b = this.bitbuf & 1; this.bitbuf >>= 1; this.bitcnt--;
    return b;
  }
  bits(n: number): number { let v = 0; for (let i = 0; i < n; i++) v |= this.bit() << i; return v; }
  put(b: number) {
    if (this.outlen === this.out.length) {
      const nb = new Uint8Array(this.out.length * 2); nb.set(this.out); this.out = nb;
    }
    this.out[this.outlen++] = b;
  }
  decode(h: Huff): number {
    let code = 0, first = 0, index = 0;
    for (let len = 1; len < 16; len++) {
      code |= this.bit();
      const count = h.count[len];
      if (code - count < first) return h.sym[index + (code - first)];
      index += count; first += count; first <<= 1; code <<= 1;
    }
    return -1;
  }
  codes(lc: Huff, dc: Huff): boolean {
    for (;;) {
      let sym = this.decode(lc);
      if (sym < 0) return false;
      if (sym < 256) this.put(sym);
      else if (sym === 256) return true;
      else {
        sym -= 257;
        if (sym >= 29) return false;
        const len = LBASE[sym] + this.bits(LEXT[sym]);
        const d = this.decode(dc);
        if (d < 0 || d >= 30) return false;
        const dist = DBASE[d] + this.bits(DEXT[d]);
        if (dist > this.outlen) return false;
        for (let i = 0; i < len; i++) this.put(this.out[this.outlen - dist]);
      }
    }
  }
  inflate(): boolean {
    let last: number;
    do {
      last = this.bit();
      const t = this.bits(2);
      if (t === 0) {
        this.nstored++;
        this.bitcnt = 0;
        if (this.pos + 4 > this.inp.length) return false;
        const len = this.inp[this.pos] | (this.inp[this.pos + 1] << 8);
        this.pos += 4;
        if (this.pos + len > this.inp.length) return false;
        for (let i = 0; i < len; i++) this.put(this.inp[this.pos + i]);
        this.pos += len;
      } else if (t === 1) {
        this.nfixed++;
        const l: number[] = [];
        for (let i = 0; i < 288; i++) l.push(i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8);
        const dl = new Array(30).fill(5);
        if (!this.codes(build(l, 0, 288), build(dl, 0, 30))) return false;
      } else if (t === 2) {
        this.ndynamic++;
        const nlen = this.bits(5) + 257, ndist = this.bits(5) + 1, ncode = this.bits(4) + 4;
        const ord = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
        const cl = new Array(19).fill(0);
        for (let i = 0; i < ncode; i++) cl[ord[i]] = this.bits(3);
        const ch = build(cl, 0, 19);
        const lens = new Array(nlen + ndist).fill(0);
        let idx = 0;
        while (idx < nlen + ndist) {
          const sym = this.decode(ch);
          if (sym < 0) return false;
          if (sym < 16) lens[idx++] = sym;
          else {
            let rep: number, val = 0;
            if (sym === 16) { if (idx === 0) return false; val = lens[idx - 1]; rep = 3 + this.bits(2); }
            else if (sym === 17) rep = 3 + this.bits(3);
            else rep = 11 + this.bits(7);
            if (idx + rep > nlen + ndist) return false;
            while (rep-- > 0) lens[idx++] = val;
          }
        }
        if (!this.codes(build(lens, 0, nlen), build(lens, nlen, ndist))) return false;
      } else return false;
    } while (!last);
    return true;
  }
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  if (pb <= pc) return b;
  return c;
}

function process1(dir: string, name: string, o: string[]) {
  o.push(`== ${name}`);
  const buf = new Uint8Array(fs.readFileSync(path.join(dir, name)));
  const sz = buf.length;
  let p = 8, n = 0;
  const typeAt = (q: number) => String.fromCharCode(buf[q + 4], buf[q + 5], buf[q + 6], buf[q + 7]);
  while (p + 12 <= sz) {
    const len = be32(buf, p);
    n++;
    const c = (crcUpdate(0xffffffff, buf, p + 4, len + 4) ^ 0xffffffff) >>> 0;
    if (c !== be32(buf, p + 8 + len)) {
      o.push(`error: CRC mismatch in chunk ${n} ${typeAt(p)}`);
      return;
    }
    p += 12 + len;
  }
  let w = 0, h = 0, depth = 0, ctype = 0;
  let pal = new Uint8Array(0), trns = new Uint8Array(0), hastrns = false;
  const idatParts: Uint8Array[] = [];
  const types: string[] = [], runs: number[] = [];
  p = 8;
  while (p + 12 <= sz) {
    const len = be32(buf, p);
    const ty = typeAt(p);
    const d = p + 8;
    if (types.length && types[types.length - 1] === ty) runs[runs.length - 1]++;
    else { types.push(ty); runs.push(1); }
    if (ty === "IHDR") { w = be32(buf, d); h = be32(buf, d + 4); depth = buf[d + 8]; ctype = buf[d + 9]; }
    else if (ty === "PLTE") pal = buf.slice(d, d + len);
    else if (ty === "tRNS") { hastrns = true; trns = buf.slice(d, d + len); }
    else if (ty === "IDAT") idatParts.push(buf.subarray(d, d + len));
    p += 12 + len;
  }
  const chunkline = types.map((t, i) => (runs[i] > 1 ? `${t}*${runs[i]}` : t)).join(" ");
  const id = new Uint8Array(Buffer.concat(idatParts));
  const s = new Inf(id);
  s.inflate();
  let a1 = 1, a2 = 0;
  const out = s.out, outlen = s.outlen;
  for (let i = 0; i < outlen; i++) { a1 = (a1 + out[i]) % 65521; a2 = (a2 + a1) % 65521; }
  const adl = ((a2 << 16) | a1) >>> 0;
  const want = s.pos + 4 <= id.length ? be32(id, s.pos) : -1;
  if (adl !== want) { o.push("error: Adler-32 mismatch"); return; }

  const chans = ctype === 0 || ctype === 3 ? 1 : ctype === 2 ? 3 : ctype === 4 ? 2 : 4;
  const bppBits = chans * depth;
  const stride = Math.floor((w * bppBits + 7) / 8);
  const bpp = Math.max(1, (bppBits + 7) >> 3);
  let prow = new Uint8Array(stride), cur = new Uint8Array(stride);
  const fc = [0, 0, 0, 0, 0];
  const sum = [0, 0, 0, 0];
  let crc = 0xffffffff;
  const rgba = new Uint8Array(w * 4);
  let ip = 0;
  const maxv = depth >= 16 ? 0xffff : (1 << depth) - 1;
  const tv = [0, 0, 0];
  for (let i = 0; i < 3 && 2 * i + 1 < trns.length; i++) tv[i] = (trns[2 * i] << 8) | trns[2 * i + 1];
  const cv = (v: number) => (depth === 16 ? v >> 8 : depth === 8 ? v : Math.floor((v * 255) / maxv));
  const v = [0, 0, 0, 0];
  for (let y = 0; y < h; y++) {
    const ft = ip < outlen ? out[ip] : 0;
    ip++;
    if (ft < 5) fc[ft]++;
    for (let i = 0; i < stride; i++) cur[i] = ip + i < outlen ? out[ip + i] : 0;
    ip += stride;
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0;
      const b = prow[i];
      const c = i >= bpp ? prow[i - bpp] : 0;
      let x = cur[i];
      switch (ft) {
        case 1: x += a; break;
        case 2: x += b; break;
        case 3: x += (a + b) >> 1; break;
        case 4: x += paeth(a, b, c); break;
      }
      cur[i] = x & 0xff;
    }
    for (let x = 0; x < w; x++) {
      for (let ch = 0; ch < chans; ch++) {
        if (depth === 16) {
          const o2 = (x * chans + ch) * 2;
          v[ch] = (cur[o2] << 8) | cur[o2 + 1];
        } else if (depth === 8) {
          v[ch] = cur[x * chans + ch];
        } else {
          const bit = (x * chans + ch) * depth;
          v[ch] = (cur[bit >> 3] >> (8 - depth - (bit & 7))) & maxv;
        }
      }
      let r: number, g: number, b: number, a = 255;
      if (ctype === 3) {
        const i = v[0];
        r = i * 3 < pal.length ? pal[i * 3] : 0;
        g = i * 3 + 1 < pal.length ? pal[i * 3 + 1] : 0;
        b = i * 3 + 2 < pal.length ? pal[i * 3 + 2] : 0;
        a = i < trns.length ? trns[i] : 255;
      } else if (ctype === 0) {
        r = g = b = cv(v[0]);
        if (hastrns && trns.length >= 2 && v[0] === tv[0]) a = 0;
      } else if (ctype === 2) {
        r = cv(v[0]); g = cv(v[1]); b = cv(v[2]);
        if (hastrns && trns.length >= 6 && v[0] === tv[0] && v[1] === tv[1] && v[2] === tv[2]) a = 0;
      } else if (ctype === 4) {
        r = g = b = cv(v[0]); a = cv(v[1]);
      } else {
        r = cv(v[0]); g = cv(v[1]); b = cv(v[2]); a = cv(v[3]);
      }
      const q = x * 4;
      rgba[q] = r; rgba[q + 1] = g; rgba[q + 2] = b; rgba[q + 3] = a;
      sum[0] += r; sum[1] += g; sum[2] += b; sum[3] += a;
    }
    crc = crcUpdate(crc, rgba, 0, rgba.length);
    const t = prow; prow = cur; cur = t;
  }
  o.push(`IHDR ${w}x${h} depth=${depth} color=${ctype}`);
  o.push(`chunks ${chunkline}`);
  o.push(`deflate stored=${s.nstored} fixed=${s.nfixed} dynamic=${s.ndynamic}`);
  o.push(`filters none=${fc[0]} sub=${fc[1]} up=${fc[2]} average=${fc[3]} paeth=${fc[4]}`);
  o.push(`rgba sum=${sum[0]},${sum[1]},${sum[2]},${sum[3]} crc32=${((crc ^ 0xffffffff) >>> 0).toString(16).padStart(8, "0")}`);
}

function main() {
  const dir = process.argv[2];
  if (!dir) return;
  const names = fs.readdirSync(dir).filter((n) => n.endsWith(".png"));
  names.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
  const o: string[] = [];
  for (const n of names) process1(dir, n, o);
  process.stdout.write(o.length ? o.join("\n") + "\n" : "");
}
main();
