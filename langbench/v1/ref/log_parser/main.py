import re
from collections import Counter

pat = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}):\d{2}:\d{2}\] (\S+) \(([^)]*)\) - (.*)$")
levels = Counter(); errs = Counter(); msgs = set(); hours = Counter()
with open("__FIXTURE_DIR__/app.log") as f:
    for line in f:
        m = pat.match(line.rstrip("\r\n"))
        if not m:
            continue
        _, h, lvl, comp, msg = m.groups()
        levels[lvl] += 1
        hours[h] += 1
        if lvl == "ERROR":
            errs[comp] += 1
            msgs.add(msg)
print("=== Log Analysis ===")
print()
print("Entries by Level:")
for l in sorted(levels):
    print(f"  {l}: {levels[l]}")
print()
c = max(sorted(errs), key=lambda k: errs[k])
print(f"Component with Most Errors: {c} ({errs[c]} errors)")
print()
print("Unique Error Messages:")
for m in sorted(msgs):
    print(f"  - {m}")
print()
h = max(sorted(hours), key=lambda k: hours[k])
print(f"Busiest Hour: {h}:00 ({hours[h]} entries)")
