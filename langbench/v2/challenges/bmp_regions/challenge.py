"""bmp_regions — parse BMP variants and report the 8-connected "ink" components of each image.

Hard parts: three DIB header sizes (40 / 108 / 124), bottom-up vs top-down (negative height), 4-byte row
padding, 1/4/8-bit palettes (count from biClrUsed or 2^bpp, entries BGRx, palettes NOT in luminance order,
index 0 not necessarily dark), pixel data located by bfOffBits after a gap, 32-bit BI_RGB with a junk X byte,
BI_BITFIELDS with masks after a 40-byte header vs inside a V4/V5 header, masks in unusual positions and with
widths other than 8 (exact rescaling rule), V5 BI_RGB files whose header masks hold junk that must be ignored,
an exact integer luminance threshold with colours right at it, 8-connectivity (diagonal-only links), a long
1-pixel snake (deep recursion), and a stated error line for RLE compression.

The oracle never parses BMP: it works from the logical pixels the generator encoded, with its own
union-find labelling.
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = """\
Write a {LANG} program that reads BMP images and finds the connected regions of dark ("ink") pixels.

The program is run with one argument: a directory. Process every file in it whose name ends in `.bmp`, in
ascending byte order of file name. All multi-byte integers in a BMP are little-endian.

## The BMP format (as it occurs here)
- File header, 14 bytes: `"BM"`, u32 file size, u16 reserved, u16 reserved, u32 `bfOffBits` = the byte
  offset of the pixel data from the start of the file. The pixel data does not necessarily start right after
  the palette; always use `bfOffBits`.
- Then the DIB header, starting with its u32 size, which is 40 (BITMAPINFOHEADER), 108 (BITMAPV4HEADER) or 124
  (BITMAPV5HEADER). Its first 40 bytes are always: u32 size, i32 width, i32 height, u16 planes (1),
  u16 bits per pixel, u32 compression, u32 image size, i32 x pixels/metre, i32 y pixels/metre, u32 colours used,
  u32 important colours. In the 108- and 124-byte headers the next 16 bytes are the u32 red, green, blue and
  alpha masks; everything after them is irrelevant here.
- A positive height means the rows are stored bottom-up (the first stored row is the bottom row of the
  image); a negative height means top-down, and the image height is its absolute value.
- Bits per pixel is 1, 4, 8, 24 or 32. Each stored row is padded to a multiple of 4 bytes.
- 1, 4 and 8 bits per pixel: each pixel is an index into the colour table, which starts immediately after the
  DIB header. It has `colours used` entries, or 2^bpp entries if that field is 0. Each entry is 4 bytes:
  blue, green, red, unused. In 1- and 4-bit rows the leftmost pixel is in the most significant bits of a byte.
  The table is in no particular order.
- 24 bits per pixel: 3 bytes per pixel in the order blue, green, red.
- 32 bits per pixel with compression 0 (BI_RGB): 4 bytes per pixel blue, green, red, unused. Any mask
  fields in a 108/124-byte header are ignored in this case.
- 32 bits per pixel with compression 3 (BI_BITFIELDS): each pixel is a u32 and the red, green and blue values
  are extracted with the red/green/blue masks. With a 40-byte header the three masks (red, green, blue, each
  u32) immediately follow the header; with a 108/124-byte header they are the header's mask fields. Each mask
  is one contiguous run of 1 to 16 set bits, and the masks do not overlap; any alpha mask is ignored. A
  channel's value is `v = (pixel & mask) >> s`, where `s` is the number of trailing zero bits of the mask; with
  `n` = the number of set bits of the mask and `m = 2^n - 1`, its 8-bit value is `(v * 255 + m / 2) / m`
  (integer division throughout; for 8-bit masks this is just `v`).
- Compression 3 only occurs with 32 bits per pixel. Any compression value other than 0 and 3 (for example
  1 = BI_RLE8, 2 = BI_RLE4) is unsupported.

