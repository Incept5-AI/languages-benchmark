"""patch_apply — apply a git-style unified diff to a tree with an exactly specified search, and report digests.

Hard parts: parsing hunk bodies by prefix (removed `-- x` lines look like `--- x` headers, added `++ x` like
`+++ x`), omitted hunk counts, `\\ No newline at end of file` on the old and/or new side (a line's newline is part
of its identity), sections without `---`/`+++` (empty file create/delete), header-count validation (a malformed
hunk must not swallow the next one), nearest-first search with the earlier position winning ties, a delta that
only counts applied hunks, CRLF content (no newline translation), deletes that fail, and CRC-32 by hand.
"""
import difflib
import hashlib
import random
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import harness as H  # noqa: E402

PROMPT = r"""
Write a {LANG} program that applies a git-style unified diff to a directory tree and reports, per file, what
happened to every hunk and a digest of the resulting content.

The program is run with one argument: a directory containing the diff `changes.diff` and a directory `tree/`.
A path such as `src/util/a.c` in the diff refers to the file `tree/src/util/a.c`. Do not modify any files:
compute the patched contents in memory.

## Lines
Treat the files and the diff as bytes (they are mostly UTF-8; some lines end in `\r`, which is ordinary line
content — never translate line endings). Content is split into lines at `\n` only. Every line ends with `\n`
except possibly the last line of a file ("no newline at end of file"). An empty file has zero lines. Two lines
are **equal** only if their bytes are equal **and** both or neither end with `\n`. The line count of a content
includes a last line that has no `\n`.

## The diff
The diff is a sequence of file sections (it ends with a newline). A section starts with `diff --git a/<path> b/<path>` (the same path
twice; paths contain no spaces; no path appears in two sections). The section's next lines may be:
- `new file mode <mode>` — the section **creates** the file (kind `create`);
- `deleted file mode <mode>` — the section **deletes** the file (kind `delete`);
- `index <hash>..<hash>` or `index <hash>..<hash> <mode>` — ignore it.

Without one of the first two the kind is `modify`. If the section has hunks, these header lines are followed by
`--- a/<path>` (`--- /dev/null` when creating) and `+++ b/<path>` (`+++ /dev/null` when deleting), and then the
hunks. A section that creates or deletes an empty file has no `---`/`+++` lines and no hunks.

A hunk starts with a header line `@@ -<a>,<b> +<c>,<d> @@`, where either `,<b>` or `,<d>` may be omitted
(meaning a count of 1); the closing `@@` may be followed by a space and any text, which is ignored. The hunk
**body** is every following line up to, not including, the next line that starts with `@@ ` or `diff --git `, or
the end of the diff. Each body line (never empty) starts with one marker character, and the rest of the line is the content:
- ` ` (space) — a context line, on both the old and the new side;
- `-` — a line on the old side only; `+` — a line on the new side only;
- `\` — the line `\ No newline at end of file`: the body line just before it has no `\n` (on the side or sides
  that line belongs to).

So a body line `--- x` is a removed line whose content is `-- x`. The hunk's **old lines** are its context and
`-` lines in order; its **new lines** are its context and `+` lines in order. If the number of old lines is not
`b` or the number of new lines is not `d`, the hunk is **malformed** and is skipped. `c` is not used.

## Applying
Handle each section independently:
- `create`: if the file already exists, the result is the error `already exists` and nothing else is done.
  Otherwise the content starts empty.
- `modify` / `delete`: if the file does not exist, the result is the error `not found`. Otherwise the content
  starts as the file's current content.

Then apply the hunks in order, keeping a number `delta`, initially 0:
- The expected position is `P = a + delta`.
- If the hunk has no old lines (`b` = 0): it applies if `0 <= P <= ` the current line count, inserting the new
  lines after line `P` (at the start if `P` = 0); it is reported as applied at `P` with offset 0. Otherwise it
  is rejected.
- Otherwise search for a position `p` (a 1-based line number in the current content) at which the `b` lines
  starting at line `p` are equal to the old lines. Try `p = P, P-1, P+1, P-2, P+2, …` — nearest first, the
  earlier position first when two are equally near — skipping positions where the hunk would not fit in the
  content (`p < 1` or `p + b - 1 >` line count), until no positions remain on either side. The first match wins:
  those `b` lines are replaced by the new lines, and the offset is `p - P`. There is no fuzz: if no position
  matches exactly, the hunk is rejected and the content is unchanged.
- After a hunk is applied, `delta` increases by `d - b`. Rejected and malformed hunks do not change `delta`.

A `delete` section deletes the file only if every one of its hunks was applied and the content is then empty;
otherwise the file stays, with its content as patched so far.

## Output
One block per section, ordered by path (bytewise); files that no section names are not reported:
```
<path>: <kind>
  hunk <n>: applied at <p> (offset <k>)
  hunk <n>: rejected
  hunk <n>: malformed
  result: <N> lines, crc32 <hex>
```
Hunks are numbered from 1 within their section and each gets one line. `<k>` is written `+3`, `-2` or `0`. The
result line is `  result: deleted` for a deleted file; otherwise `<N>` is the final line count (always followed by
`lines`) and `<hex>` is the CRC-32 (the zlib/PNG one: reflected polynomial 0xEDB88320, initial value and final
XOR 0xFFFFFFFF) of the final content bytes, as 8 lowercase hex digits. A section with an error prints only its
first line and `  error: already exists` or `  error: not found`. Indentation is two spaces.

## Example
With `tree/` holding `notes.txt` (`alpha`, `beta`, `gamma`, `delta`, `epsilon`, each ending in `\n`) and `old.txt`,
and this `changes.diff`:
```
diff --git a/notes.txt b/notes.txt
index 3c4e9cd..8d1f2b0 100644
--- a/notes.txt
+++ b/notes.txt
@@ -2,2 +2,3 @@ greek
 gamma
-delta
+DELTA
+delta2
@@ -5 +6 @@
-zeta
+ZETA
diff --git a/new/hello.txt b/new/hello.txt
new file mode 100644
index 0000000..b6fc4c6
--- /dev/null
+++ b/new/hello.txt
@@ -0,0 +1,2 @@
+hello
+world
\ No newline at end of file
diff --git a/missing.c b/missing.c
index 1111111..2222222 100644
--- a/missing.c
+++ b/missing.c
@@ -1 +1 @@
-int x;
+long x;
```
the output is
```
missing.c: modify
  error: not found
new/hello.txt: create
  hunk 1: applied at 0 (offset 0)
  result: 2 lines, crc32 @EX_CRC_NEW@
notes.txt: modify
  hunk 1: applied at 3 (offset +1)
  hunk 2: rejected
  result: 6 lines, crc32 @EX_CRC_NOTES@
```
"""

