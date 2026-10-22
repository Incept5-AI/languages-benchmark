import Foundation

enum Kind: Int { case int32, int64, uint32, uint64, sint32, sint64, bool, string, bytes, fixed32, fixed64, sfixed32, sfixed64, msg, enm }
let scalarNames: [String: Kind] = [
    "int32": .int32, "int64": .int64, "uint32": .uint32, "uint64": .uint64, "sint32": .sint32, "sint64": .sint64,
    "bool": .bool, "string": .string, "bytes": .bytes, "fixed32": .fixed32, "fixed64": .fixed64,
    "sfixed32": .sfixed32, "sfixed64": .sfixed64,
]

enum Label { case single, optional, repeated, map }

struct Ty {
    var kind: Kind = .bytes
    var ref: Int = 0
    var name: String = ""
}

struct Field {
    var json: String = ""
    var num: UInt64 = 0
    var ty = Ty()
    var keyTy = Ty()
    var label: Label = .single
    var oneof: Int = -1
}

final class MsgDesc {
    var name: String
    var fields: [Field] = []
    var byNum: [UInt64: Int] = [:]
    var oneofCount = 0
    init(_ n: String) { name = n }
}

final class EnumDesc {
    var name: String
    var byNum: [Int64: String] = [:]
    init(_ n: String) { name = n }
}

var msgs: [MsgDesc] = []
var enums: [EnumDesc] = []
var rootIdx = -1

// ---------- tokenizer ----------
var toks: [String] = []
var tpos = 0

