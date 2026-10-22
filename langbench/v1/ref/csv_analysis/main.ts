import * as fs from "fs";

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], f = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; }
      else f += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(f); f = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(f); f = ""; rows.push(row); row = [];
    } else f += c;
  }
  if (f !== "" || row.length) { row.push(f); rows.push(row); }
  return rows.filter((r) => !(r.length === 1 && r[0] === ""));
}

const fmt = (n: number, d: number) =>
  n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

const all = parseCsv(fs.readFileSync("__FIXTURE_DIR__/employees.csv", "utf8"));
const hdr = all[0].map((h) => h.trim());
const rows = all.slice(1).map((r) => {
  const o: Record<string, string> = {};
  hdr.forEach((h, i) => (o[h] = (r[i] ?? "").trim()));
  return o;
});
const by: Record<string, number[]> = {};
for (const r of rows) (by[r.department] ||= []).push(parseFloat(r.salary));
const depts = Object.keys(by).sort();
console.log("=== Salary Report ===");
console.log("");
console.log("Average Salary by Department:");
for (const d of depts) {
  const s = by[d];
  console.log(`  ${d}: $${fmt(s.reduce((x, y) => x + y, 0) / s.length, 2)}`);
}
console.log("");
let top = rows[0];
for (const r of rows) if (parseFloat(r.salary) > parseFloat(top.salary)) top = r;
console.log(`Highest Paid: ${top.name} ($${fmt(Math.trunc(parseFloat(top.salary)), 0)})`);
console.log("");
console.log("Departments with >2 employees:");
for (const d of depts) if (by[d].length > 2) console.log(`  ${d} (${by[d].length} employees)`);
console.log("");
console.log(`Total Budget: $${fmt(Math.trunc(rows.reduce((s, r) => s + parseFloat(r.salary), 0)), 0)}`);