EX_TREE = {"notes.txt": b"alpha\nbeta\ngamma\ndelta\nepsilon\n", "old.txt": b"x\n"}
EX_DIFF = PROMPT.split("and this `changes.diff`:\n```\n")[1].split("```")[0].encode()


# ══ oracle ═══════════════════════════════════════════════════════════════════════════════════════════════
def split_lines(data: bytes):
    if not data:
        return []
    parts = data.split(b"\n")
    if parts[-1] == b"":
        return [(p, True) for p in parts[:-1]]
    return [(p, True) for p in parts[:-1]] + [(parts[-1], False)]


def join_lines(lines) -> bytes:
    return b"".join(t + (b"\n" if nl else b"") for t, nl in lines)


def parse_diff(diff: bytes):
    rows = diff.split(b"\n")
    if rows and rows[-1] == b"":
        rows.pop()
    sections, i = [], 0
    while i < len(rows):
        r = rows[i]
        assert r.startswith(b"diff --git a/"), r
        path = r[len(b"diff --git a/"):].split(b" b/")[0].decode()
        kind, hunks = "modify", []
        i += 1
        while i < len(rows) and not rows[i].startswith(b"diff --git ") and not rows[i].startswith(b"@@ "):
            if rows[i].startswith(b"new file mode"):
                kind = "create"
            elif rows[i].startswith(b"deleted file mode"):
                kind = "delete"
            i += 1
        while i < len(rows) and rows[i].startswith(b"@@ "):
            hdr = rows[i].decode()
            o, n = hdr.split(" ")[1:3]
            a, _, b = o[1:].partition(",")
            c, _, d = n[1:].partition(",")
            a, b, d = int(a), int(b) if b else 1, int(d) if d else 1
            i += 1
            old, new, last = [], [], None
            while i < len(rows) and not rows[i].startswith(b"@@ ") and not rows[i].startswith(b"diff --git "):
                m, txt = rows[i][:1], rows[i][1:]
                if m == b" ":
                    old.append([txt, True])
                    new.append([txt, True])
                    last = "both"
                elif m == b"-":
                    old.append([txt, True])
                    last = "old"
                elif m == b"+":
                    new.append([txt, True])
                    last = "new"
                elif m == b"\\":
                    if last in ("both", "old"):
                        old[-1][1] = False
                    if last in ("both", "new"):
                        new[-1][1] = False
                else:
                    raise ValueError(rows[i])
                i += 1
            hunks.append(dict(a=a, b=b, d=d, old=[tuple(x) for x in old], new=[tuple(x) for x in new]))
        sections.append((path, kind, hunks))
    return sections


