"""png_decode — decode a directory of PNG files with a hand-written inflate; report structure and pixel digests.

Hard parts: chunk walking + CRC-32, zlib/inflate (stored, fixed-Huffman and dynamic-Huffman blocks, several
blocks per stream, IDAT data split at arbitrary points incl. an empty IDAT), all five scanline filters (rows of
every filter type in every image, incl. Up/Average/Paeth on the first row), sub-byte depths packed MSB-first
with garbage padding bits, 16-bit samples, palette + short tRNS, gray/RGB tRNS keys compared at full depth,
Adler-32 verification, and a stated error for a bad CRC.

The oracle never decodes the pixels: expected RGBA comes from the source samples the generator encoded. The
only decoding in the oracle is a block-type walk of each deflate stream (its own inflate, checked against zlib).
"""
import json
import random
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = """\
Write a {LANG} program that decodes PNG images and reports facts about them.

The program is run with one argument: a directory. Process every file in it whose name ends in `.png`, in
ascending byte order of file name. You must decode the files yourself (PNG, ISO/IEC 15948 / W3C PNG
specification; zlib, RFC 1950; DEFLATE, RFC 1951) using only the standard library.

## Input facts
- Every file starts with the 8-byte PNG signature and is a sequence of chunks
  (`length` big-endian u32, 4-byte ASCII type, `length` data bytes, CRC-32 big-endian u32 computed over the type
  and data bytes), the first chunk being `IHDR` and the last `IEND`. Nothing follows `IEND`.
- Color types 0 (grayscale), 2 (RGB), 3 (palette), 4 (grayscale+alpha) and 6 (RGBA) occur, each with any
  bit depth the specification allows for it (1, 2, 4, 8 or 16). Interlace method is always 0. A tRNS chunk
  occurs only with color types 0, 2 and 3.
- Besides IHDR, PLTE, tRNS, IDAT and IEND, files contain other chunks (both standard ancillary chunks and
  private ones). They are listed in the output but otherwise ignored.
- The image data is the concatenation of the data of all IDAT chunks (the IDAT chunks are consecutive; some
  may have length 0). It is one zlib stream, which may consist of several DEFLATE blocks of any type.
- Each scanline starts with a filter-type byte (0 None, 1 Sub, 2 Up, 3 Average, 4 Paeth); every filter type
  can occur on any row, including the first (where the "previous row" is all zeros). Pixels narrower than a
  byte are packed most-significant bits first; each scanline is padded to a whole byte and the values of the
  padding bits are arbitrary.

## Errors
First check the CRC of every chunk, in file order. If a chunk's CRC does not match, the output for that file is
only its `== <file name>` line followed by `error: CRC mismatch in chunk <n> <TYPE>`, where `<n>` is the chunk's
1-based position in the file (IHDR is chunk 1) — the first bad chunk only. Otherwise decode the file; if the
Adler-32 checksum at the end of the zlib stream does not match the decompressed data, the output is only
the `== <file name>` line followed by `error: Adler-32 mismatch`. No other errors occur.

## Converting to 8-bit RGBA
Expand every pixel to 8-bit R, G, B, A:
- A 16-bit sample becomes its most significant byte (`v >> 8`).
- A grayscale sample of bit depth 1, 2 or 4 becomes `v * 255 / (2^depth - 1)` (this is always an integer).
- Grayscale (types 0 and 4): R = G = B = the gray value.
- Palette (type 3): R, G, B from the PLTE entry. A = the tRNS entry with the same index if the tRNS chunk has
  that many entries, otherwise 255.
- Types 0 and 2 without tRNS: A = 255. With tRNS: A = 0 for a pixel whose samples equal the tRNS value(s) —
  compared at the image's original bit depth, before any conversion — otherwise 255.
- Types 4 and 6: A is the alpha sample (converted as above).

The RGBA buffer is all pixels row by row from the top, left to right, 4 bytes each in the order R, G, B, A.

## Output
For each file:
```
== <file name>
IHDR <width>x<height> depth=<bit depth> color=<color type>
chunks <chunk types>
deflate stored=<s> fixed=<f> dynamic=<d>
filters none=<n0> sub=<n1> up=<n2> average=<n3> paeth=<n4>
rgba sum=<R>,<G>,<B>,<A> crc32=<crc>
```
- `chunks`: every chunk type in file order separated by single spaces, where a run of `k > 1` consecutive
  chunks of the same type is written `<TYPE>*<k>`.
- `deflate`: the number of DEFLATE blocks of each type (BTYPE 00, 01, 10) in the zlib stream.
- `filters`: the number of scanlines using each filter type.
- `rgba`: the sum over all pixels of each channel of the RGBA buffer (decimal), and the CRC-32 (the same CRC as
  PNG chunks use) of the whole RGBA buffer as 8 lowercase hex digits.

A file with an error prints just the `== <file name>` line and the error line. Print nothing between files.

Example output for a directory with three files:
```
== a.png
IHDR 3x5 depth=2 color=0
chunks IHDR gAMA tRNS IDAT*3 tEXt IEND
deflate stored=0 fixed=1 dynamic=0
filters none=1 sub=1 up=1 average=1 paeth=1
rgba sum=1275,1275,1275,3570 crc32=5c1b8b56
== b.png
error: CRC mismatch in chunk 4 IDAT
== c.png
error: Adler-32 mismatch
```
(Illustrative values.)
"""

