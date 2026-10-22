import math
import os
import struct
import sys

LOW = [697, 770, 852, 941]
HIGH = [1209, 1336, 1477, 1633]
PAD = ["123A", "456B", "789C", "*0#D"]


def parse(buf):
    if buf[:4] != b"RIFF" or buf[8:12] != b"WAVE":
        return None
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(buf):
        cid = buf[pos:pos + 4]
        n = struct.unpack_from("<I", buf, pos + 4)[0]
        body = buf[pos + 8:pos + 8 + n]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + n + (n & 1)
    return fmt, data


def detect(x, rate):
    n = len(x)
    e = 0
    for v in x:
        e += v * v
    if e < 1000000 * n:
        return None
    r = {}
    for f in LOW + HIGH:
        c = 2 * math.cos(2 * math.pi * f / rate)
        s1 = s2 = 0.0
        for v in x:
            s = v + c * s1 - s2
            s2 = s1
            s1 = s
        r[f] = 2 * (s1 * s1 + s2 * s2 - c * s1 * s2) / (n * e)
    fl = max(LOW, key=lambda f: r[f])
    fh = max(HIGH, key=lambda f: r[f])
    if r[fl] >= 0.1 and r[fh] >= 0.1 and r[fl] + r[fh] >= 0.6:
        return PAD[LOW.index(fl)][HIGH.index(fh)]
    return None


def process(name, buf):
    fmt, data = parse(buf)
    tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    eff = struct.unpack_from("<H", fmt, 24)[0] if tag == 0xFFFE else tag
    if eff != 1 or bits != 16:
        return [f"{name}: unsupported"]
    frames = len(data) // (2 * ch)
    allv = struct.unpack_from(f"<{frames * ch}h", data, 0)
    left = allv[0::ch]
    nb = rate // 50
    nblocks = frames // nb
    labels = [detect(left[i * nb:(i + 1) * nb], rate) for i in range(nblocks)]
    br = list(labels)
    for i in range(1, nblocks - 1):
        if labels[i] is None and labels[i - 1] is not None and labels[i - 1] == labels[i + 1]:
            br[i] = labels[i - 1]
    out = [f"{name}: {rate} Hz, {ch} ch, {nblocks} blocks"]
    digs = ""
    i = 0
    while i < nblocks:
        j = i
        while j + 1 < nblocks and br[j + 1] == br[i]:
            j += 1
        if br[i] is not None and j - i + 1 >= 2:
            out.append(f"{br[i]} {20 * i} {20 * (j + 1)}")
            digs += br[i]
        i = j + 1
    out.append("digits: " + digs)
    return out


d = sys.argv[1]
names = sorted((n for n in os.listdir(d) if n.endswith(".wav")), key=lambda s: s.encode())
blocks = []
for n in names:
    with open(os.path.join(d, n), "rb") as fh:
        blocks.append("\n".join(process(n, fh.read())))
sys.stdout.write("\n\n".join(blocks) + "\n")
