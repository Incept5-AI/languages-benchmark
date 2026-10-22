import Foundation

let rootPath = "__FIXTURE_DIR__/project_tree"
let fm = FileManager.default
struct Entry { let rel: String; let lines: Int; let chars: Int }
var by: [String: [Entry]] = [:]
let en = fm.enumerator(atPath: rootPath)!
for case let rel as String in en {
    let full = rootPath + "/" + rel
    guard let attrs = try? fm.attributesOfItem(atPath: full), let type = attrs[.type] as? FileAttributeType, type == .typeRegular else { continue }
    let data = fm.contents(atPath: full) ?? Data()
    var lines = data.filter { $0 == 10 }.count
    if let last = data.last, last != 10 { lines += 1 }
    let name = (rel as NSString).lastPathComponent
    var ext = "(no extension)"
    if let dot = name.lastIndex(of: "."), dot != name.startIndex { ext = String(name[dot...]) }
    else if name.hasPrefix(".") { ext = name }
    by[ext, default: []].append(Entry(rel: rel, lines: lines, chars: data.count))
}
print("=== File Tree Analysis ===\n")
var tf = 0, tl = 0, tc = 0
for ext in by.keys.sorted() {
    let l = by[ext]!.sorted { Array($0.rel.utf8).lexicographicallyPrecedes(Array($1.rel.utf8)) }
    let ls = l.reduce(0) { $0 + $1.lines }, cs = l.reduce(0) { $0 + $1.chars }
    print("Extension: \(ext)")
    print("  Files: \(l.count)")
    print("  Total Lines: \(ls)")
    print("  Total Characters: \(cs)")
    for f in l { print("    \(f.rel): \(f.lines) lines, \(f.chars) chars") }
    tf += l.count; tl += ls; tc += cs
}
print("\nTotal: \(tf) files, \(tl) lines, \(tc) characters")