SIG = b"\x89PNG\r\n\x1a\n"


# ── oracle-side inflate (block-type census) ───────────────────────────────────
class _Bits:
    def __init__(self, data):
        self.d, self.p, self.b, self.n = data, 0, 0, 0

    def get(self, k):
        while self.n < k:
            self.b |= self.d[self.p] << self.n
            self.p += 1
            self.n += 8
        v = self.b & ((1 << k) - 1)
        self.b >>= k
        self.n -= k
        return v

    def align(self):
        self.b, self.n = 0, 0


def _table(lengths):
    """canonical Huffman: dict (len, code) -> symbol"""
    mx = max(lengths) if lengths else 0
    count = [0] * (mx + 1)
    for l in lengths:
        if l:
            count[l] += 1
    nxt, code = [0] * (mx + 2), 0
    for b in range(1, mx + 1):
        code = (code + count[b - 1]) << 1 if b > 1 else 0
        nxt[b] = code
    t = {}
    for s, l in enumerate(lengths):
        if l:
            t[(l, nxt[l])] = s
            nxt[l] += 1
    return t


def _sym(bits, t):
    code, l = 0, 0
    while True:
        code = (code << 1) | bits.get(1)
        l += 1
        if (l, code) in t:
            return t[(l, code)]
        if l > 15:
            raise ValueError("bad code")


LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195,
         227, 258]
LEXT = [0] * 8 + [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [0]
DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
         4097, 6145, 8193, 12289, 16385, 24577]
