import heapq
import json

with open("__FIXTURE_DIR__/graph.json") as f:
    g = json.load(f)
adj = {n: [] for n in g["nodes"]}
for e in g["edges"]:
    adj.setdefault(e["from"], []).append((e["to"], e["weight"]))
    adj.setdefault(e["to"], []).append((e["from"], e["weight"]))
dist = {"A": 0}
pq = [(0, "A")]
while pq:
    d, u = heapq.heappop(pq)
    if d > dist.get(u, float("inf")):
        continue
    for v, w in adj.get(u, []):
        nd = d + w
        if nd < dist.get(v, float("inf")):
            dist[v] = nd
            heapq.heappush(pq, (nd, v))
print("=== Shortest Paths from A ===")
print()
for n in sorted(adj):
    print(f"A -> {n}: {dist[n] if n in dist else 'unreachable'}")
