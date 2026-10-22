import Foundation

func commas(_ n: Int) -> String {
    let s = String(abs(n)); var out = ""
    for (i, c) in s.reversed().enumerated() { if i > 0 && i % 3 == 0 { out.append(",") }; out.append(c) }
    return (n < 0 ? "-" : "") + String(out.reversed())
}
func money2(_ v: Double) -> String {
    let s = String(format: "%.2f", v)
    let parts = s.split(separator: ".")
    return commas(Int(parts[0])!) + "." + parts[1]
}

let text = try! String(contentsOfFile: "__FIXTURE_DIR__/employees.csv", encoding: .utf8)
var byDept: [String: [Double]] = [:]
var topName = "", top = -1.0, total = 0.0
for (i, raw) in text.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }).enumerated() {
    if i == 0 { continue }
    let line = raw.trimmingCharacters(in: .whitespaces)
    if line.isEmpty { continue }
    let f = line.components(separatedBy: ",")
    let sal = Double(f[3].trimmingCharacters(in: .whitespaces))!
    byDept[f[2].trimmingCharacters(in: .whitespaces), default: []].append(sal)
    total += sal
    if sal > top { top = sal; topName = f[1].trimmingCharacters(in: .whitespaces) }
}
print("=== Salary Report ===\n")
print("Average Salary by Department:")
for d in byDept.keys.sorted() {
    let v = byDept[d]!
    print("  \(d): $\(money2(v.reduce(0, +) / Double(v.count)))")
}
print("\nHighest Paid: \(topName) ($\(commas(Int(top.rounded()))))")
print("\nDepartments with >2 employees:")
for d in byDept.keys.sorted() where byDept[d]!.count > 2 { print("  \(d) (\(byDept[d]!.count) employees)") }
print("\nTotal Budget: $\(commas(Int(total.rounded())))")
