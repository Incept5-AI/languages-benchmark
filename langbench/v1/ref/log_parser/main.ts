import * as fs from "fs";

const re = /^\[(\d{4}-\d{2}-\d{2}) (\d{2}):\d{2}:\d{2}\] (\S+) \(([^)]*)\) - (.*)$/;
const levels: Record<string, number> = {};
const errs: Record<string, number> = {};
const hours: Record<string, number> = {};
const msgs = new Set<string>();
for (const line of fs.readFileSync("__FIXTURE_DIR__/app.log", "utf8").split("\n")) {
  const m = re.exec(line.replace(/\r$/, ""));
  if (!m) continue;
  const [, , h, lvl, comp, msg] = m;
  levels[lvl] = (levels[lvl] || 0) + 1;
  hours[h] = (hours[h] || 0) + 1;
  if (lvl === "ERROR") {
    errs[comp] = (errs[comp] || 0) + 1;
    msgs.add(msg);
  }
}
const best = (o: Record<string, number>) => {
  let b = "";
  for (const k of Object.keys(o).sort()) if (b === "" || o[k] > o[b]) b = k;
  return b;
};
console.log("=== Log Analysis ===");
console.log("");
console.log("Entries by Level:");
for (const l of Object.keys(levels).sort()) console.log(`  ${l}: ${levels[l]}`);
console.log("");
const c = best(errs);
console.log(`Component with Most Errors: ${c} (${errs[c]} errors)`);
console.log("");
console.log("Unique Error Messages:");
for (const m of [...msgs].sort()) console.log(`  - ${m}`);
console.log("");
const h = best(hours);
console.log(`Busiest Hour: ${h}:00 (${hours[h]} entries)`);