## Ink and regions
A pixel with 8-bit colour (R, G, B) is ink iff `(299*R + 587*G + 114*B) / 1000 < 128` (integer division).
Pixel coordinates are (x, y) with (0, 0) at the top-left of the image as displayed and y growing downwards
(so in a bottom-up file the first stored row is y = height - 1). Two ink pixels are connected if they touch
horizontally, vertically or diagonally (8-connectivity); a component is a maximal set of connected ink pixels.

## Output
For each file, in order:
```
== <file name>
size <width>x<height> bpp <bits per pixel> header <DIB header size> <bottom-up|top-down> ink <ink pixels>
components <number of components>
<x>,<y> bbox <minx>,<miny>-<maxx>,<maxy> pixels <count>
...
```
with one line per component, where `<x>,<y>` is the component's first pixel in reading order (smallest y,
then smallest x among those), `bbox` is its inclusive bounding box and `pixels` its pixel count. Components
are listed in order of their first pixel (by y, then x). `<height>` is always positive.

A file with an unsupported compression prints only two lines: `== <file name>` and
`error: unsupported compression <value>` (the decimal compression value).

Example output:
```
== a.bmp
size 7x4 bpp 24 header 40 bottom-up ink 8
components 2
0,0 bbox 0,0-2,2 pixels 4
5,1 bbox 4,1-6,3 pixels 4
== b.bmp
error: unsupported compression 1
```
"""


def lum_ink(r, g, b):
    return (299 * r + 587 * g + 114 * b) // 1000 < 128


def to8(v, n):
    m = (1 << n) - 1
    return (v * 255 + m // 2) // m


# ── colour pickers ───────────────────────────────────────────────────────────
def pick_rgb(rng, ink):
    """8-bit colour of the requested class; often right at the threshold."""
    while True:
        mode = rng.random()
        if mode < 0.35:            # near the boundary: 127000 <= sum < 129000
            g = rng.randrange(256)
            r = rng.randrange(256)
            rest = 128000 - 299 * r - 587 * g + rng.randint(-1000, 999)
            if rest < 0 or rest // 114 > 255:
                continue
            b = rest // 114
        elif mode < 0.45:          # exact grey at 127 / 128
            r = g = b = 127 if ink else 128
        else:
            r, g, b = (rng.randrange(256) for _ in range(3))
        if lum_ink(r, g, b) == ink:
            return (r, g, b)


def pick_fields(rng, ink, widths):
    """raw bitfield values whose rescaled colour is of the requested class."""
    while True:
        raw = tuple(rng.randrange(1 << n) for n in widths)
        c8 = tuple(to8(v, n) for v, n in zip(raw, widths))
        s = 299 * c8[0] + 587 * c8[1] + 114 * c8[2]
        if rng.random() < 0.7 and abs(s - 128000) > 6000:
            continue
        if lum_ink(*c8) == ink:
            return raw, c8


# ── ink masks ────────────────────────────────────────────────────────────────
def ink_mask(rng, w, h):
    m = [[False] * w for _ in range(h)]

    def put(x, y):
        if 0 <= x < w and 0 <= y < h:
            m[y][x] = True

    for _ in range(rng.randint(3, 6)):                 # filled rectangles / rings with holes
        x0, y0 = rng.randrange(w), rng.randrange(h)
        x1, y1 = min(w - 1, x0 + rng.randint(1, 12)), min(h - 1, y0 + rng.randint(1, 9))
        ring = rng.random() < 0.5
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if not ring or x in (x0, x1) or y in (y0, y1):
                    put(x, y)
    for _ in range(rng.randint(2, 4)):                 # diagonal strokes (8-connected only)
        x, y = rng.randrange(w), rng.randrange(h)
        dx = rng.choice([-1, 1])
        for _ in range(rng.randint(3, 15)):
            put(x, y)
            x += dx
            y += 1
    for _ in range(rng.randint(4, 12)):                # specks
        put(rng.randrange(w), rng.randrange(h))
    for _ in range(3):                                 # pairs touching only at a corner
        x, y = rng.randrange(w - 1), rng.randrange(h - 1)
        put(x, y)
        put(x + 1, y + 1)
    for x in range(w):                                 # something touching the borders
        if rng.random() < 0.15:
            put(x, 0)
            put(x, h - 1)
    return m


def add_snake(m, rng):
    """a long 1-pixel serpentine (deep recursion for naive flood fill)"""
    h, w = len(m), len(m[0])
    x0 = rng.randrange(0, max(1, w // 4))
    x1 = w - 1 - rng.randrange(0, max(1, w // 4))
    y = rng.randrange(0, 3)
    left = True
    while y < h - 1:
        for x in range(x0, x1 + 1):
            m[y][x] = True
        for yy in range(y + 1, min(h, y + 3)):
            m[yy][x0 if not left else x1] = True
        for x in range(x0, x1 + 1):                    # keep the gap row clear
            if y + 1 < h and x not in (x0, x1):
                m[y + 1][x] = False
        left = not left
        y += 2


# ── BMP writing ──────────────────────────────────────────────────────────────
def row_bytes(bpp, w):
    return ((w * bpp + 31) // 32) * 4


def dib(hsize, w, hsign, bpp, comp, clr_used, size_img, masks, rng):
    base = struct.pack("<iiHHIIiiII", w, hsign, 1, bpp, comp, size_img, 2835, 2835, clr_used, 0)
    if hsize == 40:
        return struct.pack("<I", 40) + base
    extra = struct.pack("<IIII", *masks)
    cstype = b"BGRs" if hsize == 124 else bytes(4)              # LCS_sRGB (V5) / LCS_CALIBRATED_RGB (V4)
    extra += cstype + bytes(36) + bytes(12)                     # CSType, endpoints, gamma  (V4: 108 total)
    if hsize == 124:
        extra += struct.pack("<IIII", 4, 0, 0, 0)               # intent, profile data, size, reserved
    out = struct.pack("<I", hsize) + base + extra
    assert len(out) == hsize, (len(out), hsize)
    return out


def bmp_file(hdr, after_hdr, pixel_rows, rng, force_gap=False):
    gap = bytes(rng.randrange(256) for _ in range(rng.choice([2, 7, 33] if force_gap else [0, 0, 2, 7, 33])))
    off = 14 + len(hdr) + len(after_hdr) + len(gap)
    data = b"".join(pixel_rows)
    size = off + len(data)
    return b"BM" + struct.pack("<IHHI", size, 0, 0, off) + hdr + after_hdr + gap + data


def pad(b, n):
    return b + bytes(n - len(b))


def encode(kind, rng, w, h, mask):
    """returns (file bytes, rgb rows (logical, top-down), info dict)"""
    top_down = rng.random() < 0.5
    rgb = [[None] * w for _ in range(h)]
    pal = None
    if kind in ("pal1", "pal4", "pal8"):
        bpp = {"pal1": 1, "pal4": 4, "pal8": 8}[kind]
        if bpp == 1:
            pal = [pick_rgb(rng, False), pick_rgb(rng, True)]
            rng.shuffle(pal)
            clr_used = rng.choice([0, 2])
        else:
            n = rng.randint(5, 16) if bpp == 4 else rng.randint(12, 60)
            clr_used = 0 if (bpp == 4 and rng.random() < 0.5) else n
            if clr_used == 0:
                n = 1 << bpp
            pal = [pick_rgb(rng, i % 2 == 0) for i in range(n)]
            rng.shuffle(pal)                                     # not in luminance order
        inks = [i for i, c in enumerate(pal) if lum_ink(*c)]
        papers = [i for i, c in enumerate(pal) if not lum_ink(*c)]
        idx = [[rng.choice(inks if mask[y][x] else papers) for x in range(w)] for y in range(h)]
        for y in range(h):
            for x in range(w):
                rgb[y][x] = pal[idx[y][x]]
        rb = row_bytes(bpp, w)
        rows = []
        for y in range(h):
            if bpp == 8:
                r = bytes(idx[y])
            else:
                per = 8 // bpp
                r = bytearray()
                for x0 in range(0, w, per):
                    v = 0
                    for k in range(per):
                        v = (v << bpp) | (idx[y][x0 + k] if x0 + k < w else rng.randrange(1 << bpp))
                    r.append(v)
            rows.append(pad(bytes(r), rb))
        hsize = {"pal1": rng.choice([40, 108]), "pal4": 40, "pal8": 124}[kind]
        table = b"".join(bytes((b, g, r, rng.choice([0, 0, 255]))) for r, g, b in pal)
        hdr = dib(hsize, w, -h if top_down else h, bpp, 0, clr_used, rb * h, (0, 0, 0, 0), rng)
        after = table
        comp = 0
    elif kind == "rgb24":
        bpp, hsize, comp = 24, rng.choice([40, 40, 124]), 0
        rb = row_bytes(24, w)
        rows = []
        for y in range(h):
            r = bytearray()
            for x in range(w):
                c = pick_rgb(rng, mask[y][x])
                rgb[y][x] = c
                r += bytes((c[2], c[1], c[0]))
            rows.append(pad(bytes(r), rb))
        hdr = dib(hsize, w, -h if top_down else h, 24, 0, 0, rng.choice([0, rb * h]), (0, 0, 0, 0), rng)
        after = b""
    elif kind == "rgb32":
        bpp, comp = 32, 0
        hsize = 124
        rows = []
        for y in range(h):
            r = bytearray()
            for x in range(w):
                c = pick_rgb(rng, mask[y][x])
                rgb[y][x] = c
                r += bytes((c[2], c[1], c[0], rng.randrange(256)))
            rows.append(bytes(r))
        junk = (0xFF000000, 0x00FF0000, 0x0000FF00, 0x000000FF)   # misleading masks: must be ignored
        hdr = dib(hsize, w, -h if top_down else h, 32, 0, 0, 4 * w * h, junk, rng)
        after = b""
    elif kind in ("bf32_40", "bf32_v"):
        bpp, comp = 32, 3
        if kind == "bf32_40":
            widths = [8, 8, 8]
            hsize = 40
        else:
            widths = rng.choice([[10, 10, 10], [5, 6, 5], [11, 11, 10], [6, 5, 12], [3, 9, 7]])
            hsize = rng.choice([108, 124])
        # place the three fields (and maybe an alpha field) at shuffled, non-overlapping positions
        order = [0, 1, 2]
        rng.shuffle(order)
        masks = [0, 0, 0]
        pos = rng.randint(0, 32 - sum(widths))
        free = 32 - sum(widths) - pos
        for ch in order:
            masks[ch] = ((1 << widths[ch]) - 1) << pos
            pos += widths[ch]
            if free and rng.random() < 0.5:
                g = rng.randint(0, free)
                pos += g
                free -= g
        used = masks[0] | masks[1] | masks[2]
        amask = 0
        for bit in range(32):
            if not used >> bit & 1 and rng.random() < 0.5:
                amask |= 1 << bit
        rows = []
        for y in range(h):
            r = bytearray()
            for x in range(w):
                raw, c8 = pick_fields(rng, mask[y][x], widths)
                rgb[y][x] = c8
                p = 0
                for ch in range(3):
                    shift = (masks[ch] & -masks[ch]).bit_length() - 1
                    p |= raw[ch] << shift
                p |= rng.getrandbits(32) & ~used & 0xFFFFFFFF        # junk in unused / alpha bits
                r += struct.pack("<I", p)
            rows.append(bytes(r))
        hdr = dib(hsize, w, -h if top_down else h, 32, 3, 0, 4 * w * h, (*masks, amask), rng)
        after = struct.pack("<III", *masks) if hsize == 40 else b""
    else:
        raise ValueError(kind)
    stored = rows if top_down else rows[::-1]
    blob = bmp_file(hdr, after, stored, rng, force_gap=kind in ("pal8", "bf32_40"))
    return blob, rgb, dict(bpp=bpp, hsize=hsize, top_down=top_down, comp=comp)


def rle_file(rng):
    """a valid BI_RLE8 (or RLE4) image — reported as unsupported"""
    w, h = rng.randint(8, 30), rng.randint(5, 20)
    comp = rng.choice([1, 1, 2])
    bpp = 8 if comp == 1 else 4
    data = bytearray()
    for y in range(h):
        x = 0
        while x < w:
            n = min(w - x, rng.randint(1, 9))
            data += bytes((n, rng.randrange(16) * (17 if bpp == 4 else 1)))
            x += n
        data += b"\x00\x00"
    data += b"\x00\x01"
    pal = b"".join(bytes((rng.randrange(256), rng.randrange(256), rng.randrange(256), 0)) for _ in range(16))
    hdr = dib(rng.choice([40, 124]), w, h, bpp, comp, 16, len(data), (0, 0, 0, 0), rng)
    return bmp_file(hdr, pal, [bytes(data)], rng), comp


# ── oracle: union-find labelling ─────────────────────────────────────────────
def components(ink, w, h):
    parent = list(range(w * h))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for y in range(h):
        for x in range(w):
            if not ink[y][x]:
                continue
            for dx, dy in ((-1, 0), (-1, -1), (0, -1), (1, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and ink[ny][nx]:
                    ra, rb = find(y * w + x), find(ny * w + nx)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    comps = {}
    for y in range(h):
        for x in range(w):
            if ink[y][x]:
                r = find(y * w + x)
                if r not in comps:
                    comps[r] = [x, y, x, y, x, y, 0]
                c = comps[r]
                c[2], c[3], c[4], c[5] = min(c[2], x), min(c[3], y), max(c[4], x), max(c[5], y)
                c[6] += 1
    return sorted(comps.values(), key=lambda c: (c[1], c[0]))


KINDS = ["rgb24", "rgb32", "bf32_40", "bf32_v", "pal1", "pal4", "pal8"]


def make_case(seed):
    rng = random.Random(77_000 + seed)
    files = []
    snake_kind = rng.choice(KINDS)
    for kind in KINDS:
        w, h = rng.randint(17, 70), rng.randint(12, 48)
        if kind == snake_kind:
            w, h = rng.randint(60, 90), rng.randint(40, 60)
        while kind in ("rgb24", "pal1", "pal4", "pal8") and (w * {"rgb24": 24, "pal1": 1, "pal4": 4,
                                                                   "pal8": 8}[kind]) % 32 == 0:
            w += 1
        mask = ink_mask(rng, w, h)
        if kind == snake_kind:
            add_snake(mask, rng)
        blob, rgb, info = encode(kind, rng, w, h, mask)
        ink = [[lum_ink(*rgb[y][x]) for x in range(w)] for y in range(h)]
        assert ink == mask
        comps = components(ink, w, h)
        lines = [f"size {w}x{h} bpp {info['bpp']} header {info['hsize']} "
                 f"{'top-down' if info['top_down'] else 'bottom-up'} ink {sum(map(sum, ink))}",
                 f"components {len(comps)}"]
        lines += [f"{c[0]},{c[1]} bbox {c[2]},{c[3]}-{c[4]},{c[5]} pixels {c[6]}" for c in comps]
        files.append((blob, lines, (kind, rgb)))
    blob, comp = rle_file(rng)
    files.append((blob, [f"error: unsupported compression {comp}"], ("rle", None)))
    rng.shuffle(files)
    stems = ["Scan", "img", "IMG", "page", "b", "Z", "x"]
    named = []
    for i, f in enumerate(files):
        named.append((f"{rng.choice(stems)}_{i:02d}.bmp", f))
    named.sort(key=lambda t: t[0].encode())
    out = []
    for name, (blob, lines, _) in named:
        out += [f"== {name}"] + lines
    return named, "\n".join(out) + "\n"


class Challenge(H.Challenge):
    name = "bmp_regions"
    title = "Parse BMP variants and label 8-connected ink regions"
    category = "binary"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        named, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        for name, (blob, _, _) in named:
            (d / name).write_bytes(blob)
        (d / "notes.txt").write_text("not a bitmap\n")
        return H.Case(args=[str(d)], expected=exp)
