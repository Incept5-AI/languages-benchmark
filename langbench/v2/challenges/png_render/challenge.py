"""png_render — rasterise a scene with exact integer coverage and blending rules, write it as a PNG.

Hard parts: a JSON reader (any key order/whitespace, negative numbers), exact integer coverage tests (circle,
inclusive edge functions independent of winding, capsule distance without division — needs 64-bit), clipping
of shapes that extend past the canvas, the rounding blend, and above all writing a valid PNG with only the
standard library: CRC-32, zlib header + Adler-32, and DEFLATE (stored blocks are fine, but each holds at most
65535 bytes and every image here needs more than one). The checker decodes the PNG strictly with its own code
(Python zlib only for the inflate itself) and compares every pixel, so any valid filters/compression pass.
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
Write a {LANG} program that renders a 2-D scene and saves it as a PNG image.

The program is run with one argument: a directory containing `scene.json`. It must write the image to
`out.png` in the current working directory and print a summary to standard output.

## The scene
```json
{"width": 200, "height": 150, "background": [255, 255, 255],
 "shapes": [
   {"type": "rect", "x": -5, "y": 10, "w": 40, "h": 20, "color": [255, 0, 0, 255]},
   {"type": "circle", "cx": 50, "cy": 60, "r": 25, "color": [0, 0, 255, 128]},
   {"type": "triangle", "points": [[10, 10], [80, 30], [30, 90]], "color": [0, 160, 0, 200]},
   {"type": "line", "x1": 0, "y1": 149, "x2": 199, "y2": 0, "r": 3, "color": [20, 20, 20, 77]}
 ]}
```
Objects may list their keys in any order and the file may use any JSON whitespace. All numbers are integers
(coordinates may be negative or lie far outside the canvas; `|coordinate| <= 4000`, `0 <= r <= 4000`,
`0 <= w, h`). Colours are `[R, G, B]` (background) or `[R, G, B, A]` (shapes), each 0–255. The triangle's
three points are never collinear. There are at most 100 shapes, and `1 <= width, height <= 1000`.

## Rendering
The canvas has `width` × `height` pixels, x growing to the right and y downwards, (0, 0) at the top left. For
the coverage tests, pixel (x, y) is the point with integer coordinates (x, y). Every pixel starts as the
background colour. Shapes are drawn in the order listed; each shape changes exactly the canvas pixels it
covers (parts outside the canvas are simply not drawn):
- `rect` covers (x, y) iff `rx <= x < rx + w` and `ry <= y < ry + h` (where rx, ry are the shape's `x`, `y`).
- `circle` covers (x, y) iff `(x - cx)² + (y - cy)² <= r²`.
- `triangle` with points P0, P1, P2: for each edge i = 0, 1, 2 from Pi = (xi, yi) to Pj (j = (i + 1) mod 3)
  let `Ei = (xj - xi) * (y - yi) - (yj - yi) * (x - xi)`. It covers (x, y) iff E0, E1, E2 are all `>= 0` or
  all `<= 0` (edges and vertices are included, for either winding order).
- `line` from A = (x1, y1) to B = (x2, y2) with radius r covers the points within distance r of the segment
  AB. Exactly: let `dx = x2 - x1`, `dy = y2 - y1`, `L = dx² + dy²`, `t = (x - x1)*dx + (y - y1)*dy`.
  If `t <= 0` it covers iff `(x - x1)² + (y - y1)² <= r²`; else if `t >= L` it covers iff
  `(x - x2)² + (y - y2)² <= r²`; otherwise let `c = (x - x1)*dy - (y - y1)*dx` and it covers iff
  `c² <= r² * L`. (A and B may coincide.)

All of these are exact integer computations; every intermediate value fits in a signed 64-bit integer, but
some exceed 32 bits. Do not use floating-point arithmetic that can round (every value also stays below 2^53, so a JavaScript `number` holds it exactly).

A covered pixel with current colour (dR, dG, dB) drawn with shape colour (sR, sG, sB, A) becomes, per channel,
`(s * A + d * (255 - A) + 127) / 255` with integer (floor) division. A shape with A = 0 changes nothing but
still covers its pixels.

## Output
`out.png`: a valid PNG of exactly `width` × `height` pixels, bit depth 8, colour type 2 (RGB, no alpha),
interlace method 0, holding the final canvas. Any valid filter types and any valid zlib/DEFLATE encoding are
accepted (for example uncompressed "stored" DEFLATE blocks), and ancillary chunks are allowed. The checker
decodes it strictly (signature, chunk order and lengths, every CRC-32, the zlib header and Adler-32, filter
bytes) and compares every pixel.

Standard output: one line per shape in order, `<index> <type> <n>`, where index counts from 1 and `<n>` is
the number of canvas pixels the shape covers; then a line `colors <k>`, where `<k>` is the number of distinct
RGB colours in the final image.

## Worked example
For this scene:
```json
@EXAMPLE_SCENE@
```
the standard output is:
```
@EXAMPLE_OUT@
```
and some of the final pixels are:
```
@EXAMPLE_PIXELS@
```
"""