def apply_all(tree: dict, diff: bytes) -> str:
    out = []
    for path, kind, hunks in sorted(parse_diff(diff), key=lambda s: s[0].encode()):
        out.append(f"{path}: {kind}")
        if kind == "create" and path in tree:
            out.append("  error: already exists")
            continue
        if kind != "create" and path not in tree:
            out.append("  error: not found")
            continue
        L = [] if kind == "create" else split_lines(tree[path])
        delta, all_ok = 0, True
        for n, h in enumerate(hunks, 1):
            if len(h["old"]) != h["b"] or len(h["new"]) != h["d"]:
                out.append(f"  hunk {n}: malformed")
                all_ok = False
                continue
            P, b = h["a"] + delta, h["b"]
            found = None
            if b == 0:
                if 0 <= P <= len(L):
                    found = P
                    L[P:P] = h["new"]
            else:
                last = len(L) - b + 1
                cands = [P] + [x for k in range(1, max(abs(P - 1), abs(last - P)) + 2) for x in (P - k, P + k)]
                for p in cands:
                    if 1 <= p <= last and L[p - 1:p - 1 + b] == h["old"]:
                        found = p
                        L[p - 1:p - 1 + b] = h["new"]
                        break
            if found is None:
                out.append(f"  hunk {n}: rejected")
                all_ok = False
                continue
            off = found - P if b else 0
            out.append(f"  hunk {n}: applied at {found} (offset {'+' if off > 0 else ''}{off})")
            delta += h["d"] - h["b"]
        if kind == "delete" and all_ok and not L:
            out.append("  result: deleted")
        else:
            data = join_lines(L)
            out.append(f"  result: {len(L)} lines, crc32 {zlib.crc32(data):08x}")
    return "\n".join(out)


# ══ generation ═══════════════════════════════════════════════════════════════════════════════════════════
def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:7]


