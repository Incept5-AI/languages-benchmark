import * as fs from "fs";
import * as path from "path";

const root = "__FIXTURE_DIR__/project_tree";
const groups: Record<string, [string, number, number][]> = {};
function walk(dir: string) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isSymbolicLink()) continue;
    if (ent.isDirectory()) walk(p);
    else if (ent.isFile()) {
      const data = fs.readFileSync(p);
      let lines = 0;
      for (const b of data) if (b === 10) lines++;
      if (data.length > 0 && data[data.length - 1] !== 10) lines++;
      const rel = path.relative(root, p).split(path.sep).join("/");
      (groups[path.extname(ent.name)] ||= []).push([rel, lines, data.length]);
    }
  }
}
walk(root);
console.log("=== File Tree Analysis ===");
console.log("");
let tf = 0, tl = 0, tc = 0;
for (const ext of Object.keys(groups).sort()) {
  const fs_ = groups[ext].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const l = fs_.reduce((s, x) => s + x[1], 0);
  const c = fs_.reduce((s, x) => s + x[2], 0);
  console.log(`Extension: ${ext}`);
  console.log(`  Files: ${fs_.length}`);
  console.log(`  Total Lines: ${l}`);
  console.log(`  Total Characters: ${c}`);
  for (const [rel, ln, ch] of fs_) console.log(`    ${rel}: ${ln} lines, ${ch} chars`);
  tf += fs_.length; tl += l; tc += c;
}
console.log("");
console.log(`Total: ${tf} files, ${tl} lines, ${tc} characters`);
