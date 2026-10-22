import os
import sys
import zlib


class Inflater:
    """puff-style inflate that also records block types"""

    def __init__(self, data):
        self.src = data
        self.pos = 0
        self.bitbuf = 0
        self.bitcnt = 0
        self.out = bytearray()
        self.btypes = []

    def bits(self, need):
        val = self.bitbuf
        while self.bitcnt < need:
            val |= self.src[self.pos] << self.bitcnt
            self.pos += 1
            self.bitcnt += 8
        self.bitbuf = val >> need
        self.bitcnt -= need
        return val & ((1 << need) - 1)

    @staticmethod
    def build(lengths):
        count = [0] * 16
        for l in lengths:
            count[l] += 1
        offs = [0] * 16
        for l in range(1, 15):
            offs[l + 1] = offs[l] + count[l]
        symbol = [0] * len(lengths)
        for s, l in enumerate(lengths):
            if l:
                symbol[offs[l]] = s
                offs[l] += 1
        return count, symbol

    def decode(self, h):
        count, symbol = h
        code = first = index = 0
        for l in range(1, 16):
            code |= self.bits(1)
            c = count[l]
            if code - c < first:
                return symbol[index + (code - first)]
            index += c
            first = (first + c) << 1
            code <<= 1
        raise ValueError("ran out of codes")

    LB = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163,
          195, 227, 258]
    LE = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
    DB = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
          4097, 6145, 8193, 12289, 16385, 24577]
    DE = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]

    def codes(self, lit, dist):
        out = self.out
        while True:
            s = self.decode(lit)
            if s < 256:
                out.append(s)
            elif s == 256:
                return
            else:
                s -= 257
                n = self.LB[s] + self.bits(self.LE[s])
                d = self.decode(dist)
                d = self.DB[d] + self.bits(self.DE[d])
                start = len(out) - d
                for i in range(n):
                    out.append(out[start + i])

    def run(self):
        fixed = None
        while True:
            last = self.bits(1)
            t = self.bits(2)
            self.btypes.append(t)
            if t == 0:
                self.bitbuf = 0
                self.bitcnt = 0
                p = self.pos
                n = self.src[p] | self.src[p + 1] << 8
                self.out += self.src[p + 4:p + 4 + n]
                self.pos = p + 4 + n
            elif t == 1:
                if fixed is None:
                    fixed = (self.build([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8), self.build([5] * 30))
                self.codes(*fixed)
            elif t == 2:
                nlen = self.bits(5) + 257
                ndist = self.bits(5) + 1
                ncode = self.bits(4) + 4
                order = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
                lens = [0] * 19
                for i in range(ncode):
                    lens[order[i]] = self.bits(3)
                lc = self.build(lens)
                ll = []
                while len(ll) < nlen + ndist:
                    s = self.decode(lc)
                    if s < 16:
                        ll.append(s)
                    elif s == 16:
                        ll += [ll[-1]] * (3 + self.bits(2))
                    elif s == 17:
                        ll += [0] * (3 + self.bits(3))
                    else:
                        ll += [0] * (11 + self.bits(7))
                self.codes(self.build(ll[:nlen]), self.build(ll[nlen:]))
            else:
                raise ValueError("bad block type")
            if last:
                return self.pos


def adler32(b):
    a, s = 1, 0
    for i in range(0, len(b), 5552):
        for x in b[i:i + 5552]:
            a += x
            s += a
        a %= 65521
        s %= 65521
    return (s << 16) | a


def process(path):
    data = open(path, "rb").read()
    p = 8
    chunks = []
    while p < len(data):
        n = int.from_bytes(data[p:p + 4], "big")
        t = data[p + 4:p + 8]
        body = data[p + 8:p + 8 + n]
        crc = int.from_bytes(data[p + 8 + n:p + 12 + n], "big")
        chunks.append((t.decode("latin-1"), body, crc))
        p += 12 + n
    for i, (t, body, crc) in enumerate(chunks):
        if zlib.crc32(t.encode("latin-1") + body) != crc:
            return [f"error: CRC mismatch in chunk {i + 1} {t}"]
    ih = chunks[0][1]
    w = int.from_bytes(ih[0:4], "big")
    h = int.from_bytes(ih[4:8], "big")
    depth, ct = ih[8], ih[9]
    plte = trns = None
    idat = bytearray()
    for t, body, _ in chunks:
        if t == "PLTE":
            plte = body
        elif t == "tRNS":
            trns = body
        elif t == "IDAT":
            idat += body
    inf = Inflater(bytes(idat[2:]))
    end = inf.run() + 2
    raw = bytes(inf.out)
    if adler32(raw) != int.from_bytes(idat[end:end + 4], "big"):
        return ["error: Adler-32 mismatch"]

    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    bitspp = nch * depth
    bpp = max(1, bitspp // 8)
    stride = (w * bitspp + 7) // 8
    prev = bytearray(stride)
    fcount = [0] * 5
    rows = []
    q = 0
    for y in range(h):
        f = raw[q]
        cur = bytearray(raw[q + 1:q + 1 + stride])
        q += 1 + stride
        fcount[f] += 1
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                pr = a
            elif f == 2:
                pr = b
            elif f == 3:
                pr = (a + b) >> 1
            elif f == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            else:
                pr = 0
            cur[i] = (cur[i] + pr) & 255
        rows.append(cur)
        prev = cur

    def samples(row):
        if depth < 8:
            mask = (1 << depth) - 1
            res = []
            for x in range(w):
                bit = x * depth
                byte = row[bit >> 3]
                res.append((byte >> (8 - depth - (bit & 7))) & mask)
            return [tuple(res[x:x + 1]) for x in range(w)]
        if depth == 8:
            return [tuple(row[x * nch:(x + 1) * nch]) for x in range(w)]
        vals = [row[2 * i] << 8 | row[2 * i + 1] for i in range(w * nch)]
        return [tuple(vals[x * nch:(x + 1) * nch]) for x in range(w)]

    def to8(v):
        if depth == 16:
            return v >> 8
        if depth < 8:
            return v * 255 // ((1 << depth) - 1)
        return v

    key = None
    if trns is not None and ct in (0, 2):
        key = tuple(int.from_bytes(trns[i:i + 2], "big") for i in range(0, len(trns), 2))
    buf = bytearray()
    for row in rows:
        for s in samples(row):
            if ct == 3:
                i = s[0]
                r, g, b = plte[3 * i], plte[3 * i + 1], plte[3 * i + 2]
                a = trns[i] if trns is not None and i < len(trns) else 255
            elif ct == 0:
                r = g = b = to8(s[0])
                a = 0 if key == s else 255
            elif ct == 2:
                r, g, b = to8(s[0]), to8(s[1]), to8(s[2])
                a = 0 if key == s else 255
            elif ct == 4:
                r = g = b = to8(s[0])
                a = to8(s[1])
            else:
                r, g, b, a = (to8(v) for v in s)
            buf += bytes((r, g, b, a))

    seq = []
    for t, _, _ in chunks:
        if seq and seq[-1][0] == t:
            seq[-1][1] += 1
        else:
            seq.append([t, 1])
    bt = inf.btypes
    sums = [sum(buf[c::4]) for c in range(4)]
    return [
        f"IHDR {w}x{h} depth={depth} color={ct}",
        "chunks " + " ".join(t if k == 1 else f"{t}*{k}" for t, k in seq),
        f"deflate stored={bt.count(0)} fixed={bt.count(1)} dynamic={bt.count(2)}",
        "filters none=%d sub=%d up=%d average=%d paeth=%d" % tuple(fcount),
        "rgba sum=%d,%d,%d,%d crc32=%08x" % (*sums, zlib.crc32(bytes(buf))),
    ]


def main():
    d = sys.argv[1]
    names = sorted((n for n in os.listdir(d) if n.endswith(".png")), key=lambda n: n.encode())
    out = []
    for n in names:
        out.append(f"== {n}")
        out += process(os.path.join(d, n))
    sys.stdout.write("\n".join(out) + "\n")


main()
