import Foundation

let text = try! String(contentsOfFile: "__FIXTURE_DIR__/app.log", encoding: .utf8)
let re = try! NSRegularExpression(pattern: "^\\[(\\d{4}-\\d{2}-\\d{2}) (\\d{2}):\\d{2}:\\d{2}\\] (\\S+) \\(([^)]*)\\) - (.*)$")
var levels: [String: Int] = [:], comps: [String: Int] = [:], hours: [String: Int] = [:]
var msgs = Set<String>()
for raw in text.components(separatedBy: "\n") {
    let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    let ns = line as NSString
    guard let m = re.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else { continue }
    let hour = ns.substring(with: m.range(at: 2)), lvl = ns.substring(with: m.range(at: 3))
    levels[lvl, default: 0] += 1
    hours[hour, default: 0] += 1
    if lvl == "ERROR" {
        comps[ns.substring(with: m.range(at: 4)), default: 0] += 1
        msgs.insert(ns.substring(with: m.range(at: 5)))
    }
}
print("=== Log Analysis ===\n")
print("Entries by Level:")
for l in levels.keys.sorted() { print("  \(l): \(levels[l]!)") }
var bc = "", bn = 0
for c in comps.keys.sorted() where comps[c]! > bn { bn = comps[c]!; bc = c }
print("\nComponent with Most Errors: \(bc) (\(bn) errors)")
print("\nUnique Error Messages:")
for m in msgs.sorted() { print("  - \(m)") }
var bh = "", bhn = 0
for h in hours.keys.sorted() where hours[h]! > bhn { bhn = hours[h]!; bh = h }
print("\nBusiest Hour: \(bh):00 (\(bhn) entries)")