def make_section(path, old: bytes, new: bytes, kind, rng, malform=None):
    out = [b"diff --git a/%s b/%s" % (path.encode(), path.encode())]
    if kind == "create":
        out += [b"new file mode 100644", b"index 0000000..%s" % blob(new).encode()]
    elif kind == "delete":
        out += [b"deleted file mode 100644", b"index %s..0000000" % blob(old).encode()]
    else:
        out += [b"index %s..%s 100644" % (blob(old).encode(), blob(new).encode())]
    a, b = split_lines(old), split_lines(new)
    if not a and not b:
        return out
    out.append(b"--- /dev/null" if kind == "create" else b"--- a/" + path.encode())
    out.append(b"+++ /dev/null" if kind == "delete" else b"+++ b/" + path.encode())

    for hn, grp in enumerate(difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(3)):
        i1, i2, j1, j2 = grp[0][1], grp[-1][2], grp[0][3], grp[-1][4]
        oc, nc = i2 - i1, j2 - j1
        os_ = i1 + 1 if oc else i1
        ns_ = j1 + 1 if nc else j1
        if malform is not None and hn == malform:
            nc += rng.choice([1, -1])
        hdr = f"@@ -{os_ if oc == 1 else f'{os_},{oc}'} +{ns_ if nc == 1 else f'{ns_},{nc}'} @@"
        if rng.random() < 0.4:
            hdr += " " + rng.choice(["int main(void)", "def helper(x):", "## Section", "[core]", "fn run()"])
        out.append(hdr.encode())
        body = []
        for tag, x1, x2, y1, y2 in grp:
            if tag == "equal":
                body += [(b" ", ln) for ln in a[x1:x2]]
            else:
                body += [(b"-", ln) for ln in a[x1:x2]]
                body += [(b"+", ln) for ln in b[y1:y2]]
        for m, (txt, nl) in body:
            out.append(m + txt)
            if not nl:
                out.append(b"\\ No newline at end of file")
    return out


WORDS = ["alpha", "bravo", "count", "delta", "value", "index", "total", "buffer", "result", "offset", "node",
         "token", "cache", "flag", "limit", "queue"]


def code_lines(rng, n, tag):
    out = []
    for i in range(n):
        w = rng.choice(WORDS)
        out.append(rng.choice([
            f"    {w}_{tag}{i} = {w}_{tag}{i - 1 if i else 0} + {rng.randrange(1, 99)};",
            f"    if ({w}_{tag}{i} > {rng.randrange(100)}) {{",
            f"    }}  /* {tag}:{i} */",
            f"int {w}_{tag}{i}(void);",
            f"    /* step {i}: {w} {rng.choice(WORDS)} */",
            f"    return {w}_{tag}{i};",
        ]))
    return out


def enc(lines, final_nl=True, sep="\n"):
    s = sep.join(lines)
    return (s + sep if final_nl and lines else s).encode()


def edit(lines, edits):
    """edits: list of (index, op, payload) applied bottom-up. op: rep (replace 1 line with payload list),
    ins (insert payload after index), dele (delete payload lines starting at index)."""
    out = list(lines)
    for idx, op, pay in sorted(edits, key=lambda e: -e[0]):
        if op == "rep":
            out[idx:idx + 1] = pay
        elif op == "ins":
            out[idx + 1:idx + 1] = pay
        else:
            out[idx:idx + pay] = []
    return out


