import os
import struct
import sys
from collections import deque


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def process(path):
    b = open(path, "rb").read()
    off = u32(b, 10)
    hs = u32(b, 14)
    w, hh, planes, bpp, comp = struct.unpack_from("<iiHHI", b, 18)
    clr_used = u32(b, 14 + 32)
    if comp not in (0, 3):
        return [f"error: unsupported compression {comp}"]
    top_down = hh < 0
    h = abs(hh)
    stride = ((w * bpp + 31) // 32) * 4
    pal = None
    if bpp <= 8:
        n = clr_used or (1 << bpp)
        p = 14 + hs
        pal = [(b[p + 4 * i + 2], b[p + 4 * i + 1], b[p + 4 * i]) for i in range(n)]
    fields = None
    if bpp == 32 and comp == 3:
        mo = 14 + hs if hs == 40 else 14 + 40
        masks = struct.unpack_from("<III", b, mo)
        fields = []
        for m in masks:
            s = (m & -m).bit_length() - 1
            n = bin(m).count("1")
            fields.append((m, s, (1 << n) - 1))
    ink = [[False] * w for _ in range(h)]
    for sr in range(h):
        y = sr if top_down else h - 1 - sr
        row = b[off + sr * stride: off + (sr + 1) * stride]
        for x in range(w):
            if bpp == 24:
                bl, g, r = row[3 * x], row[3 * x + 1], row[3 * x + 2]
            elif bpp == 32:
                if fields is None:
                    bl, g, r = row[4 * x], row[4 * x + 1], row[4 * x + 2]
                else:
                    px = u32(row, 4 * x)
                    r, g, bl = ((((px & m) >> s) * 255 + mx // 2) // mx for m, s, mx in fields)
            else:
                bit = x * bpp
                idx = (row[bit >> 3] >> (8 - bpp - (bit & 7))) & ((1 << bpp) - 1)
                r, g, bl = pal[idx]
            ink[y][x] = (299 * r + 587 * g + 114 * bl) // 1000 < 128
    seen = [[False] * w for _ in range(h)]
    comps = []
    total = 0
    for y in range(h):
        for x in range(w):
            if ink[y][x] and not seen[y][x]:
                seen[y][x] = True
                q = deque([(x, y)])
                x0 = x1 = x
                y0 = y1 = y
                n = 0
                while q:
                    cx, cy = q.popleft()
                    n += 1
                    x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            nx, ny = cx + dx, cy + dy
                            if 0 <= nx < w and 0 <= ny < h and ink[ny][nx] and not seen[ny][nx]:
                                seen[ny][nx] = True
                                q.append((nx, ny))
                total += n
                comps.append(f"{x},{y} bbox {x0},{y0}-{x1},{y1} pixels {n}")
    return [f"size {w}x{h} bpp {bpp} header {hs} {'top-down' if top_down else 'bottom-up'} ink {total}",
            f"components {len(comps)}"] + comps


d = sys.argv[1]
out = []
for name in sorted((n for n in os.listdir(d) if n.endswith(".bmp")), key=lambda n: n.encode()):
    out.append(f"== {name}")
    out += process(os.path.join(d, name))
print("\n".join(out))
