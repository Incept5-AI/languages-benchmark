"""wav_dtmf — walk RIFF/WAVE chunks and decode DTMF digits with a precisely specified Goertzel detector.

Hard parts: chunk walking (LIST/JUNK/fact chunks before and after `fmt `/`data`, odd-sized chunks with a pad
byte, a 40-byte WAVE_FORMAT_EXTENSIBLE `fmt ` whose real format is in the SubFormat GUID), stereo (left
channel only — the right channel carries different digits), the Goertzel recurrence at exact (non-bin)
frequencies, normalising the power correctly (a wrong scale lets distractor tones through), and the
block/bridge/minimum-duration rules.

Robustness: every generated block sits far from every threshold (asserted below), so floating-point
differences between languages can never change a decision.
"""
import math
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

LOW = [697, 770, 852, 941]
HIGH = [1209, 1336, 1477, 1633]
KEYS = ["123A", "456B", "789C", "*0#D"]
DIGIT_FREQ = {KEYS[i][j]: (LOW[i], HIGH[j]) for i in range(4) for j in range(4)}
E_MIN = 1_000_000      # mean-square threshold
R_MIN = 0.1            # per-group threshold
SUM_MIN = 0.6          # combined threshold

PROMPT = """\
Write a {LANG} program that decodes DTMF (telephone keypad) tones from WAV files.

The program is run with one argument: a directory. Process every file in it whose name ends with `.wav`
(case-sensitive), in ascending byte-wise order of file name. Ignore all other files.

## WAV parsing

Each file is a RIFF file: the 4 bytes `RIFF`, a 32-bit little-endian size, the 4 bytes `WAVE`, then a sequence
of chunks until the end of the file. Each chunk is a 4-byte ASCII id, a 32-bit little-endian size `n`, and `n`
bytes of body; if `n` is odd, one pad byte (not counted in `n`) follows the body. Chunk ids are
case-sensitive. Besides `fmt ` (with a trailing space) and `data` there may be any number of other chunks
(e.g. `LIST`, `JUNK`, `fact`, or others, some with odd sizes) before, between or after them; skip them. Every file
has exactly one `fmt ` chunk and one `data` chunk, and `fmt ` comes before `data`.

`fmt ` body (all little-endian): format tag (u16), channel count (u16), sample rate (u32), byte rate (u32),
block align (u16), bits per sample (u16), and — only when the body is longer than 16 bytes — more fields.
The *effective format* is the format tag, except when the tag is `0xFFFE` (WAVE_FORMAT_EXTENSIBLE): then it is
the u16 at offset 24 of the `fmt ` body (the first two bytes of the SubFormat GUID).

A file is supported if and only if its effective format is 1 (PCM) and bits per sample is 16. For any other
file print the single line `<name>: unsupported` and go on to the next file.

Supported files have 1 or 2 channels and a sample rate of 8000 or 16000 Hz. Samples are signed 16-bit
little-endian, interleaved by frame (for stereo: left, right, left, right, …). The number of frames is the
`data` size divided by (2 × channels). **Use only the first (left) channel**; ignore the other one entirely.

## Detection

Split the left-channel samples into consecutive blocks of N = rate / 50 samples (20 ms: N = 160 at 8000 Hz,
320 at 16000 Hz), starting at the first sample. A final partial block is ignored. Block `i` (0-based) covers
the time [20·i, 20·i + 20) ms. Process each block independently, with the sample values `x[0..N-1]` taken as
the integers read from the file (no scaling):

1. Energy: `E = Σ x[n]²`. If `E < 1000000 · N` (mean square below 1,000,000), the block has no digit.
2. For each of the 8 DTMF frequencies f ∈ {697, 770, 852, 941} (low group) and {1209, 1336, 1477, 1633}
   (high group), compute the Goertzel power at exactly f (not rounded to a DFT bin):
   `c = 2·cos(2π·f / rate)`; `s1 = s2 = 0`; for n = 0..N−1: `s = x[n] + c·s1 − s2; s2 = s1; s1 = s`;
   then `P(f) = s1² + s2² − c·s1·s2`. Its relative power is `r(f) = 2·P(f) / (N · E)` (a pure sinusoid at f
   that fills the block has r ≈ 1).
3. Let `fl` be the low-group frequency with the largest r and `fh` the high-group frequency with the largest r.
   The block detects a digit if and only if `r(fl) ≥ 0.1`, `r(fh) ≥ 0.1` and `r(fl) + r(fh) ≥ 0.6`.
   The digit is given by the keypad:

```
          1209 1336 1477 1633
    697     1    2    3    A
    770     4    5    6    B
    852     7    8    9    C
    941     *    0    #    D
```

(The test signals are generated so that every quantity above is far from its threshold; any correct
double-precision implementation of these formulas makes the same decisions.)

This gives a label per block: a digit, or none.

4. Bridging: a block labelled none whose immediate neighbours (blocks i−1 and i+1, both existing) carry the
   same digit is relabelled with that digit. Decide this for every block from the labels of step 3 (not from
   already-bridged labels). Only none-blocks are bridged; a block carrying a different digit is never changed.
5. Events: each maximal run of consecutive blocks with the same digit (after bridging) is a key press if it is
   at least 2 blocks long; shorter runs are discarded. Its start is 20 × (first block index) ms and its end is
   20 × (last block index + 1) ms.

## Output

For each supported file, in order:
```
<name>: <rate> Hz, <channels> ch, <blocks> blocks
<digit> <start_ms> <end_ms>        (one line per key press, in time order)
digits: <all key-press digits concatenated in time order>
```
where `<blocks>` is the number of complete blocks. If a file has no key presses the last line is `digits: `
(nothing after the space; trailing spaces are ignored by the grader). Print one blank line between consecutive
files (including unsupported ones). `<name>` is the file name without the directory.

Example output:
```
call-a.wav: 8000 Hz, 1 ch, 85 blocks
1 40 200
1 240 300
# 300 380
digits: 11#

call-b.wav: unsupported

call-c.wav: 16000 Hz, 2 ch, 51 blocks
D 0 60
digits: D
```
"""


