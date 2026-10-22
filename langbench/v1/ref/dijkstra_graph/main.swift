import Foundation

let data = try! Data(contentsOf: URL(fileURLWithPath: "__FIXTURE_DIR__/graph.json"))
let g = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
let nodes = g["nodes"] as! [String]
let edges = g["edges"] as! [[String: Any]]
var adj: [String: [(String, Double)]] = [:]
for n in nodes { adj[n] = [] }
for e in edges {
    let a = e["from"] as! String, b = e["to"] as! String, w = (e["weight"] as! NSNumber).doubleValue
    adj[a, default: []].append((b, w))
    adj[b, default: []].append((a, w))
}
var dist: [String: Double] = [:]
var done = Set<String>()
dist["A"] = 0
while true {
    var best: String? = nil
    for (k, d) in dist where !done.contains(k) { if best == nil || d < dist[best!]! { best = k } }
    guard let u = best else { break }
    done.insert(u)
    for (v, w) in adj[u] ?? [] {
        let nd = dist[u]! + w
        if dist[v] == nil || nd < dist[v]! { dist[v] = nd }
    }
}
print("=== Shortest Paths from A ===\n")
for n in adj.keys.sorted() {
    if let d = dist[n] {
        print("A -> \(n): \(d == d.rounded() ? String(Int(d)) : String(d))")
    } else {
        print("A -> \(n): unreachable")
    }
}
