import Foundation

let dirArg = CommandLine.arguments[1]
guard let fileData = FileManager.default.contents(atPath: dirArg + "/db.sqlite") else {
    FileHandle.standardError.write("cannot read db\n".data(using: .utf8)!)
    exit(1)
}
let db = [UInt8](fileData)

func be16(_ p: [UInt8], _ o: Int) -> Int { return (Int(p[o]) << 8) | Int(p[o + 1]) }
func be32(_ p: [UInt8], _ o: Int) -> Int {
    return (Int(p[o]) << 24) | (Int(p[o + 1]) << 16) | (Int(p[o + 2]) << 8) | Int(p[o + 3])
}

func varint(_ p: [UInt8], _ o: Int) -> (UInt64, Int) {
    var v: UInt64 = 0
    for i in 0..<8 {
        let b = p[o + i]
        v = (v << 7) | UInt64(b & 0x7F)
        if b & 0x80 == 0 { return (v, i + 1) }
    }
    v = (v << 8) | UInt64(p[o + 8])
    return (v, 9)
}

var pageSize = be16(db, 16)
if pageSize == 1 { pageSize = 65536 }
let usable = pageSize - Int(db[20])

struct Row { let rowid: Int64; let payload: [UInt8] }

func pageOff(_ n: Int) -> Int { return (n - 1) * pageSize }

func walk(_ pg: Int, _ rows: inout [Row], _ depth: Int) {
    if depth > 64 { fatalError("too deep") }
    let base = pageOff(pg)
    let h = base + (pg == 1 ? 100 : 0)
    let type = db[h]
    let ncell = be16(db, h + 3)
    if type == 0x05 {
        for i in 0..<ncell {
            let c = base + be16(db, h + 12 + 2 * i)
            walk(be32(db, c), &rows, depth + 1)
        }
        walk(be32(db, h + 8), &rows, depth + 1)
    } else if type == 0x0D {
        for i in 0..<ncell {
            let c = base + be16(db, h + 8 + 2 * i)
            let (plenU, l1) = varint(db, c)
            let (rowidU, l2) = varint(db, c + l1)
            let start = c + l1 + l2
            let plen = Int(plenU)
            let x = usable - 35
            var out = [UInt8]()
            out.reserveCapacity(plen)
            if plen <= x {
                out.append(contentsOf: db[start..<(start + plen)])
            } else {
                let m = ((usable - 12) * 32 / 255) - 23
                let k = m + ((plen - m) % (usable - 4))
                let local = k <= x ? k : m
                out.append(contentsOf: db[start..<(start + local)])
                var ov = be32(db, start + local)
                while out.count < plen && ov != 0 {
                    let op = pageOff(ov)
                    let take = min(plen - out.count, usable - 4)
                    out.append(contentsOf: db[(op + 4)..<(op + 4 + take)])
                    ov = be32(db, op)
                }
            }
            rows.append(Row(rowid: Int64(bitPattern: rowidU), payload: out))
        }
    } else {
        fatalError("unexpected page type \(type) at \(pg)")
    }
}

enum Val {
    case null
    case int(Int64)
    case real(Double)
    case text(ArraySlice<UInt8>)
    case blob(ArraySlice<UInt8>)
}

func decode(_ p: [UInt8]) -> [Val] {
    let (hlenU, l) = varint(p, 0)
    let hlen = Int(hlenU)
    var hp = l
    var dp = hlen
    var vals = [Val]()
    let sizes = [0, 1, 2, 3, 4, 6, 8]
    while hp < hlen {
        let (st, sl) = varint(p, hp)
        hp += sl
        if st == 0 {
            vals.append(.null)
        } else if st >= 1 && st <= 6 {
            let n = sizes[Int(st)]
            var u: UInt64 = 0
            for i in 0..<n { u = (u << 8) | UInt64(p[dp + i]) }
            if n < 8 && (u >> UInt64(n * 8 - 1)) & 1 == 1 { u |= ~UInt64(0) << UInt64(n * 8) }
            vals.append(.int(Int64(bitPattern: u)))
            dp += n
        } else if st == 7 {
            var u: UInt64 = 0
            for i in 0..<8 { u = (u << 8) | UInt64(p[dp + i]) }
            vals.append(.real(Double(bitPattern: u)))
            dp += 8
        } else if st == 8 {
            vals.append(.int(0))
        } else if st == 9 {
            vals.append(.int(1))
        } else if st >= 12 {
            let n = Int((st - 12) / 2)
            let s = p[dp..<(dp + n)]
            dp += n
            vals.append(st & 1 == 1 ? .text(s) : .blob(s))
        } else {
            vals.append(.null)
        }
    }
    return vals
}