# ── signal model ─────────────────────────────────────────────────────────────
def goertzel_r(x, rate):
    n = len(x)
    e = float(sum(v * v for v in x))
    out = {}
    for f in LOW + HIGH:
        c = 2.0 * math.cos(2.0 * math.pi * f / rate)
        s1 = s2 = 0.0
        for v in x:
            s1, s2 = v + c * s1 - s2, s1
        p = s1 * s1 + s2 * s2 - c * s1 * s2
        out[f] = 2.0 * p / (n * e) if e > 0 else 0.0
    return e, out


def check_margins(x, rate):
    """Return the oracle's label for a block, asserting it is far from every decision boundary."""
    n = len(x)
    e, r = goertzel_r(x, rate)
    ms = e / n
    assert not (0.6 * E_MIN <= ms <= 1.6 * E_MIN), f"energy near threshold: {ms}"
    if ms < E_MIN:
        return None
    lo = sorted(LOW, key=lambda f: -r[f])
    hi = sorted(HIGH, key=lambda f: -r[f])
    rl, rh = r[lo[0]], r[hi[0]]
    for v in (rl, rh):
        assert not (0.065 <= v <= 0.15), f"group max near threshold: {v}"
    if rl >= 0.15 and rh >= 0.15:
        assert not (0.45 <= rl + rh <= 0.8), f"sum near threshold: {rl + rh}"
    # argmax must be unambiguous whenever it can matter
    if rl > 0.065:
        assert rl >= 3 * r[lo[1]], (rl, r[lo[1]])
    if rh > 0.065:
        assert rh >= 3 * r[hi[1]], (rh, r[hi[1]])
    if rl >= R_MIN and rh >= R_MIN and rl + rh >= SUM_MIN:
        return KEYS[LOW.index(lo[0])][HIGH.index(hi[0])]
    return None


def events_from_labels(labels):
    n = len(labels)
    b = list(labels)
    for i in range(1, n - 1):
        if labels[i] is None and labels[i - 1] is not None and labels[i - 1] == labels[i + 1]:
            b[i] = labels[i - 1]
    ev = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and b[j + 1] == b[i]:
            j += 1
        if b[i] is not None and j - i + 1 >= 2:
            ev.append((b[i], 20 * i, 20 * (j + 1)))
        i = j + 1
    return ev


def synth(rng, rate, plan, noise=300):
    """plan: list of (nblocks, [(freq, amp), ...]). Returns int samples (clipped never happens)."""
    nb = rate // 50
    out = []
    for nblocks, tones in plan:
        ph = [rng.uniform(0, 2 * math.pi) for _ in tones]
        for k in range(nblocks * nb):
            v = sum(a * math.cos(2 * math.pi * f * k / rate + p) for (f, a), p in zip(tones, ph))
            v += rng.randint(-noise, noise)
            iv = int(round(v))
            assert -32768 <= iv <= 32767
            out.append(iv)
    return out