func isWord(_ c: UInt8) -> Bool {
    return (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c == 95 || c == 46 || c == 43 || c == 45 || c >= 0x80
}

func tokenize(_ src: [UInt8]) {
    let n = src.count
    var i = 0
    func tok(_ a: Int, _ b: Int) { toks.append(String(decoding: src[a..<b], as: UTF8.self)) }
    while i < n {
        let c = src[i]
        if c == 32 || (c >= 9 && c <= 13) { i += 1; continue }
        if c == 47 && i + 1 < n && src[i + 1] == 47 {
            while i < n && src[i] != 10 { i += 1 }
            continue
        }
        if c == 47 && i + 1 < n && src[i + 1] == 42 {
            var j = i + 2
            while j + 1 < n && !(src[j] == 42 && src[j + 1] == 47) { j += 1 }
            i = j + 1 < n ? j + 2 : n
            continue
        }
        if c == 34 || c == 39 {
            var j = i + 1
            while j < n && src[j] != c { if src[j] == 92 { j += 1 }; j += 1 }
            let e = min(j + 1, n)
            tok(i, e)
            i = e
            continue
        }
        if isWord(c) {
            var j = i
            while j < n && isWord(src[j]) { j += 1 }
            tok(i, j)
            i = j
            continue
        }
        tok(i, i + 1)
        i += 1
    }
}

func peek() -> String { return tpos < toks.count ? toks[tpos] : "" }
@discardableResult func next() -> String {
    if tpos < toks.count { tpos += 1; return toks[tpos - 1] }
    return ""
}
func more() -> Bool { return tpos < toks.count }

func skipStatement() {
    var depth = 0
    while more() {
        let t = next()
        if t == "{" || t == "[" || t == "(" { depth += 1 }
        else if t == "}" || t == "]" || t == ")" { depth -= 1 }
        else if t == ";" && depth <= 0 { return }
    }
}

func skipFieldOptions() {
    if peek() == "[" {
        var depth = 0
        while more() {
            let t = next()
            if t == "[" { depth += 1 }
            else if t == "]" { depth -= 1; if depth == 0 { break } }
        }
    }
}

func parseInt(_ s0: String) -> Int64 {
    var s = Substring(s0)
    var neg = false
    if s.hasPrefix("-") { neg = true; s = s.dropFirst() } else if s.hasPrefix("+") { s = s.dropFirst() }
    var v: Int64
    if s.hasPrefix("0x") || s.hasPrefix("0X") { v = Int64(UInt64(s.dropFirst(2), radix: 16) ?? 0) }
    else if s.count > 1 && s.hasPrefix("0") { v = Int64(UInt64(s.dropFirst(), radix: 8) ?? 0) }
    else { v = Int64(bitPattern: UInt64(s) ?? 0) }
    return neg ? -v : v
}

func camel(_ name: String) -> String {
    var out = ""
    var up = false
    for ch in name {
        if ch == "_" { up = true; continue }
        if up && ch >= "a" && ch <= "z" { out += ch.uppercased() } else { out.append(ch) }
        up = false
    }
    return out
}

func parseEnum() {
    let ed = EnumDesc(next())
    enums.append(ed)
    next()
    while more() && peek() != "}" {
        let t = peek()
        if t == ";" { next(); continue }
        if t == "option" || t == "reserved" { skipStatement(); continue }
        let vname = next()
        next()
        let num = parseInt(next())
        skipFieldOptions()
        next()
        if ed.byNum[num] == nil { ed.byNum[num] = vname }
    }
    next()
}

func parseField(_ md: MsgDesc, _ oneof: Int) {
    var f = Field()
    f.oneof = oneof
    var t = next()
    if t == "repeated" { f.label = .repeated; t = next() }
    else if t == "optional" { f.label = .optional; t = next() }
    if t == "map" && peek() == "<" {
        next()
        f.keyTy.name = next()
        next()
        t = next()
        next()
        f.label = .map
    }
    f.ty.name = t
    f.json = camel(next())
    next()
    f.num = UInt64(bitPattern: parseInt(next()))
    skipFieldOptions()
    next()
    md.fields.append(f)
}

func parseMessage() -> Int {
    let md = MsgDesc(next())
    let idx = msgs.count
    msgs.append(md)
    next()
    while more() && peek() != "}" {
        let t = peek()
        switch t {
        case ";": next()
        case "message": next(); _ = parseMessage()
        case "enum": next(); parseEnum()
        case "option", "reserved", "extensions": skipStatement()
        case "oneof":
            next(); next()
            let oi = md.oneofCount
            md.oneofCount += 1
            next()
            while more() && peek() != "}" {
                let u = peek()
                if u == ";" { next(); continue }
                if u == "option" { skipStatement(); continue }
                parseField(md, oi)
            }
            next()
        default: parseField(md, -1)
        }
    }
    next()
    md.fields.sort { $0.num < $1.num }
    for (i, f) in md.fields.enumerated() { md.byNum[f.num] = i }
    return idx
}

func resolve(_ t: inout Ty, _ names: [String: (Kind, Int)]) {
    if let k = scalarNames[t.name] { t.kind = k; return }
    if let (k, r) = names[t.name] { t.kind = k; t.ref = r } else { t.kind = .bytes }
}

func parseSchema(_ src: [UInt8]) {
    tokenize(src)
    while more() {
        let t = peek()
        if t == "message" { next(); let i = parseMessage(); if rootIdx < 0 { rootIdx = i } }
        else if t == "enum" { next(); parseEnum() }
        else if t == ";" { next() }
        else { skipStatement() }
    }
    var names: [String: (Kind, Int)] = [:]
    for (i, m) in msgs.enumerated() { names[m.name] = (.msg, i) }
    for (i, e) in enums.enumerated() { names[e.name] = (.enm, i) }
    for m in msgs {
        for i in 0..<m.fields.count {
            resolve(&m.fields[i].ty, names)
            if m.fields[i].label == .map { resolve(&m.fields[i].keyTy, names) }
        }
    }
}

// ---------- values ----------
final class MsgVal {
    var fields: [UInt64: Slot] = [:]
    var oneof: [UInt64]
    init(_ mi: Int) { oneof = Array(repeating: 0, count: msgs[mi].oneofCount) }
}

enum Val {
    case n(UInt64)
    case b(ArraySlice<UInt8>)
    case m(MsgVal)
}

enum MapKey: Hashable {
    case i(Int64)
    case s([UInt8])
}

enum Slot {
    case one(Val)
    case rep([Val])
    case map([MapKey: Val])
}

struct Trunc: Error {}

struct Reader {
    let buf: [UInt8]
    var pos: Int
    let end: Int
    mutating func varint() throws -> UInt64 {
        var r: UInt64 = 0
        var shift: UInt64 = 0
        while true {
            if pos >= end { throw Trunc() }
            let b = buf[pos]
            pos += 1
            if shift < 64 { r |= UInt64(b & 0x7f) << shift }
            shift += 7
            if b & 0x80 == 0 { break }
        }
        return r
    }
    mutating func fixed(_ n: Int) throws -> UInt64 {
        if pos + n > end { throw Trunc() }
        var v: UInt64 = 0
        for i in 0..<n { v |= UInt64(buf[pos + i]) << UInt64(8 * i) }
        pos += n
        return v
    }
    mutating func lenDelim() throws -> (Int, Int) {
        let l = try varint()
        if l > UInt64(end - pos) { throw Trunc() }
        let s = pos
        pos += Int(l)
        return (s, pos)
    }
}

func skipField(_ r: inout Reader, _ wt: UInt64, _ fnum: UInt64) throws {
    switch wt {
    case 0: _ = try r.varint()
    case 1: _ = try r.fixed(8)
    case 5: _ = try r.fixed(4)
    case 2: _ = try r.lenDelim()
    case 3:
        while true {
            let key = try r.varint()
            let w = key & 7
            let n = key >> 3
            if w == 4 { if n != fnum { throw Trunc() }; break }
            try skipField(&r, w, n)
        }
    case 4: break
    default: throw Trunc()
    }
}

func wireOf(_ t: Ty) -> UInt64 {
    switch t.kind {
    case .fixed32, .sfixed32: return 5
    case .fixed64, .sfixed64: return 1
    case .string, .bytes, .msg: return 2
    default: return 0
    }
}

func readScalar(_ r: inout Reader, _ wt: UInt64) throws -> Val {
    switch wt {
    case 0: return .n(try r.varint())
    case 5: return .n(try r.fixed(4))
    case 1: return .n(try r.fixed(8))
    default:
        let (a, b) = try r.lenDelim()
        return .b(r.buf[a..<b])
    }
}

func defaultVal(_ t: Ty) -> Val {
    switch t.kind {
    case .msg: return .m(MsgVal(t.ref))
    case .string, .bytes: return .b([])
    default: return .n(0)
    }
}

func convInt(_ k: Kind, _ v: UInt64) -> Int64 {
    switch k {
    case .int32, .sfixed32, .enm: return Int64(Int32(truncatingIfNeeded: v))
    case .uint32, .fixed32: return Int64(UInt32(truncatingIfNeeded: v))
    case .sint32:
        let u = UInt32(truncatingIfNeeded: v)
        return Int64(Int32(bitPattern: (u >> 1) ^ (0 &- (u & 1))))
    case .sint64: return Int64(bitPattern: (v >> 1) ^ (0 &- (v & 1)))
    case .bool: return v != 0 ? 1 : 0
    default: return Int64(bitPattern: v)
    }
}

func decodeInto(_ mi: Int, _ msg: MsgVal, _ buf: [UInt8], _ start: Int, _ end: Int) throws {
    let md = msgs[mi]
    var r = Reader(buf: buf, pos: start, end: end)
    while r.pos < r.end {
        let key = try r.varint()
        let wt = key & 7
        let fnum = key >> 3
        guard let fi = md.byNum[fnum] else { try skipField(&r, wt, fnum); continue }
        let f = md.fields[fi]
        if f.label == .map {
            if wt != 2 { try skipField(&r, wt, fnum); continue }
            let (a, b) = try r.lenDelim()
            var kv: Val? = nil
            var vv: Val? = nil
            var er = Reader(buf: buf, pos: a, end: b)
            while er.pos < er.end {
                let k2 = try er.varint()
                let w2 = k2 & 7
                let n2 = k2 >> 3
                let t: Ty? = n2 == 1 ? f.keyTy : (n2 == 2 ? f.ty : nil)
                if let t = t, wireOf(t) == w2 {
                    if t.kind == .msg {
                        let (c, d) = try er.lenDelim()
                        var m: MsgVal
                        if case .m(let ex)? = vv { m = ex } else { m = MsgVal(t.ref) }
                        try decodeInto(t.ref, m, buf, c, d)
                        vv = .m(m)
                    } else {
                        let v = try readScalar(&er, w2)
                        if n2 == 1 { kv = v } else { vv = v }
                    }
                } else {
                    try skipField(&er, w2, n2)
                }
            }
            let k = kv ?? defaultVal(f.keyTy)
            let v = vv ?? defaultVal(f.ty)
            var mk: MapKey
            switch k {
            case .b(let s): mk = .s(Array(s))
            case .n(let n): mk = .i(convInt(f.keyTy.kind, n))
            default: mk = .i(0)
            }
            var dict: [MapKey: Val] = [:]
            if case .map(let d)? = msg.fields[fnum] { dict = d }
            msg.fields[fnum] = nil
            dict[mk] = v
            msg.fields[fnum] = .map(dict)
            continue
        }
        let ewt = wireOf(f.ty)
        if f.label == .repeated {
            var arr: [Val] = []
            if wt == 2 && ewt != 2 {
                let (a, b) = try r.lenDelim()
                if case .rep(let x)? = msg.fields[fnum] { arr = x }
                msg.fields[fnum] = nil
                var sr = Reader(buf: buf, pos: a, end: b)
                while sr.pos < sr.end { arr.append(try readScalar(&sr, ewt)) }
                msg.fields[fnum] = .rep(arr)
                continue
            }
            if wt != ewt { try skipField(&r, wt, fnum); continue }
            var val: Val
            if f.ty.kind == .msg {
                let (a, b) = try r.lenDelim()
                let m = MsgVal(f.ty.ref)
                try decodeInto(f.ty.ref, m, buf, a, b)
                val = .m(m)
            } else {
                val = try readScalar(&r, wt)
            }
            if case .rep(let x)? = msg.fields[fnum] { arr = x }
            msg.fields[fnum] = nil
            arr.append(val)
            msg.fields[fnum] = .rep(arr)
            continue
        }
        if wt != ewt { try skipField(&r, wt, fnum); continue }
        if f.oneof >= 0 {
            let cur = msg.oneof[f.oneof]
            if cur != 0 && cur != fnum { msg.fields[cur] = nil }
            msg.oneof[f.oneof] = fnum
        }
        if f.ty.kind == .msg {
            let (a, b) = try r.lenDelim()
            var m: MsgVal
            if case .one(.m(let ex))? = msg.fields[fnum] { m = ex } else { m = MsgVal(f.ty.ref) }
            msg.fields[fnum] = .one(.m(m))
            try decodeInto(f.ty.ref, m, buf, a, b)
        } else {
            msg.fields[fnum] = .one(try readScalar(&r, wt))
        }
    }
}

// ---------- output ----------
var out: [UInt8] = []
out.reserveCapacity(1 << 16)
let hexd: [UInt8] = Array("0123456789abcdef".utf8)

func ws(_ s: String) { out.append(contentsOf: s.utf8) }

func esc<C: Collection>(_ b: C) where C.Element == UInt8 {
    out.append(34)
    for c in b {
        switch c {
        case 34: out.append(92); out.append(34)
        case 92: out.append(92); out.append(92)
        case 8: out.append(92); out.append(98)
        case 9: out.append(92); out.append(116)
        case 10: out.append(92); out.append(110)
        case 12: out.append(92); out.append(102)
        case 13: out.append(92); out.append(114)
        default:
            if c < 0x20 {
                out.append(contentsOf: [92, 117, 48, 48, hexd[Int(c >> 4)], hexd[Int(c & 15)]])
            } else { out.append(c) }
        }
    }
    out.append(34)
}

let b64: [UInt8] = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/".utf8)
func base64(_ s: ArraySlice<UInt8>) {
    let b = Array(s)
    out.append(34)
    var i = 0
    while i < b.count {
        var v = UInt32(b[i]) << 16
        if i + 1 < b.count { v |= UInt32(b[i + 1]) << 8 }
        if i + 2 < b.count { v |= UInt32(b[i + 2]) }
        out.append(b64[Int((v >> 18) & 63)])
        out.append(b64[Int((v >> 12) & 63)])
        out.append(i + 1 < b.count ? b64[Int((v >> 6) & 63)] : 61)
        out.append(i + 2 < b.count ? b64[Int(v & 63)] : 61)
        i += 3
    }
    out.append(34)
}

func fmtVal(_ t: Ty, _ v: Val) {
    switch v {
    case .m(let m): fmtMsg(t.ref, m)
    case .b(let s):
        if t.kind == .bytes { base64(s) } else { esc(s) }
    case .n(let n):
        switch t.kind {
        case .bool: ws(n != 0 ? "true" : "false")
        case .uint64, .fixed64: ws("\"\(n)\"")
        case .int64, .sint64, .sfixed64: ws("\"\(convInt(t.kind, n))\"")
        case .enm:
            let x = convInt(.enm, n)
            if let name = enums[t.ref].byNum[x] { esc(Array(name.utf8)) } else { ws(String(x)) }
        default: ws(String(convInt(t.kind, n)))
        }
    }
}

func lexLess(_ a: [UInt8], _ b: [UInt8]) -> Bool {
    return a.lexicographicallyPrecedes(b)
}

func fmtMsg(_ mi: Int, _ m: MsgVal) {
    out.append(123)
    var first = true
    for f in msgs[mi].fields {
        guard let slot = m.fields[f.num] else { continue }
        var skip = false
        switch slot {
        case .map(let d): skip = d.isEmpty
        case .rep(let a): skip = a.isEmpty
        case .one(let v):
            if f.ty.kind != .msg && f.label != .optional && f.oneof < 0 {
                switch v {
                case .b(let s): skip = s.isEmpty
                case .n(let n): skip = convInt(f.ty.kind, n) == 0
                default: break
                }
            }
        }
        if skip { continue }
        if !first { out.append(44) }
        first = false
        esc(Array(f.json.utf8))
        out.append(58)
        switch slot {
        case .map(let d):
            out.append(123)
            let keys = d.keys.sorted { a, b in
                switch (a, b) {
                case (.i(let x), .i(let y)): return x < y
                case (.s(let x), .s(let y)): return lexLess(x, y)
                case (.i, .s): return true
                default: return false
                }
            }
            for (i, k) in keys.enumerated() {
                if i > 0 { out.append(44) }
                switch k {
                case .s(let s): esc(s)
                case .i(let x): ws("\"\(x)\"")
                }
                out.append(58)
                fmtVal(f.ty, d[k]!)
            }
            out.append(125)
        case .rep(let a):
            out.append(91)
            for (i, e) in a.enumerated() {
                if i > 0 { out.append(44) }
                fmtVal(f.ty, e)
            }
            out.append(93)
        case .one(let v): fmtVal(f.ty, v)
        }
    }
    out.append(125)
}

let args = CommandLine.arguments
let dir = args.count > 1 ? args[1] : "."
let srcData = (try? Data(contentsOf: URL(fileURLWithPath: dir + "/schema.proto"))) ?? Data()
let binData = (try? Data(contentsOf: URL(fileURLWithPath: dir + "/messages.bin"))) ?? Data()
parseSchema([UInt8](srcData))
let data = [UInt8](binData)
if rootIdx >= 0 {
    var r = Reader(buf: data, pos: 0, end: data.count)
    while r.pos < r.end {
        guard let (a, b) = try? r.lenDelim() else { break }
        let m = MsgVal(rootIdx)
        try? decodeInto(rootIdx, m, data, a, b)
        fmtMsg(rootIdx, m)
        out.append(10)
    }
}
FileHandle.standardOutput.write(Data(out))
