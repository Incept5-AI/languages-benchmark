import Foundation

final class Hunk {
    var a = 0, b = 0, d = 0
    var malformed = false
    var oldL: [[UInt8]] = []
    var newL: [[UInt8]] = []
}

final class Section {
    var path: String
    var kind = "modify"
    var hunks: [Hunk] = []
    init(path: String) { self.path = path }
}

func splitLines(_ data: [UInt8]) -> [[UInt8]] {
    var out: [[UInt8]] = []
    var start = 0
    for i in 0..<data.count where data[i] == 0x0A {
        out.append(Array(data[start...i]))
        start = i + 1
    }
    if start < data.count { out.append(Array(data[start...])) }
    return out
}

func startsWith(_ l: [UInt8], _ s: String) -> Bool {
    let sb = Array(s.utf8)
    if l.count < sb.count { return false }
    for i in 0..<sb.count where l[i] != sb[i] { return false }
    return true
}

func str(_ l: [UInt8]) -> String {
    var n = l.count
    while n > 0 && (l[n - 1] == 0x0A || l[n - 1] == 0x0D) { n -= 1 }
    return String(decoding: l[0..<n], as: UTF8.self)
}

func parseRange(_ s: Substring) -> (Int, Int)? {
    if let ci = s.firstIndex(of: ",") {
        guard let x = Int(s[s.startIndex..<ci]), let y = Int(s[s.index(after: ci)...]) else { return nil }
        return (x, y)
    }
    guard let x = Int(s) else { return nil }
    return (x, 1)
}

func parseHeader(_ l: [UInt8]) -> (Int, Int, Int, Int)? {
    let s = str(l)
    guard s.hasPrefix("@@ -") else { return nil }
    let rest = s.dropFirst(4)
    guard let sp = rest.firstIndex(of: " ") else { return nil }
    guard let (a, b) = parseRange(rest[rest.startIndex..<sp]) else { return nil }
    var r2 = rest[rest.index(after: sp)...]
    guard r2.hasPrefix("+") else { return nil }
    r2 = r2.dropFirst()
    guard let sp2 = r2.firstIndex(of: " ") else { return nil }
    guard let (c, d) = parseRange(r2[r2.startIndex..<sp2]) else { return nil }
    let r3 = r2[r2.index(after: sp2)...]
    guard r3.hasPrefix("@@") else { return nil }
    return (a, b, c, d)
}

var table = [UInt32](repeating: 0, count: 256)
for n in 0..<256 {
    var c = UInt32(n)
    for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB88320 ^ (c >> 1)) : (c >> 1) }
    table[n] = c
}

let root = CommandLine.arguments[1]
func readBytes(_ p: String) -> [UInt8]? {
    guard let d = FileManager.default.contents(atPath: p) else { return nil }
    return [UInt8](d)
}
let diff = readBytes(root + "/changes.diff") ?? []
let lines = splitLines(diff)
var sections: [Section] = []
var inHunk = false
for i in 0..<lines.count {
    let l = lines[i]
    if startsWith(l, "diff --git ") {
        let parts = str(l).split(separator: " ", omittingEmptySubsequences: false)
        var p = parts.count > 2 ? String(parts[2]) : ""
        if p.hasPrefix("a/") { p = String(p.dropFirst(2)) }
        sections.append(Section(path: p))
        inHunk = false
        continue
    }
    guard let cur = sections.last else { continue }
    if startsWith(l, "@@ ") {
        let h = Hunk()
        if let (a, b, _, d) = parseHeader(l) {
            h.a = a; h.b = b; h.d = d
        } else { h.malformed = true }
        cur.hunks.append(h)
        inHunk = true
        continue
    }
    if !inHunk {
        if startsWith(l, "new file mode") { cur.kind = "create" }
        else if startsWith(l, "deleted file mode") { cur.kind = "delete" }
        continue
    }
    if l.isEmpty { continue }
    let h = cur.hunks[cur.hunks.count - 1]
    let mk = l[0]
    if mk == 0x5C { continue }
    var content = Array(l[1...])
    let noNl = i + 1 < lines.count && !lines[i + 1].isEmpty && lines[i + 1][0] == 0x5C
    if noNl && content.last == 0x0A { content.removeLast() }
    switch mk {
    case 0x20: h.oldL.append(content); h.newL.append(content)
    case 0x2D: h.oldL.append(content)
    case 0x2B: h.newL.append(content)
    case 0x0A: h.oldL.append([0x0A]); h.newL.append([0x0A])
    default: break
    }
}
for s in sections {
    for h in s.hunks where h.oldL.count != h.b || h.newL.count != h.d { h.malformed = true }
}
sections.sort { x, y in
    let bx = Array(x.path.utf8), by = Array(y.path.utf8)
    return bx.lexicographicallyPrecedes(by)
}

var out = ""
for s in sections {
    out += "\(s.path): \(s.kind)\n"
    let fp = root + "/tree/" + s.path
    var isDir: ObjCBool = false
    let exists = FileManager.default.fileExists(atPath: fp, isDirectory: &isDir) && !isDir.boolValue
    var content: [[UInt8]]
    if s.kind == "create" {
        if exists { out += "  error: already exists\n"; continue }
        content = []
    } else {
        if !exists { out += "  error: not found\n"; continue }
        content = splitLines(readBytes(fp) ?? [])
    }
    var delta = 0
    var allApplied = true
    for (idx, h) in s.hunks.enumerated() {
        let n = idx + 1
        if h.malformed { out += "  hunk \(n): malformed\n"; allApplied = false; continue }
        let P = h.a + delta
        if h.b == 0 {
            if P >= 0 && P <= content.count {
                content.insert(contentsOf: h.newL, at: P)
                out += "  hunk \(n): applied at \(P) (offset 0)\n"
                delta += h.d - h.b
            } else {
                out += "  hunk \(n): rejected\n"; allApplied = false
            }
            continue
        }
        let maxP = content.count - h.b + 1
        var found = -1
        if maxP >= 1 {
            let lim = max(abs(P - 1), abs(P - maxP)) + 1
            var k = 0
            while k <= lim && found < 0 {
                let cands = k == 0 ? [P] : [P - k, P + k]
                for p in cands {
                    if p < 1 || p > maxP { continue }
                    var ok = true
                    for j in 0..<h.b where content[p - 1 + j] != h.oldL[j] { ok = false; break }
                    if ok { found = p; break }
                }
                k += 1
            }
        }
        if found < 0 { out += "  hunk \(n): rejected\n"; allApplied = false; continue }
        content.replaceSubrange((found - 1)..<(found - 1 + h.b), with: h.newL)
        let off = found - P
        let os = off > 0 ? "+\(off)" : "\(off)"
        out += "  hunk \(n): applied at \(found) (offset \(os))\n"
        delta += h.d - h.b
    }
    if s.kind == "delete" && allApplied && content.isEmpty { out += "  result: deleted\n"; continue }
    var c: UInt32 = 0xFFFFFFFF
    for l in content {
        for b in l { c = table[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
    }
    c ^= 0xFFFFFFFF
    let hex = String(c, radix: 16)
    out += "  result: \(content.count) lines, crc32 \(String(repeating: "0", count: 8 - hex.count) + hex)\n"
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