def make_plan(rng, flavour):
    """A block-level plan that contains every trap. flavour varies the mix between files."""
    digits = list("0123456789*#ABCD")
    plan = []

    def pair(d, amp=None):
        lo, hi = DIGIT_FREQ[d]
        a = amp or rng.randint(3000, 6500)
        twist = rng.choice([1.0, 1.0, 0.6, 1.6, 0.55, 1.8])
        return [(lo, a), (hi, int(a * twist))]

    def sil(n):
        plan.append((n, []))

    def press(d, n):
        plan.append((n, pair(d)))

    sil(rng.randint(1, 4))
    traps = ["plain", "plain", "plain", "min2", "dropout", "gap2", "blip", "interrupt", "adjacent",
             "single_tone", "single_dtmf_freq", "half_pair", "three_tone", "quiet", "plain"]
    rng.shuffle(traps)
    traps = traps[: 9 + flavour]
    for t in traps:
        d = rng.choice(digits)
        if t == "plain":
            press(d, rng.randint(3, 8))
        elif t == "min2":
            press(d, 2)
        elif t == "dropout":              # d d none d d d -> one press
            press(d, rng.randint(2, 4)); sil(1); press(d, rng.randint(2, 4))
        elif t == "gap2":                 # d d none none d d -> two presses
            press(d, rng.randint(2, 4)); sil(2); press(d, rng.randint(2, 4))
        elif t == "blip":                 # a single block is discarded
            sil(2); press(d, 1); sil(2)
        elif t == "interrupt":            # d d X d d -> two presses of d, X discarded
            x = rng.choice([c for c in digits if c != d])
            press(d, rng.randint(2, 3)); press(x, 1); press(d, rng.randint(2, 3))
        elif t == "adjacent":             # d then e with no gap -> two presses
            e = rng.choice([c for c in digits if c != d])
            press(d, rng.randint(2, 4)); press(e, rng.randint(2, 4))
        elif t == "single_tone":          # loud non-DTMF tone
            plan.append((rng.randint(3, 6), [(rng.choice([1000, 1100, 2000, 400, 1800]), rng.randint(4000, 9000))]))
        elif t == "single_dtmf_freq":     # one DTMF frequency alone
            plan.append((rng.randint(3, 6), [(rng.choice(LOW + HIGH), rng.randint(4000, 9000))]))
        elif t == "half_pair":            # low DTMF + non-DTMF high-ish tone
            plan.append((rng.randint(3, 6), [(rng.choice(LOW), 5000), (rng.choice([1100, 1900, 2100]), 5000)]))
        elif t == "three_tone":           # valid pair under a much louder third tone
            a = rng.randint(2500, 3500)
            lo, hi = DIGIT_FREQ[d]
            plan.append((rng.randint(3, 6), [(lo, a), (hi, a), (rng.choice([1100, 2000, 2400]), round(1.85 * a))]))
        elif t == "quiet":                # proper pair, far too quiet
            lo, hi = DIGIT_FREQ[d]
            plan.append((rng.randint(3, 6), [(lo, 350), (hi, 350)]))
        sil(rng.randint(1, 3))
    return plan


