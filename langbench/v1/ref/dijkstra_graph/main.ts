import * as fs from "fs";

const g = JSON.parse(fs.readFileSync("__FIXTURE_DIR__/graph.json", "utf8"));
const adj: Record<string, [string, number][]> = {};
for (const n of g.nodes) adj[n] = [];
for (const e of g.edges) {
  (adj[e.from] ||= []).push([e.to, e.weight]);
  (adj[e.to] ||= []).push([e.from, e.weight]);
}
const dist: Record<string, number> = { A: 0 };
const done = new Set<string>();
while (true) {
  let u: string | null = null;
  for (const k of Object.keys(dist)) if (!done.has(k) && (u === null || dist[k] < dist[u])) u = k;
  if (u === null) break;
  done.add(u);
  for (const [v, w] of adj[u]) {
    if (!(v in dist) || dist[u] + w < dist[v]) dist[v] = dist[u] + w;
  }
}
console.log("=== Shortest Paths from A ===");
console.log("");
for (const n of Object.keys(adj).sort()) console.log(`A -> ${n}: ${n in dist ? dist[n] : "unreachable"}`);
