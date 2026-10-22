import csv
from collections import defaultdict

rows = []
with open("__FIXTURE_DIR__/employees.csv", newline="") as f:
    for r in csv.DictReader(f):
        rows.append(r)
by = defaultdict(list)
for r in rows:
    by[r["department"]].append(float(r["salary"]))
print("=== Salary Report ===")
print()
print("Average Salary by Department:")
for d in sorted(by):
    print(f"  {d}: ${sum(by[d]) / len(by[d]):,.2f}")
print()
top = max(rows, key=lambda r: float(r["salary"]))
print(f"Highest Paid: {top['name']} (${int(float(top['salary'])):,})")
print()
print("Departments with >2 employees:")
for d in sorted(by):
    if len(by[d]) > 2:
        print(f"  {d} ({len(by[d])} employees)")
print()
print(f"Total Budget: ${int(sum(float(r['salary']) for r in rows)):,}")