var crcTable = [UInt32](repeating: 0, count: 256)
for i in 0..<256 {
    var c = UInt32(i)
    for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB88320 ^ (c >> 1)) : (c >> 1) }
    crcTable[i] = c
}
func crc32(_ s: ArraySlice<UInt8>) -> String {
    var c: UInt32 = 0xFFFFFFFF
    for b in s { c = crcTable[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
    c ^= 0xFFFFFFFF
    let h = String(c, radix: 16)
    return String(repeating: "0", count: 8 - h.count) + h
}

func fmtReal(_ d: Double) -> String {
    var buf = [CChar](repeating: 0, count: 1600)
    _ = withVaList([d]) { va in
        buf.withUnsafeMutableBufferPointer { bp in
            vsnprintf(bp.baseAddress!, 1600, "%.1100f", va)
        }
    }
    var s = Array(String(cString: buf).utf8)
    while let last = s.last, last == UInt8(ascii: "0") { s.removeLast() }
    if s.last == UInt8(ascii: ".") { s.append(UInt8(ascii: "0")) }
    return String(decoding: s, as: UTF8.self)
}

let hexDigits = Array("0123456789ABCDEF".utf8)

func app(_ out: inout [UInt8], _ s: String) { out.append(contentsOf: s.utf8) }

func fmtVal(_ out: inout [UInt8], _ v: Val) {
    switch v {
    case .null: app(&out, "NULL")
    case .int(let i): app(&out, String(i))
    case .real(let d): app(&out, fmtReal(d))
    case .text(let s):
        if s.count > 100 {
            app(&out, "<TEXT len=\(s.count) crc32=\(crc32(s))>")
        } else {
            out.append(0x22)
            for c in s {
                switch c {
                case 0x5C: app(&out, "\\\\")
                case 0x22: app(&out, "\\\"")
                case 0x0A: app(&out, "\\n")
                case 0x0D: app(&out, "\\r")
                case 0x09: app(&out, "\\t")
                default:
                    if c < 0x20 || c == 0x7F {
                        app(&out, "\\x")
                        out.append(hexDigits[Int(c >> 4)])
                        out.append(hexDigits[Int(c & 15)])
                    } else {
                        out.append(c)
                    }
                }
            }
            out.append(0x22)
        }
    case .blob(let s):
        if s.count > 100 {
            app(&out, "<BLOB len=\(s.count) crc32=\(crc32(s))>")
        } else {
            app(&out, "X'")
            for c in s {
                out.append(hexDigits[Int(c >> 4)])
                out.append(hexDigits[Int(c & 15)])
            }
            app(&out, "'")
        }
    }
}

func isId(_ c: UInt8) -> Bool {
    return (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A) || c == 0x5F || c == 0x24 || c >= 0x80
}
func isSpace(_ c: UInt8) -> Bool { return c == 0x20 || (c >= 0x09 && c <= 0x0D) }

struct Col { let name: [UInt8]; let alias: Bool }

func upper(_ t: [UInt8]) -> String {
    return String(decoding: t.map { ($0 >= 0x61 && $0 <= 0x7A) ? $0 - 32 : $0 }, as: UTF8.self)
}

func parseCols(_ sql: [UInt8]) -> [Col] {
    let n = sql.count
    let Q = UInt8(ascii: "\""), LP = UInt8(ascii: "("), RP = UInt8(ascii: ")"), CM = UInt8(ascii: ",")
    var i = 0
    while i < n && sql[i] != LP {
        if sql[i] == Q { i += 1; while i < n && sql[i] != Q { i += 1 } }
        i += 1
    }
    let start = i + 1
    var depth = 1
    var end = n
    var j = start
    while j < n {
        let c = sql[j]
        if c == Q {
            j += 1
            while j < n && sql[j] != Q { j += 1 }
        } else if c == LP {
            depth += 1
        } else if c == RP {
            depth -= 1
            if depth == 0 { end = j; break }
        }
        j += 1
    }
    var defs = [[UInt8]]()
    var ds = start
    depth = 0
    j = start
    while j <= end {
        let c = j == end ? CM : sql[j]
        if c == Q && j < end {
            j += 1
            while j < end && sql[j] != Q { j += 1 }
        } else if c == LP {
            depth += 1
        } else if c == RP {
            depth -= 1
        } else if c == CM && depth == 0 {
            defs.append(Array(sql[ds..<j]))
            ds = j + 1
        }
        j += 1
    }
    let kws: Set<String> = ["CONSTRAINT", "PRIMARY", "NOT", "NULL", "UNIQUE", "CHECK", "DEFAULT", "COLLATE", "REFERENCES", "GENERATED", "AS"]
    var cols = [Col]()
    for d in defs {
        var toks = [[UInt8]]()
        var k = 0
        while k < d.count {
            let c = d[k]
            if isSpace(c) {
                k += 1
            } else if c == Q {
                let s = k
                k += 1
                while k < d.count && d[k] != Q { k += 1 }
                k += 1
                toks.append(Array(d[s..<min(k, d.count)]))
            } else if isId(c) {
                let s = k
                while k < d.count && isId(d[k]) { k += 1 }
                toks.append(Array(d[s..<k]))
            } else {
                toks.append([c])
                k += 1
            }
        }
        if toks.isEmpty { continue }
        let t0 = toks[0]
        let name = t0[0] == Q ? Array(t0[1..<(t0.count - 1)]) : t0
        let up = toks.map { upper($0) }
        var t = 1, words = 0
        var isInt = false
        while t < toks.count {
            let c = toks[t][0]
            if !(isId(c) || c == Q) { break }
            if kws.contains(up[t]) { break }
            words += 1
            if words == 1 && up[t] == "INTEGER" { isInt = true }
            t += 1
        }
        let paren = t < toks.count && toks[t] == [LP] && words > 0
        var pk = false
        if up.count >= 2 {
            for q in 0..<(up.count - 1) where up[q] == "PRIMARY" && up[q + 1] == "KEY" { pk = true }
        }
        cols.append(Col(name: name, alias: words == 1 && isInt && !paren && pk))
    }
    return cols
}

var schema = [Row]()
walk(1, &schema, 0)
var tabs = [(name: [UInt8], root: Int, sql: [UInt8])]()
for r in schema {
    let v = decode(r.payload)
    if v.count < 5 { continue }
    guard case .text(let t) = v[0], Array(t) == Array("table".utf8) else { continue }
    guard case .text(let nm) = v[1] else { continue }
    let name = Array(nm)
    if name.count >= 7 && Array(name[0..<7]) == Array("sqlite_".utf8) { continue }
    var root = 0
    if case .int(let rt) = v[3] { root = Int(rt) }
    var sql = [UInt8]()
    if case .text(let s) = v[4] { sql = Array(s) }
    tabs.append((name: name, root: root, sql: sql))
}
tabs.sort { a, b in a.name.lexicographicallyPrecedes(b.name) }

var out = [UInt8]()
for (ti, tab) in tabs.enumerated() {
    let cols = parseCols(tab.sql)
    var rows = [Row]()
    if tab.root != 0 { walk(tab.root, &rows, 0) }
    if ti > 0 { out.append(0x0A) }
    app(&out, "TABLE ")
    out.append(contentsOf: tab.name)
    app(&out, " rows=\(rows.count)\nCOLUMNS ")
    for (i, c) in cols.enumerated() {
        if i > 0 { app(&out, ", ") }
        out.append(contentsOf: c.name)
    }
    out.append(0x0A)
    for r in rows {
        let v = decode(r.payload)
        app(&out, "\(r.rowid): ")
        for (i, c) in cols.enumerated() {
            if i > 0 { app(&out, ", ") }
            if c.alias { app(&out, String(r.rowid)) }
            else if i < v.count { fmtVal(&out, v[i]) }
            else { app(&out, "NULL") }
        }
        out.append(0x0A)
    }
}
FileHandle.standardOutput.write(Data(out))