def make_case(seed):
    rng = random.Random(7300 + seed)
    names = rng.sample(["engine", "parser", "widget", "socket", "matrix", "ledger", "render", "codec",
                        "router", "vault", "kernel", "plotter"], 12)
    tree, sections, checks = {}, [], []

    # 1 code file: three hunks applied with offsets, one rejected (changed context), delta excludes it
    p1 = f"src/{names[0]}.c"
    o = code_lines(rng, 72, "a")
    n_ = edit(o, [(10, "rep", [o[10] + " /* fixed */", "    check();"]), (26, "dele", 2),
                  (42, "ins", [f"    /* new {k} */" for k in range(3)]), (58, "rep", ["    return 0;"])])
    t = ["/* header */", f"/* {names[0]} v{seed + 2} */", "#include <stdio.h>"] + edit(
        o, [(24, "rep", [o[24] + " "]), (50, "dele", 2)])
    tree[p1] = enc(t)
    sections.append(make_section(p1, enc(o), enc(n_), "modify", rng))
    checks.append((p1, ["rejected", "offset +3"]))

    # 2 periodic file: the expected position is mid-period → tie → the earlier match wins
    p2 = f"config/{names[1]}.ini"
    head = [f"; {names[1]} settings", "[global]"]
    block = ["[item]", "enabled = true", f"weight = {rng.randrange(1, 5)}", ""]
    o = head + block * 12 + ["[end]", f"checksum = {rng.randrange(10 ** 6)}"]
    k = 2 + 4 * rng.choice([5, 6, 7]) + 2
    n_ = list(o)
    n_[k] = "weight = 9"
    n_[-1] = "checksum = 0"
    tree[p2] = enc(head + ["; inserted", "; lines"] + o[2:])
    sections.append(make_section(p2, enc(o), enc(n_), "modify", rng))
    checks.append((p2, ["offset -2", "offset +2"]))

    # 3 UTF-8 doc whose old side has no final newline; an earlier hunk has a bad count (malformed)
    p3 = f"docs/{names[2]}.md"
    o = [f"# {names[2].title()} — guide", ""] + [
        f"{i}. Café {rng.choice(WORDS)} naïve {rng.choice(WORDS)} ✓ step {i}" for i in range(1, 25)]
    n_ = edit(o, [(5, "rep", [o[5] + " (updated)"]), (len(o) - 1, "ins", ["", "Done. 🎉"])])
    tree[p3] = enc(o, final_nl=False)
    sections.append(make_section(p3, enc(o, final_nl=False), enc(n_), "modify", rng, malform=0))
    checks.append((p3, ["malformed", "hunk 2: applied"]))

    # 4 new side loses its final newline; a line inserted in the tree shifts the second hunk
    p4 = f"lib/{names[3]}.txt"
    o = [f"{rng.choice(WORDS)} {i} {rng.choice(WORDS)}" for i in range(30)]
    n_ = edit(o, [(4, "rep", ["changed four"]), (29, "rep", ["the end"])])
    tree[p4] = enc(edit(o, [(15, "ins", ["extra line in tree"])]))
    sections.append(make_section(p4, enc(o), enc(n_, final_nl=False), "modify", rng))
    checks.append((p4, ["offset +1"]))

    # 5 lines that look like headers inside hunk bodies
    p5 = f"scripts/{names[4]}.lua"
    o = []
    for i in range(24):
        o += [f"-- {rng.choice(WORDS)} comment {i}", f"local {rng.choice(WORDS)}{i} = {i}"]
    n_ = edit(o, [(6, "rep", ["++ counter", "-- replaced comment"]), (7, "dele", 1),
                  (30, "rep", ["@@ -1,2 +1,2 @@ not a header", "++ b/fake"]), (40, "dele", 2)])
    tree[p5] = enc(o)
    sections.append(make_section(p5, enc(o), enc(n_), "modify", rng))
    checks.append((p5, ["offset 0"]))

    # 6 CRLF file with an extra line at the top of the tree
    p6 = f"win/{names[5]}.bat"
    o = [f"echo {rng.choice(WORDS)} {i}\r" for i in range(20)]
    n_ = edit(o, [(9, "rep", [f"rem changed {seed}\r"])])
    tree[p6] = enc(["@echo off\r"] + o)
    sections.append(make_section(p6, enc(o), enc(n_), "modify", rng))
    checks.append((p6, ["offset +1"]))

    # 7-9 creates: one line with newline (omitted count), several lines without, empty
    p7, p8, p9 = f"new/{names[6]}.txt", f"new/{names[7]}.h", f"new/{names[8]}.keep"
    sections.append(make_section(p7, b"", f"only {names[6]}\n".encode(), "create", rng))
    sections.append(make_section(p8, b"", enc([f"#define {w.upper()} {i}" for i, w in
                                              enumerate(rng.sample(WORDS, 5))], final_nl=False), "create", rng))
    sections.append(make_section(p9, b"", b"", "create", rng))

    # 10-12 deletes: success, failure (tree differs), empty
    p10, p11, p12 = f"old/{names[9]}.txt", f"old/{names[10]}.txt", f"old/{names[11]}.lock"
    o = [f"obsolete {rng.choice(WORDS)} {i}" for i in range(rng.randrange(4, 9))]
    tree[p10] = enc(o)
    sections.append(make_section(p10, enc(o), b"", "delete", rng))
    o = [f"legacy {rng.choice(WORDS)} {i}" for i in range(6)]
    tree[p11] = enc(edit(o, [(3, "rep", ["locally edited"])]))
    sections.append(make_section(p11, enc(o), b"", "delete", rng))
    tree[p12] = b""
    sections.append(make_section(p12, b"", b"", "delete", rng))

    # 13 modify a missing file, 14 create an existing one
    p13 = f"src/{names[0]}_missing.c"
    sections.append(make_section(p13, b"int x;\n", b"long x;\n", "modify", rng))
    p14 = f"src/{names[1]}.h"
    tree[p14] = b"#pragma once\n"
    sections.append(make_section(p14, b"", b"#pragma once\nint y;\n", "create", rng))

    # 15 one-line file (both counts omitted) with an upper-case path
    p15 = rng.choice(["VERSION", "Makefile", "README"])
    tree[p15] = f"version = {seed}.1\n".encode()
    sections.append(make_section(p15, f"version = {seed}.1\n".encode(), f"version = {seed}.2\n".encode(),
                                 "modify", rng))

    # 16 tree lost its final newline: the hunk at the end no longer matches
    p16 = f"etc/{names[2]}.conf"
    o = [f"{w} = {rng.randrange(1000)}" for w in WORDS[:14]]
    n_ = edit(o, [(2, "rep", ["count = 0"]), (13, "rep", ["queue = 1"])])
    tree[p16] = enc(["# managed"] + o, final_nl=False)
    sections.append(make_section(p16, enc(o), enc(n_), "modify", rng))
    checks.append((p16, ["offset +1", "hunk 2: rejected"]))

    # unrelated tree files
    tree["src/unrelated.c"] = b"/* not in the diff */\n"
    tree[f"docs/{names[3]}.md"] = b"untouched\n"

    rng.shuffle(sections)
    diff = b"\n".join(b"\n".join(s) for s in sections) + b"\n"
    exp = apply_all(tree, diff)
    # every case must contain every trap
    blocks = {}
    for ln in exp.split("\n"):
        if not ln.startswith("  "):
            cur = ln.split(":")[0]
            blocks[cur] = []
        else:
            blocks[cur].append(ln)
    for path, needles in checks:
        txt = "\n".join(blocks[path])
        for nd in needles:
            assert nd in txt, (seed, path, nd, txt)
    return tree, diff, exp


class Challenge(H.Challenge):
    name = "patch_apply"
    title = "Apply a git-style unified diff: offsets, ties, no-newline markers, rejects, CRC-32"
    category = "lang"

    def prompt(self, ctx):
        exp = apply_all(EX_TREE, EX_DIFF).split("\n")
        crc_new = exp[4].rsplit(" ", 1)[1]
        crc_notes = exp[8].rsplit(" ", 1)[1]
        return PROMPT.replace("@EX_CRC_NEW@", crc_new).replace("@EX_CRC_NOTES@", crc_notes)

    def case(self, seed, ctx):
        tree, diff, exp = make_case(seed)
        d = ctx.data_dir(self, seed)
        for rel, data in tree.items():
            f = d / "tree" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        (d / "changes.diff").write_bytes(diff)
        return H.Case(args=[str(d)], expected=exp)
