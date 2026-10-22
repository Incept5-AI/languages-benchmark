import os

root = "__FIXTURE_DIR__/project_tree"
groups = {}
for dp, dns, fns in os.walk(root, followlinks=False):
    for fn in fns:
        p = os.path.join(dp, fn)
        if os.path.islink(p) or not os.path.isfile(p):
            continue
        with open(p, "rb") as f:
            data = f.read()
        lines = len(data.splitlines())
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        ext = os.path.splitext(fn)[1]
        groups.setdefault(ext, []).append((rel, lines, len(data)))
print("=== File Tree Analysis ===")
print()
tf = tl = tc = 0
for ext in sorted(groups):
    fs = sorted(groups[ext])
    l = sum(x[1] for x in fs); c = sum(x[2] for x in fs)
    print(f"Extension: {ext}")
    print(f"  Files: {len(fs)}")
    print(f"  Total Lines: {l}")
    print(f"  Total Characters: {c}")
    for rel, ln, ch in fs:
        print(f"    {rel}: {ln} lines, {ch} chars")
    tf += len(fs); tl += l; tc += c
print()
print(f"Total: {tf} files, {tl} lines, {tc} characters")