def labels_for(samples, rate):
    nb = rate // 50
    return [check_margins(samples[i * nb:(i + 1) * nb], rate) for i in range(len(samples) // nb)]


# ── WAV writing ──────────────────────────────────────────────────────────────
def chunk(cid: bytes, body: bytes) -> bytes:
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def fmt_body(kind, ch, rate, bits=16, tag=1, sub=1):
    ba = ch * bits // 8
    base = struct.pack("<HHIIHH", 0xFFFE if kind == "ext" else tag, ch, rate, rate * ba, ba, bits)
    if kind == "16":
        return base
    if kind == "18":
        return base + struct.pack("<H", 0)
    # WAVE_FORMAT_EXTENSIBLE: cbSize=22, validBits, channelMask, SubFormat GUID
    guid = struct.pack("<H", sub) + bytes.fromhex("00000000001000800000aa00389b71")
    return base + struct.pack("<HHI", 22, bits, 3 if ch == 2 else 4) + guid


def list_chunk(rng):
    txt = rng.choice([b"recorded by cb2\0", b"DTMF test\0", b"odd!\0"])
    sub = chunk(b"INAM", txt) + chunk(b"ICMT", b"x" * rng.randint(1, 7))
    return chunk(b"LIST", b"INFO" + sub)


def make_wav(rng, layout, ch, rate, left, right=None, fmt_kind="16", unsupported=None):
    frames = len(left)
    if unsupported == "u8":
        data = bytes((v >> 8) + 128 for v in left)
        fbody = fmt_body("16", ch, rate, bits=8)
        assert ch == 1
    elif unsupported == "float":
        data = b"".join(struct.pack("<f", v / 32768.0) for v in left)
        fbody = fmt_body("18", 1, rate, bits=32, tag=3)
    elif unsupported == "extfloat":
        data = b"".join(struct.pack("<f", v / 32768.0) for v in left)
        fbody = fmt_body("ext", 1, rate, bits=32, sub=3)
    else:
        if ch == 1:
            data = struct.pack(f"<{frames}h", *left)
        else:
            inter = [0] * (2 * frames)
            inter[0::2] = left
            inter[1::2] = right
            data = struct.pack(f"<{2 * frames}h", *inter)
        fbody = fmt_body(fmt_kind, ch, rate)
    parts = []
    for item in layout:
        if item == "fmt":
            parts.append(chunk(b"fmt ", fbody))
        elif item == "data":
            parts.append(chunk(b"data", data))
        elif item == "LIST":
            parts.append(list_chunk(rng))
        elif item == "odd":
            parts.append(chunk(rng.choice([b"note", b"cue ", b"bext"]), bytes(rng.randrange(256) for _ in range(rng.choice([1, 3, 7, 13])))))
        elif item == "JUNK":
            parts.append(chunk(b"JUNK", b"\0" * rng.choice([4, 28, 9])))
        elif item == "fact":
            parts.append(chunk(b"fact", struct.pack("<I", frames)))
        elif item == "fakedata":   # a chunk whose id merely starts like 'data'
            parts.append(chunk(b"dat2", b"\x01\x02\x03"))
    body = b"WAVE" + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_case(seed: int):
    rng = random.Random(4100 + seed)
    names = rng.sample(["call-a", "call-b", "Call-C", "call_d", "callE", "call-f", "Z-last", "x-ray"], 5)
    unsup = rng.choice(["u8", "float", "extfloat"])
    specs = [
        dict(ch=1, rate=8000, fmt="16", layout=["LIST", "fmt", "data"], flavour=0),
        dict(ch=2, rate=16000, fmt="18", layout=["fmt", "odd", "JUNK", "data", "LIST"], flavour=2),
        dict(ch=2, rate=8000, fmt="ext", layout=["odd", "fmt", "fact", "fakedata", "data", "odd"], flavour=4),
        dict(ch=1, rate=16000, fmt="ext", layout=["JUNK", "fmt", "LIST", "odd", "data"], flavour=1),
        dict(ch=1, rate=rng.choice([8000, 16000]), fmt=None, layout=["fmt", "odd", "data"], flavour=0,
             unsupported=unsup),
    ]
    files, outputs = {}, {}
    for name, sp in zip(names, specs):
        fname = name + ".wav"
        rate = sp["rate"]
        for _attempt in range(50):   # regenerate (deterministically) until every block clears the margins
            plan = make_plan(rng, sp["flavour"])
            left = synth(rng, rate, plan)
            left += [rng.randint(-300, 300) for _ in range(rng.randint(1, rate // 50 - 1))]  # partial block
            right = None
            if sp["ch"] == 2:
                right = synth(rng, rate, make_plan(rng, 3))
                right = (right + [0] * len(left))[: len(left)]
            try:
                labels = labels_for(left, rate)
                if right is not None:
                    labels_for(right, rate)  # the right channel is far from thresholds too
                break
            except AssertionError:
                continue
        else:
            raise RuntimeError("could not generate a signal with safe margins")
        if sp.get("unsupported"):
            files[fname] = make_wav(rng, sp["layout"], 1, rate, left, unsupported=sp["unsupported"])
            outputs[fname] = f"{fname}: unsupported"
            continue
        files[fname] = make_wav(rng, sp["layout"], sp["ch"], rate, left, right, fmt_kind=sp["fmt"])
        ev = events_from_labels(labels)
        lines = [f"{fname}: {rate} Hz, {sp['ch']} ch, {len(labels)} blocks"]
        lines += [f"{d} {a} {b}" for d, a, b in ev]
        lines.append("digits: " + "".join(d for d, _, _ in ev))
        outputs[fname] = "\n".join(lines)
    files["notes.txt"] = b"not a wav file\n"
    files["readme.wav.bak"] = b"RIFF\0\0\0\0WAVE"
    order = sorted((n for n in files if n.endswith(".wav")), key=lambda s: s.encode())
    expected = "\n\n".join(outputs[n] for n in order) + "\n"
    return files, expected


class Challenge(H.Challenge):
    name = "wav_dtmf"
    title = "Walk RIFF/WAVE chunks and decode DTMF digits with a Goertzel detector"
    category = "binary"

    def prompt(self, ctx):
        return PROMPT

    def case(self, seed, ctx):
        files, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        for n, b in files.items():
            (d / n).write_bytes(b)
        return H.Case(args=[str(d)], expected=exp)