# ── oracle rasteriser ────────────────────────────────────────────────────────
def covers(s, x, y):
    t = s["type"]
    if t == "rect":
        return s["x"] <= x < s["x"] + s["w"] and s["y"] <= y < s["y"] + s["h"]
    if t == "circle":
        return (x - s["cx"]) ** 2 + (y - s["cy"]) ** 2 <= s["r"] ** 2
    if t == "triangle":
        (x0, y0), (x1, y1), (x2, y2) = s["points"]
        e = [(xb - xa) * (y - ya) - (yb - ya) * (x - xa)
             for (xa, ya), (xb, yb) in (((x0, y0), (x1, y1)), ((x1, y1), (x2, y2)), ((x2, y2), (x0, y0)))]
        return min(e) >= 0 or max(e) <= 0
    if t == "line":
        x1, y1, x2, y2, r = s["x1"], s["y1"], s["x2"], s["y2"], s["r"]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        tt = (x - x1) * dx + (y - y1) * dy
        if tt <= 0:
            return (x - x1) ** 2 + (y - y1) ** 2 <= r * r
        if tt >= L:
            return (x - x2) ** 2 + (y - y2) ** 2 <= r * r
        c = (x - x1) * dy - (y - y1) * dx
        return c * c <= r * r * L
    raise ValueError(t)


def bbox(s):
    t = s["type"]
    if t == "rect":
        return s["x"], s["y"], s["x"] + s["w"] - 1, s["y"] + s["h"] - 1
    if t == "circle":
        return s["cx"] - s["r"], s["cy"] - s["r"], s["cx"] + s["r"], s["cy"] + s["r"]
    if t == "triangle":
        xs = [p[0] for p in s["points"]]
        ys = [p[1] for p in s["points"]]
        return min(xs), min(ys), max(xs), max(ys)
    r = s["r"]
    return (min(s["x1"], s["x2"]) - r, min(s["y1"], s["y2"]) - r, max(s["x1"], s["x2"]) + r,
            max(s["y1"], s["y2"]) + r)


