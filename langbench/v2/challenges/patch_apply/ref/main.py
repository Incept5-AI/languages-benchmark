import os
import re
import sys

root = sys.argv[1]
with open(os.path.join(root, "changes.diff"), "rb") as fh:
    diff = fh.read()

HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def to_lines(data):
    lines = []
    pos = 0
    while pos < len(data):
        j = data.find(b"\n", pos)
        if j < 0:
            lines.append((data[pos:], False))
            break
        lines.append((data[pos:j], True))
        pos = j + 1
    return lines


CRC_TABLE = []
for n in range(256):
    c = n
    for _ in range(8):
        c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
    CRC_TABLE.append(c)


def crc32(data):
    c = 0xFFFFFFFF
    for byte in data:
        c = CRC_TABLE[(c ^ byte) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


# ── parse ──
rows = diff.split(b"\n")
if rows and rows[-1] == b"":
    rows.pop()

sections = []
i = 0
while i < len(rows):
    if not rows[i].startswith(b"diff --git "):
        i += 1
        continue
    m = re.match(rb"diff --git a/(\S+) b/(\S+)", rows[i])
    sec = {"path": m.group(2), "kind": "modify", "hunks": []}
    sections.append(sec)
    i += 1
    while i < len(rows) and not rows[i].startswith(b"@@ ") and not rows[i].startswith(b"diff --git "):
        if rows[i].startswith(b"new file mode "):
            sec["kind"] = "create"
        elif rows[i].startswith(b"deleted file mode "):
            sec["kind"] = "delete"
        i += 1
    while i < len(rows) and rows[i].startswith(b"@@ "):
        hm = HUNK_RE.match(rows[i])
        a = int(hm.group(1))
        b = int(hm.group(2)) if hm.group(2) is not None else 1
        d = int(hm.group(4)) if hm.group(4) is not None else 1
        i += 1
        old, new = [], []
        prev = None
        while i < len(rows) and not rows[i].startswith(b"@@ ") and not rows[i].startswith(b"diff --git "):
            row = rows[i]
            mark, text = row[:1], row[1:]
            if mark == b"\\":
                if prev in (b" ", b"-"):
                    old[-1] = (old[-1][0], False)
                if prev in (b" ", b"+"):
                    new[-1] = (new[-1][0], False)
            else:
                if mark in (b" ", b"-"):
                    old.append((text, True))
                if mark in (b" ", b"+"):
                    new.append((text, True))
                prev = mark
            i += 1
        sec["hunks"].append((a, b, d, old, new))

# ── apply ──
out = []
for sec in sorted(sections, key=lambda s: s["path"]):
    path, kind = sec["path"], sec["kind"]
    out.append(path.decode() + ": " + kind)
    fpath = os.path.join(root, "tree", path.decode())
    exists = os.path.isfile(fpath)
    if kind == "create" and exists:
        out.append("  error: already exists")
        continue
    if kind != "create" and not exists:
        out.append("  error: not found")
        continue
    if kind == "create":
        content = []
    else:
        with open(fpath, "rb") as fh:
            content = to_lines(fh.read())
    delta = 0
    clean = True
    for n, (a, b, d, old, new) in enumerate(sec["hunks"], 1):
        if len(old) != b or len(new) != d:
            out.append(f"  hunk {n}: malformed")
            clean = False
            continue
        P = a + delta
        where = None
        if b == 0:
            if 0 <= P <= len(content):
                content[P:P] = new
                where = P
        else:
            hi = len(content) - b + 1
            dist = 0
            while True:
                tried = False
                for p in ((P,) if dist == 0 else (P - dist, P + dist)):
                    if 1 <= p <= hi:
                        tried = True
                        if content[p - 1:p - 1 + b] == old:
                            where = p
                            break
                if where is not None:
                    break
                if not tried and (P - dist < 1 and P + dist > hi):
                    break
                dist += 1
            if where is not None:
                content[where - 1:where - 1 + b] = new
        if where is None:
            out.append(f"  hunk {n}: rejected")
            clean = False
            continue
        off = 0 if b == 0 else where - P
        out.append(f"  hunk {n}: applied at {where} (offset {off:+d})" if off else
                   f"  hunk {n}: applied at {where} (offset 0)")
        delta += d - b
    if kind == "delete" and clean and not content:
        out.append("  result: deleted")
    else:
        data = b"".join(t + (b"\n" if nl else b"") for t, nl in content)
        out.append(f"  result: {len(content)} lines, crc32 {crc32(data):08x}")

sys.stdout.write("\n".join(out) + "\n")