DEXT = [0, 0, 0, 0] + [i // 2 for i in range(2, 28)]


def census_inflate(z: bytes):
    """returns (decompressed bytes, [btype per block]); z includes the 2-byte zlib header and Adler trailer"""
    bits = _Bits(z[2:])
    out, types = bytearray(), []
    while True:
        final, bt = bits.get(1), bits.get(2)
        types.append(bt)
        if bt == 0:
            bits.align()
            p = bits.p
            ln, nln = struct.unpack("<HH", bits.d[p:p + 4])
            assert ln ^ nln == 0xFFFF
            out += bits.d[p + 4:p + 4 + ln]
            bits.p = p + 4 + ln
        else:
            if bt == 1:
                lt = _table([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8)
                dt = _table([5] * 30)
            elif bt == 2:
                hl, hd, hc = bits.get(5) + 257, bits.get(5) + 1, bits.get(4) + 4
                order = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
                cl = [0] * 19
                for i in range(hc):
                    cl[order[i]] = bits.get(3)
                ct = _table(cl)
                lens = []
                while len(lens) < hl + hd:
                    s = _sym(bits, ct)
                    if s < 16:
                        lens.append(s)
                    elif s == 16:
                        lens += [lens[-1]] * (3 + bits.get(2))
                    elif s == 17:
                        lens += [0] * (3 + bits.get(3))
                    else:
                        lens += [0] * (11 + bits.get(7))
                lt, dt = _table(lens[:hl]), _table(lens[hl:])
            else:
                raise ValueError("btype 3")
            while True:
                s = _sym(bits, lt)
                if s < 256:
                    out.append(s)
                elif s == 256:
                    break
                else:
                    s -= 257
                    ln = LBASE[s] + bits.get(LEXT[s])
                    d = _sym(bits, dt)
                    dist = DBASE[d] + bits.get(DEXT[d])
                    for _ in range(ln):
                        out.append(out[-dist])
        if final:
            break
    return bytes(out), types


# ── encoding ─────────────────────────────────────────────────────────────────
def chunk(t: bytes, data: bytes, bad=False) -> bytes:
    crc = zlib.crc32(t + data)
    if bad:
        crc ^= 0x00010000
    return struct.pack(">I", len(data)) + t + data + struct.pack(">I", crc)


CH = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def pack_rows(samples, ct, depth, rng):
    """samples[y][x] = tuple; returns list of raw scanline bytes (no filter byte)."""
    rows = []
    for row in samples:
        if depth < 8:
            acc, nb, out = 0, 0, bytearray()
            for (v,) in row:
                acc = (acc << depth) | v
                nb += depth
                if nb == 8:
                    out.append(acc)
                    acc, nb = 0, 0
            if nb:
                pad = rng.randrange(1 << (8 - nb))   # arbitrary padding bits
                out.append((acc << (8 - nb)) | pad)
            rows.append(bytes(out))
        elif depth == 8:
            rows.append(bytes(c for px in row for c in px))
        else:
            rows.append(b"".join(struct.pack(">H", c) for px in row for c in px))
    return rows


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def filter_rows(rows, bpp, ftypes):
    out = bytearray()
    prev = bytes(len(rows[0]))
    for raw, ft in zip(rows, ftypes):
        out.append(ft)
        for i, x in enumerate(raw):
            a = raw[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            pred = [0, a, b, (a + b) // 2, paeth(a, b, c)][ft]
            out.append((x - pred) & 0xFF)
        prev = raw
    return bytes(out)


def stored_blocks(data, rng, final=True, empty=False):
    out = bytearray()
    pieces, i = [], 0
    while i < len(data):
        n = rng.randint(1, max(1, min(400, len(data) - i)))
        pieces.append(data[i:i + n])
        i += n
    if empty:
        pieces.insert(rng.randrange(len(pieces) + 1), b"")
    for k, p in enumerate(pieces):
        fin = 1 if (final and k == len(pieces) - 1) else 0
        out += bytes([fin]) + struct.pack("<HH", len(p), len(p) ^ 0xFFFF) + p
    return bytes(out)


def zlib_stream(data, mode, rng):
    ad = struct.pack(">I", zlib.adler32(data))
    if mode == "stored":
        return b"\x78\x01" + stored_blocks(data, rng, empty=True) + ad
    if mode == "fixed":
        co = zlib.compressobj(rng.choice([1, 6, 9]), zlib.DEFLATED, 15, 8, zlib.Z_FIXED)
        return co.compress(data) + co.flush()
    if mode == "dynamic":
        co = zlib.compressobj(9, zlib.DEFLATED, 15, 9, zlib.Z_DEFAULT_STRATEGY)
        return co.compress(data) + co.flush()
    # mixed: hand-made stored blocks, then a raw deflate tail with a sync flush (empty stored block) inside
    k = len(data) // 3
    co = zlib.compressobj(rng.choice([6, 9]), zlib.DEFLATED, -15, 8,
                          rng.choice([zlib.Z_DEFAULT_STRATEGY, zlib.Z_FIXED]))
    m = k + (len(data) - k) // 2
    body = stored_blocks(data[:k], rng, final=False)
    body += co.compress(data[k:m]) + co.flush(zlib.Z_SYNC_FLUSH) + co.compress(data[m:]) + co.flush()
    return b"\x78\xda" + body + ad


# ── image content ────────────────────────────────────────────────────────────
def smooth(rng, w, h, maxv, nch):
    """structured content: gradients + flat patches + some noise, so all block types/filters matter."""
    base = [rng.randrange(maxv + 1) for _ in range(nch)]
    gx = [rng.randint(-3, 3) for _ in range(nch)]
    gy = [rng.randint(-3, 3) for _ in range(nch)]
    scale = max(1, (maxv + 1) // 64)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            px = []
            for c in range(nch):
                if rng.random() < 0.25:
                    v = rng.randrange(maxv + 1)
                else:
                    v = (base[c] + (gx[c] * x + gy[c] * y) * scale) % (maxv + 1)
                px.append(v)
            row.append(tuple(px))
        rows.append(row)
    return rows


def make_image(kind, rng):
    w = rng.randint(5, 37)
    h = rng.randint(6, 22)
    plte = trns = None
    if kind == "gray_low":
        ct, depth = 0, rng.choice([1, 2, 4])
        while (w * depth) % 8 == 0:
            w += 1
        mx = (1 << depth) - 1
        s = smooth(rng, w, h, mx, 1)
        key = rng.randrange(mx + 1)
        trns = struct.pack(">H", key)
    elif kind == "gray16":
        ct, depth = 0, 16
        s = smooth(rng, w, h, 65535, 1)
        key = rng.randrange(65536)
        trns = struct.pack(">H", key)
        for _ in range(4):   # exact key and near misses (same high byte)
            s[rng.randrange(h)][rng.randrange(w)] = (key,)
            s[rng.randrange(h)][rng.randrange(w)] = (key ^ rng.randint(1, 255),)
    elif kind == "gray8a":
        ct, depth = 4, 8
        s = smooth(rng, w, h, 255, 2)
    elif kind == "pal_low":
        ct, depth = 3, rng.choice([1, 2, 4])
        while (w * depth) % 8 == 0:
            w += 1
        n = rng.randint(2, 1 << depth) if depth > 1 else 2
        plte = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(n)]
        s = [[(min(n - 1, v),) for (v,) in row] for row in smooth(rng, w, h, (1 << depth) - 1, 1)]
        trns = bytes(rng.randrange(256) for _ in range(rng.randint(1, max(1, n - 1))))
    elif kind == "pal8":
        ct, depth = 3, 8
        n = rng.randint(20, 200)
        plte = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(n)]
        s = [[(v % n,) for (v,) in row] for row in smooth(rng, w, h, 255, 1)]
        trns = bytes(rng.choice([0, 255, rng.randrange(256)]) for _ in range(rng.randint(3, n - 2)))
    elif kind == "rgb8":
        ct, depth = 2, 8
        s = smooth(rng, w, h, 255, 3)
        key = tuple(rng.randrange(256) for _ in range(3))
        trns = struct.pack(">HHH", *key)
        for _ in range(5):
            s[rng.randrange(h)][rng.randrange(w)] = key
            near = list(key)
            near[rng.randrange(3)] ^= rng.randint(1, 255)
            s[rng.randrange(h)][rng.randrange(w)] = tuple(near)
    elif kind == "rgb16":
        ct, depth = 2, 16
        s = smooth(rng, w, h, 65535, 3)
        key = tuple(rng.randrange(65536) for _ in range(3))
        trns = struct.pack(">HHH", *key)
        for _ in range(4):
            s[rng.randrange(h)][rng.randrange(w)] = key
            near = list(key)
            near[rng.randrange(3)] ^= rng.randint(1, 255)   # same high bytes, differs only in a low byte
            s[rng.randrange(h)][rng.randrange(w)] = tuple(near)
    elif kind == "rgba8":
        ct, depth = 6, 8
        s = smooth(rng, w, h, 255, 4)
    elif kind == "rgba16":
        ct, depth = 6, 16
        s = smooth(rng, w, h, 65535, 4)
    else:
        raise ValueError(kind)
    return dict(w=w, h=h, ct=ct, depth=depth, s=s, plte=plte, trns=trns)


def to_rgba(img) -> bytes:
    ct, depth, plte, trns = img["ct"], img["depth"], img["plte"], img["trns"]
    out = bytearray()

    def c8(v):
        if depth == 16:
            return v >> 8
        if depth < 8:
            return v * 255 // ((1 << depth) - 1)
        return v

    key = None
    if trns is not None and ct in (0, 2):
        key = struct.unpack(">" + "H" * (len(trns) // 2), trns)
    for row in img["s"]:
        for px in row:
            if ct == 3:
                i = px[0]
                r, g, b = plte[i]
                a = trns[i] if trns is not None and i < len(trns) else 255
            elif ct == 0:
                r = g = b = c8(px[0])
                a = 0 if key is not None and px == key else 255
            elif ct == 2:
                r, g, b = (c8(v) for v in px)
                a = 0 if key is not None and px == key else 255
            elif ct == 4:
                r = g = b = c8(px[0])
                a = c8(px[1])
            else:
                r, g, b, a = (c8(v) for v in px)
            out += bytes((r, g, b, a))
    return bytes(out)


ANC = ["gAMA", "pHYs", "tEXt", "tIME", "prVt", "vpAg", "iTXt", "sBIT"]


def anc_chunk(t, rng, img):
    if t == "gAMA":
        return struct.pack(">I", 45455)
    if t == "pHYs":
        return struct.pack(">IIB", 2835, 2835, 1)
    if t == "tEXt":
        return b"Comment\x00" + bytes(rng.choice(b"abcdefgh IDAT IEND") for _ in range(rng.randint(0, 30)))
    if t == "tIME":
        return struct.pack(">HBBBBB", 2026, rng.randint(1, 12), rng.randint(1, 28), 1, 2, 3)
    if t == "iTXt":
        return b"Title\x00\x00\x00\x00\x00" + "pngé".encode()
    if t == "sBIT":
        n = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[img["ct"]]
        return bytes([min(img["depth"], 8)] * n)
    return bytes(rng.randrange(256) for _ in range(rng.randint(0, 20)))


def build_png(img, mode, rng, corrupt=None):
    """returns (file bytes, facts dict) ; corrupt in (None, 'crc', 'adler')"""
    ct, depth, w, h = img["ct"], img["depth"], img["w"], img["h"]
    bitspp = CH[ct] * depth
    bpp = max(1, bitspp // 8)
    ft = [0, 1, 2, 3, 4, 4] + [rng.randrange(5) for _ in range(h - 6)]
    rng.shuffle(ft)
    first = rng.choice([2, 3, 4])            # make the first row use a filter that reads the "previous row"
    j = ft.index(first)
    ft[0], ft[j] = ft[j], ft[0]
    if depth == 8:
        # Paeth ties with distinct candidates so the tie-break order matters:
        # pa == pc with a != c  <=>  a = 3c - 2b;   pb == pc with b != c  <=>  a = (3c - b) / 2
        s = img["s"]
        nch = CH[ct]
        top = (len(img["plte"]) - 1) if ct == 3 else 255
        for y in range(1, h):
            if ft[y] != 4:
                continue
            for _ in range(max(2, w // 3)):
                x = rng.randrange(1, w)
                ch = rng.randrange(nch)
                b, c = s[y - 1][x][ch], s[y - 1][x - 1][ch]
                a = 3 * c - 2 * b if rng.random() < 0.5 else (3 * c - b) // 2 if (3 * c - b) % 2 == 0 else -1
                if 0 <= a <= top and b != c:
                    px = list(s[y][x - 1])
                    px[ch] = a
                    s[y][x - 1] = tuple(px)
    rows = pack_rows(img["s"], ct, depth, rng)
    raw = filter_rows(rows, bpp, ft)
    z = zlib_stream(raw, mode, rng)
    dec, types = census_inflate(z)
    assert dec == raw == zlib.decompress(z), "oracle inflate disagrees with zlib"
    if corrupt == "adler":
        z = z[:-4] + bytes([z[-4] ^ 0x5A]) + z[-3:]
    # split into IDATs
    k = rng.randint(1, 7)
    cuts = sorted(rng.sample(range(1, len(z)), min(k - 1, len(z) - 1)))
    parts = [z[a:b] for a, b in zip([0] + cuts, cuts + [len(z)])]
    if rng.random() < 0.5:
        parts.insert(rng.randrange(1, len(parts) + 1), b"")
    chunks = [(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ct, 0, 0, 0))]
    pre = rng.sample(["gAMA", "sBIT", "prVt", "pHYs"], rng.randint(1, 2))
    for t in pre:
        chunks.append((t.encode(), anc_chunk(t, rng, img)))
    if img["plte"]:
        chunks.append((b"PLTE", b"".join(bytes(c) for c in img["plte"])))
    if img["trns"] is not None:
        chunks.append((b"tRNS", img["trns"]))
    if rng.random() < 0.5:
        chunks.append((b"vpAg", anc_chunk("vpAg", rng, img)))
    chunks += [(b"IDAT", p) for p in parts]
    for t in rng.sample(["tEXt", "tIME", "iTXt"], rng.randint(0, 2)):
        chunks.append((t.encode(), anc_chunk(t, rng, img)))
    chunks.append((b"IEND", b""))
    bad_idx = None
    if corrupt == "crc":
        # first bad chunk: an ancillary chunk before the image data (so a decoder that checks only critical
        # chunks, or only the chunks it uses, gets it wrong); a non-empty IDAT later is damaged as well
        anc = [i for i, (t, d) in enumerate(chunks) if i > 0 and t[:1].islower() and i < len(chunks) - 2
               and all(tt != b"IDAT" for tt, _ in chunks[:i])]
        bad_idx = rng.choice(anc)
        idats = [i for i, (t, d) in enumerate(chunks) if t == b"IDAT" and d]
        second = rng.choice(idats)
    blob = bytearray(SIG)
    for i, (t, d) in enumerate(chunks):
        if corrupt == "crc" and (i == second or (i == bad_idx and d and rng.random() < 0.6)):
            dd = bytearray(d)
            dd[rng.randrange(len(dd))] ^= 1 << rng.randrange(8)   # damaged data, stale CRC
            blob += struct.pack(">I", len(d)) + t + bytes(dd) + struct.pack(">I", zlib.crc32(t + d))
        elif corrupt == "crc" and i == bad_idx:
            blob += chunk(t, d, bad=True)
        else:
            blob += chunk(t, d)
    names = [t.decode() for t, _ in chunks]
    runs = []
    for n in names:
        if runs and runs[-1][0] == n:
            runs[-1][1] += 1
        else:
            runs.append([n, 1])
    rgba = to_rgba(img)
    facts = dict(
        ihdr=f"IHDR {w}x{h} depth={depth} color={ct}",
        chunks="chunks " + " ".join(n if c == 1 else f"{n}*{c}" for n, c in runs),
        deflate="deflate stored=%d fixed=%d dynamic=%d" % tuple(types.count(i) for i in range(3)),
        filters="filters none=%d sub=%d up=%d average=%d paeth=%d" % tuple(ft.count(i) for i in range(5)),
        rgba="rgba sum=%d,%d,%d,%d crc32=%08x" % (tuple(sum(rgba[c::4]) for c in range(4)) + (zlib.crc32(rgba),)),
        types=types, bad=(bad_idx, names[bad_idx]) if bad_idx is not None else None,
    )
    return bytes(blob), facts, rgba


KINDS = ["gray_low", "gray16", "gray8a", "pal_low", "pal8", "rgb8", "rgb16", "rgba8", "rgba16"]


def make_case(seed):
    rng = random.Random(81_000 + seed)
    kinds = KINDS[:]
    rng.shuffle(kinds)
    while True:
        modes = ["stored", "fixed", "dynamic", "mixed"] + [rng.choice(["fixed", "dynamic", "stored", "mixed"])
                                                           for _ in range(len(kinds) - 4)]
        rng.shuffle(modes)
        files, census = [], [0, 0, 0]
        for kind, mode in zip(kinds, modes):
            img = make_image(kind, rng)
            blob, facts, rgba = build_png(img, mode, rng)
            for t in facts["types"]:
                census[t] += 1
            files.append((kind, img, blob, facts, rgba, None))
        if all(census):
            break
    for corrupt in ("crc", "adler"):
        img = make_image(rng.choice(KINDS), rng)
        blob, facts, rgba = build_png(img, rng.choice(["fixed", "dynamic", "mixed"]), rng, corrupt=corrupt)
        files.append((corrupt, img, blob, facts, rgba, corrupt))
    order = list(range(len(files)))
    rng.shuffle(order)
    named = []
    for pos, i in enumerate(order):
        # byte order: upper-case stems sort before lower-case ones
        stem = ["Pic", "a", "Z"][pos] if pos < 3 else rng.choice(["img", "Pic", "scan", "z", "a", "IMG"])
        named.append((f"{stem}{pos:02d}_{rng.randrange(100)}.png", files[i]))
    named.sort(key=lambda t: t[0].encode())
    out = []
    for name, (kind, img, blob, facts, rgba, corrupt) in named:
        out.append(f"== {name}")
        if corrupt == "crc":
            n, t = facts["bad"]
            out.append(f"error: CRC mismatch in chunk {n + 1} {t}")
        elif corrupt == "adler":
            out.append("error: Adler-32 mismatch")
        else:
            out += [facts[k] for k in ("ihdr", "chunks", "deflate", "filters", "rgba")]
    return named, "\n".join(out) + "\n"


class Challenge(H.Challenge):
    name = "png_decode"
    title = "Decode PNG files (inflate, filters, palette/tRNS, sub-byte and 16-bit depths)"
    category = "binary"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        named, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        for name, f in named:
            (d / name).write_bytes(f[2])
        (d / "README.txt").write_text("not a png\n")
        return H.Case(args=[str(d)], expected=exp)