def render(scene):
    W, Hh = scene["width"], scene["height"]
    img = [[tuple(scene["background"])] * W for _ in range(Hh)]
    who = [[[] for _ in range(W)] for _ in range(Hh)]
    counts = []
    for idx, s in enumerate(scene["shapes"], 1):
        bx0, by0, bx1, by1 = bbox(s)
        bx0, by0, bx1, by1 = max(bx0, 0), max(by0, 0), min(bx1, W - 1), min(by1, Hh - 1)
        sr, sg, sb, a = s["color"]
        n = 0
        for y in range(by0, by1 + 1):
            row, wrow = img[y], who[y]
            for x in range(bx0, bx1 + 1):
                if covers(s, x, y):
                    n += 1
                    dr, dg, db = row[x]
                    row[x] = ((sr * a + dr * (255 - a) + 127) // 255, (sg * a + dg * (255 - a) + 127) // 255,
                              (sb * a + db * (255 - a) + 127) // 255)
                    wrow[x].append(idx)
        counts.append(f"{idx} {s['type']} {n}")
    colors = len({p for row in img for p in row})
    return img, who, "\n".join(counts + [f"colors {colors}"])


# ── strict PNG decoder for the checker ───────────────────────────────────────
class PngError(Exception):
    pass


def decode_png(data: bytes, W: int, Hh: int):
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError("out.png does not start with the 8-byte PNG signature 89 50 4E 47 0D 0A 1A 0A")
    p, chunks = 8, []
    while p < len(data):
        if p + 8 > len(data):
            raise PngError(f"truncated chunk header at byte offset {p}")
        n = struct.unpack(">I", data[p:p + 4])[0]
        t = data[p + 4:p + 8]
        if n > 0x7FFFFFFF or p + 12 + n > len(data):
            raise PngError(f"chunk {len(chunks) + 1} at offset {p} declares length {n}, which runs past the end "
                           f"of the file ({len(data)} bytes)")
        if not all(65 <= c <= 90 or 97 <= c <= 122 for c in t):
            raise PngError(f"chunk {len(chunks) + 1} at offset {p} has an invalid type {t!r}")
        body = data[p + 8:p + 8 + n]
        crc = struct.unpack(">I", data[p + 8 + n:p + 12 + n])[0]
        calc = zlib.crc32(t + body)
        if crc != calc:
            raise PngError(f"chunk {len(chunks) + 1} ({t.decode()}) has CRC {crc:08x} but the CRC-32 of its type "
                           f"and data is {calc:08x}")
        chunks.append((t.decode(), body))
        p += 12 + n
        if t == b"IEND":
            if p != len(data):
                raise PngError(f"{len(data) - p} bytes follow the IEND chunk")
            break
    if not chunks or chunks[0][0] != "IHDR":
        raise PngError("the first chunk is not IHDR")
    if chunks[-1][0] != "IEND":
        raise PngError("the file does not end with an IEND chunk")
    if chunks[-1][1]:
        raise PngError("IEND chunk has non-zero length")
    ih = chunks[0][1]
    if len(ih) != 13:
        raise PngError(f"IHDR has length {len(ih)}, expected 13")
    w, h, depth, ct, cm, fm, im = struct.unpack(">IIBBBBB", ih)
    if (w, h) != (W, Hh):
        raise PngError(f"IHDR says {w}x{h}, expected {W}x{Hh}")
    if (depth, ct) != (8, 2):
        raise PngError(f"IHDR has bit depth {depth} and colour type {ct}; expected bit depth 8, colour type 2")
    if (cm, fm, im) != (0, 0, 0):
        raise PngError(f"IHDR compression/filter/interlace methods are {cm}/{fm}/{im}; expected 0/0/0")
    names = [c[0] for c in chunks]
    for i, nm in enumerate(names):
        if nm in ("IHDR",) and i != 0:
            raise PngError(f"a second IHDR chunk appears as chunk {i + 1}")
        if nm[0].isupper() and nm not in ("IHDR", "PLTE", "IDAT", "IEND"):
            raise PngError(f"unknown critical chunk {nm} (chunk {i + 1})")
        if nm == "IEND" and i != len(names) - 1:
            raise PngError("IEND is not the last chunk")
    idx = [i for i, nm in enumerate(names) if nm == "IDAT"]
    if not idx:
        raise PngError("there is no IDAT chunk")
    if idx != list(range(idx[0], idx[-1] + 1)):
        raise PngError("IDAT chunks are not consecutive")
    if "PLTE" in names and names.index("PLTE") > idx[0]:
        raise PngError("PLTE appears after IDAT")
    z = b"".join(chunks[i][1] for i in idx)
    if len(z) < 6:
        raise PngError(f"the zlib stream in IDAT is only {len(z)} bytes")
    cmf, flg = z[0], z[1]
    if cmf & 15 != 8 or cmf >> 4 > 7:
        raise PngError(f"zlib header byte CMF={cmf:02x}: compression method must be 8 and window size <= 32K")
    if (cmf * 256 + flg) % 31:
        raise PngError(f"zlib header {cmf:02x} {flg:02x} fails the FCHECK test ((CMF*256+FLG) % 31 must be 0)")
    if flg & 0x20:
        raise PngError("zlib header has FDICT set (preset dictionaries are not allowed in PNG)")
    d = zlib.decompressobj()
    try:
        raw = d.decompress(z, 1 << 26)
    except zlib.error as e:
        msg = f"the zlib/DEFLATE stream in IDAT is invalid ({e})"
        want = Hh * (3 * W + 1)
        r = zlib.decompressobj(-15)
        try:
            body = r.decompress(z[2:], 1 << 26)
            if r.eof:
                trail = r.unused_data[:4]
                msg += (f"; its DEFLATE blocks end (final block reached) after {len(body)} bytes of output, "
                        f"expected {want} (filter bytes included)")
                if len(body) == want:
                    msg += (f"; the 4 bytes after it are {trail.hex()} but the big-endian Adler-32 of the "
                            f"uncompressed data is {zlib.adler32(body):08x}")
            else:
                msg += f"; the DEFLATE data never reaches a final block (decoded {len(body)} bytes)"
        except zlib.error as e2:
            msg += f"; the raw DEFLATE data fails to decode ({e2})"
        raise PngError(msg)
    if not d.eof:
        raise PngError("the zlib stream ends before its final DEFLATE block (or the Adler-32) is complete")
    if d.unused_data:
        raise PngError(f"{len(d.unused_data)} bytes follow the end of the zlib stream (after the Adler-32)")
    stride = 3 * W
    if len(raw) != Hh * (stride + 1):
        raise PngError(f"decompressed image data is {len(raw)} bytes; {Hh} rows x (1 filter byte + {stride}) = "
                       f"{Hh * (stride + 1)} expected")
    prev = bytearray(stride)
    rows = []
    for y in range(Hh):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f > 4:
            raise PngError(f"row {y} has invalid filter type {f}")
        for i in range(stride):
            a = cur[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if f == 1:
                cur[i] = (cur[i] + a) & 255
            elif f == 2:
                cur[i] = (cur[i] + b) & 255
            elif f == 3:
                cur[i] = (cur[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(cur)
        prev = cur
    return rows


def make_checker(scene, img, who, expected, is_example):
    W, Hh = scene["width"], scene["height"]
    kinds = {i: s["type"] for i, s in enumerate(scene["shapes"], 1)}

    def check(stdout, wd):
        f = Path(wd) / "out.png"
        if not f.exists():
            return False, "out.png was not written to the current working directory"
        try:
            rows = decode_png(f.read_bytes(), W, Hh)
        except PngError as e:
            return False, f"out.png is not a valid PNG: {e}"
        wrong, first = 0, None
        for y in range(Hh):
            row = rows[y]
            for x in range(W):
                got = tuple(row[3 * x:3 * x + 3])
                if got != img[y][x]:
                    wrong += 1
                    if first is None:
                        first = (x, y, got)
        if first:
            x, y, got = first
            cov = ", ".join(f"#{i} {kinds[i]}" for i in who[y][x]) or "no shape (background only)"
            return False, (f"out.png decodes correctly but {wrong} of {W * Hh} pixels are wrong. First wrong pixel "
                           f"(scanning rows top to bottom): ({x},{y}) expected {img[y][x]} got {got}; that pixel "
                           f"is covered by {cov}")
        if H.compare_lines(stdout, expected):
            return True, ""
        return False, ("out.png is correct, but standard output is wrong:\n"
                       + H.diff_feedback(stdout, expected, show_all=is_example))

    return check


# ── scene generation ─────────────────────────────────────────────────────────
def gen_scene(rng, W, Hh, nshapes):
    def col(alpha=None):
        a = alpha if alpha is not None else rng.choice([255, 255, 255, 1, 128, 127, 254, 200, rng.randrange(256)])
        return [rng.randrange(256), rng.randrange(256), rng.randrange(256), a]

    def pt(margin):
        return [rng.randint(-margin, W - 1 + margin), rng.randint(-margin, Hh - 1 + margin)]

    shapes = []
    kinds = ["rect", "circle", "triangle", "line"] * 2 + [rng.choice(["rect", "circle", "triangle", "line"])
                                                          for _ in range(nshapes - 8)]
    rng.shuffle(kinds)
    specials = ["huge_circle", "far_line", "r0_line", "dot_line", "zero_rect", "neg_rect", "sliver", "axis_tri",
                "alpha0"]
    for i, k in enumerate(specials):
        kinds.insert(rng.randrange(len(kinds) + 1), k)
    for k in kinds:
        if k == "rect":
            x, y = rng.randint(-20, W - 10), rng.randint(-20, Hh - 10)
            s = {"type": "rect", "x": x, "y": y, "w": rng.randint(1, W // 2), "h": rng.randint(1, Hh // 2)}
        elif k == "zero_rect":
            x, y = pt(0)
            s = {"type": "rect", "x": x, "y": y, "w": rng.choice([0, 5]), "h": 0}
        elif k == "neg_rect":
            s = {"type": "rect", "x": rng.randint(-60, -1), "y": rng.randint(-60, -1), "w": rng.randint(61, 120),
                 "h": rng.randint(61, 100)}
        elif k == "circle":
            cx, cy = pt(5)
            s = {"type": "circle", "cx": cx, "cy": cy, "r": rng.choice([0, 1, 2, 5, rng.randint(3, 60)])}
        elif k == "huge_circle":
            s = {"type": "circle", "cx": rng.choice([-1, 1]) * rng.randint(1500, 3000),
                 "cy": rng.randint(0, Hh - 1), "r": 0}
            s["r"] = abs(s["cx"]) + rng.randint(5, 60) if s["cx"] < 0 else s["cx"] - W + rng.randint(5, 60)
        elif k == "triangle":
            s = {"type": "triangle", "points": [pt(40), pt(40), pt(40)]}
        elif k == "sliver":
            a = pt(0)
            b = [a[0] + rng.randint(40, 150), a[1] + rng.randint(-3, 3)]
            c = [a[0] + rng.randint(-20, 20), a[1] + rng.choice([-1, 1])]
            s = {"type": "triangle", "points": [a, b, c]}
        elif k == "axis_tri":
            a = pt(0)
            dx, dy = rng.randint(8, 50), rng.randint(8, 50)
            pts = [a, [a[0] + dx, a[1]], [a[0], a[1] + dy]]
            rng.shuffle(pts)
            s = {"type": "triangle", "points": pts}
        elif k == "line":
            (x1, y1), (x2, y2) = pt(40), pt(40)
            s = {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "r": rng.choice([1, 2, 3, 6, 10])}
        elif k == "far_line":   # endpoints far outside: c² and r²·L exceed 32 bits
            s = {"type": "line", "x1": rng.randint(-4000, -2500), "y1": rng.randint(-3000, 3000),
                 "x2": rng.randint(2500, 4000), "y2": rng.randint(-3000, 3000), "r": rng.randint(3, 9)}
            # make sure it crosses the canvas: aim through a canvas point
            px, py = rng.randrange(W), rng.randrange(Hh)
            s["y2"] = py + (py - s["y1"]) * (s["x2"] - px) // max(1, px - s["x1"])
            s["y2"] = max(-4000, min(4000, s["y2"]))
        elif k == "r0_line":
            x1, y1 = pt(0)
            ux, uy = rng.choice([(1, 1), (2, 1), (1, -3), (3, 2), (1, 0)])
            n = rng.randint(10, 40)
            s = {"type": "line", "x1": x1, "y1": y1, "x2": x1 + ux * n, "y2": y1 + uy * n, "r": 0}
        elif k == "dot_line":
            x1, y1 = pt(0)
            s = {"type": "line", "x1": x1, "y1": y1, "x2": x1, "y2": y1, "r": rng.randint(2, 12)}
        elif k == "alpha0":
            x, y = pt(0)
            s = {"type": "circle", "cx": x, "cy": y, "r": rng.randint(5, 30)}
        s["color"] = col(0 if k == "alpha0" else None)
        if s["type"] == "triangle":
            while True:
                (x0, y0), (x1, y1), (x2, y2) = s["points"]
                if (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) != 0:
                    break
                s["points"][2][0] += 1
        shapes.append(s)
    return {"width": W, "height": Hh, "background": [rng.randrange(256) for _ in range(3)], "shapes": shapes}


def dump_scene(scene, rng):
    """JSON text with shuffled key order and varied whitespace."""
    def obj(d):
        items = list(d.items())
        rng.shuffle(items)
        sep = rng.choice([", ", ",", " ,\n   "])
        return "{" + sep.join(f'"{k}"{rng.choice([":", ": ", " : "])}{json.dumps(v)}' for k, v in items) + "}"

    head = {k: v for k, v in scene.items() if k != "shapes"}
    head["shapes"] = None
    items = list(head.items())
    rng.shuffle(items)
    parts = []
    for k, v in items:
        if k == "shapes":
            v = "[\n  " + ",\n  ".join(obj(s) for s in scene["shapes"]) + "\n ]"
        else:
            v = json.dumps(v)
        parts.append(f' "{k}": {v}')
    return "{\n" + ",\n".join(parts) + "\n}\n"


EXAMPLE = {"width": 8, "height": 6, "background": [10, 20, 30], "shapes": [
    {"type": "rect", "x": -2, "y": 1, "w": 5, "h": 3, "color": [255, 0, 0, 255]},
    {"type": "circle", "cx": 5, "cy": 2, "r": 2, "color": [0, 0, 255, 128]},
    {"type": "triangle", "points": [[7, 5], [1, 5], [4, 0]], "color": [0, 200, 0, 100]},
    {"type": "line", "x1": 0, "y1": 5, "x2": 7, "y2": 0, "r": 0, "color": [255, 255, 255, 255]},
]}
EXAMPLE_PIXELS = [(0, 0), (1, 1), (3, 2), (4, 2), (4, 1), (2, 4), (0, 5), (7, 5)]


class Challenge(H.Challenge):
    name = "png_render"
    title = "Rasterise a scene with exact integer rules and write a valid PNG"
    category = "binary"

    def prompt(self, ctx):
        img, _, out = render(EXAMPLE)
        scene_txt = ('{"width": 8, "height": 6, "background": [10, 20, 30], "shapes": [\n'
                     + ",\n".join("  " + json.dumps(s) for s in EXAMPLE["shapes"]) + "\n]}")
        px = "\n".join(f"({x},{y}) = {img[y][x]}" for x, y in EXAMPLE_PIXELS)
        return (PROMPT.replace("@EXAMPLE_SCENE@", scene_txt).replace("@EXAMPLE_OUT@", out)
                .replace("@EXAMPLE_PIXELS@", px))

    def case(self, seed, ctx):
        rng = random.Random(91_000 + seed)
        W, Hh = rng.randint(200, 240), rng.randint(130, 160)
        scene = gen_scene(rng, W, Hh, rng.randint(22, 28))
        img, who, out = render(scene)
        d = ctx.data_dir(self, seed)
        (d / "scene.json").write_text(dump_scene(scene, rng))
        assert json.loads((d / "scene.json").read_text()) == scene
        return H.Case(args=[str(d)], check=make_checker(scene, img, who, out, seed == 0))
