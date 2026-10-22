import json
import os
import struct
import sys
import zlib

scene = json.load(open(os.path.join(sys.argv[1], "scene.json"), encoding="utf-8"))
W, H = scene["width"], scene["height"]
bg = scene["background"]
R = [bytearray([bg[0]] * W) for _ in range(H)]
G = [bytearray([bg[1]] * W) for _ in range(H)]
B = [bytearray([bg[2]] * W) for _ in range(H)]


def spans(s):
    """yield (y, x) pixels covered, clipped to the canvas"""
    t = s["type"]
    if t == "rect":
        for y in range(max(0, s["y"]), min(H, s["y"] + s["h"])):
            for x in range(max(0, s["x"]), min(W, s["x"] + s["w"])):
                yield y, x
    elif t == "circle":
        cx, cy, r = s["cx"], s["cy"], s["r"]
        for y in range(max(0, cy - r), min(H, cy + r + 1)):
            rem = r * r - (y - cy) ** 2
            if rem < 0:
                continue
            k = int(rem ** 0.5)
            while k * k > rem:
                k -= 1
            while (k + 1) * (k + 1) <= rem:
                k += 1
            for x in range(max(0, cx - k), min(W, cx + k + 1)):
                yield y, x
    elif t == "triangle":
        p = s["points"]
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        edges = [(p[i][0], p[i][1], p[(i + 1) % 3][0], p[(i + 1) % 3][1]) for i in range(3)]
        for y in range(max(0, min(ys)), min(H, max(ys) + 1)):
            for x in range(max(0, min(xs)), min(W, max(xs) + 1)):
                pos = neg = False
                for xi, yi, xj, yj in edges:
                    e = (xj - xi) * (y - yi) - (yj - yi) * (x - xi)
                    if e > 0:
                        pos = True
                    elif e < 0:
                        neg = True
                if not (pos and neg):
                    yield y, x
    else:
        x1, y1, x2, y2, r = s["x1"], s["y1"], s["x2"], s["y2"], s["r"]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        rr = r * r
        for y in range(max(0, min(y1, y2) - r), min(H, max(y1, y2) + r + 1)):
            for x in range(max(0, min(x1, x2) - r), min(W, max(x1, x2) + r + 1)):
                t2 = (x - x1) * dx + (y - y1) * dy
                if t2 <= 0:
                    ok = (x - x1) ** 2 + (y - y1) ** 2 <= rr
                elif t2 >= L:
                    ok = (x - x2) ** 2 + (y - y2) ** 2 <= rr
                else:
                    c = (x - x1) * dy - (y - y1) * dx
                    ok = c * c <= rr * L
                if ok:
                    yield y, x


out = []
for i, s in enumerate(scene["shapes"], 1):
    sr, sg, sb, a = s["color"]
    ia = 255 - a
    n = 0
    for y, x in spans(s):
        n += 1
        R[y][x] = (sr * a + R[y][x] * ia + 127) // 255
        G[y][x] = (sg * a + G[y][x] * ia + 127) // 255
        B[y][x] = (sb * a + B[y][x] * ia + 127) // 255
    out.append(f"{i} {s['type']} {n}")

raw = bytearray()
colors = set()
for y in range(H):
    raw.append(0)
    for x in range(W):
        px = (R[y][x], G[y][x], B[y][x])
        colors.add(px)
        raw += bytes(px)
out.append(f"colors {len(colors)}")


def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))


png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
open("out.png", "wb").write(png)
print("\n".join(out))
