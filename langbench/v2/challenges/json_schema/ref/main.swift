import Foundation

indirect enum V {
    case null
    case bool(Bool)
    case num(Double)
    case str([UInt8])
    case arr([V])
    case obj([([UInt8], V)])

    func get(_ k: [UInt8]) -> V? {
        if case .obj(let m) = self {
            for (kk, v) in m where kk == k { return v }
        }
        return nil
    }
    func get(_ k: String) -> V? { return get(Array(k.utf8)) }
    var tname: String {
        switch self {
        case .null: return "null"
        case .bool: return "boolean"
        case .num: return "number"
        case .str: return "string"
        case .arr: return "array"
        case .obj: return "object"
        }
    }
    var num: Double {
        if case .num(let d) = self { return d }
        return Double.nan
    }
}

struct Parser {
    let s: [UInt8]
    var i = 0
    init(_ s: [UInt8]) { self.s = s }
    mutating func ws() {
        while i < s.count && (s[i] == 0x20 || s[i] == 0x09 || s[i] == 0x0A || s[i] == 0x0D) { i += 1 }
    }
    mutating func value() -> V {
        ws()
        let c = s[i]
        switch c {
        case UInt8(ascii: "{"):
            i += 1
            var m: [([UInt8], V)] = []
            ws()
            if s[i] == UInt8(ascii: "}") { i += 1; return .obj(m) }
            while true {
                ws()
                let k = string()
                ws(); i += 1
                let v = value()
                m.append((k, v))
                ws()
                let d = s[i]; i += 1
                if d == UInt8(ascii: "}") { break }
            }
            return .obj(m)
        case UInt8(ascii: "["):
            i += 1
            var a: [V] = []
            ws()
            if s[i] == UInt8(ascii: "]") { i += 1; return .arr(a) }
            while true {
                a.append(value())
                ws()
                let d = s[i]; i += 1
                if d == UInt8(ascii: "]") { break }
            }
            return .arr(a)
        case UInt8(ascii: "\""):
            return .str(string())
        case UInt8(ascii: "t"):
            i += 4; return .bool(true)
        case UInt8(ascii: "f"):
            i += 5; return .bool(false)
        case UInt8(ascii: "n"):
            i += 4; return .null
        default:
            let st = i
            while i < s.count, let _ = "+-0123456789.eE".utf8.firstIndex(of: s[i]) { i += 1 }
            let t = String(decoding: s[st..<i], as: UTF8.self)
            return .num(Double(t)!)
        }
    }
    mutating func hex4() -> UInt32 {
        let t = String(decoding: s[i..<i + 4], as: UTF8.self)
        i += 4
        return UInt32(t, radix: 16)!
    }
    mutating func string() -> [UInt8] {
        i += 1
        var b: [UInt8] = []
        while true {
            let c = s[i]; i += 1
            if c == UInt8(ascii: "\"") { break }
            if c == UInt8(ascii: "\\") {
                let e = s[i]; i += 1
                switch e {
                case UInt8(ascii: "n"): b.append(0x0A)
                case UInt8(ascii: "t"): b.append(0x09)
                case UInt8(ascii: "r"): b.append(0x0D)
                case UInt8(ascii: "b"): b.append(0x08)
                case UInt8(ascii: "f"): b.append(0x0C)
                case UInt8(ascii: "u"):
                    var cp = hex4()
                    if cp >= 0xD800 && cp < 0xDC00 && s[i] == UInt8(ascii: "\\") && s[i + 1] == UInt8(ascii: "u") {
                        i += 2
                        let lo = hex4()
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                    }
                    let sc = Unicode.Scalar(cp) ?? Unicode.Scalar(0xFFFD)!
                    b.append(contentsOf: Array(String(Character(sc)).utf8))
                default: b.append(e)
                }
            } else {
                b.append(c)
            }
        }
        return b
    }
}

func deepEq(_ a: V, _ b: V) -> Bool {
    switch (a, b) {
    case (.null, .null): return true
    case (.bool(let x), .bool(let y)): return x == y
    case (.num(let x), .num(let y)): return x == y
    case (.str(let x), .str(let y)): return x == y
    case (.arr(let x), .arr(let y)):
        if x.count != y.count { return false }
        for k in 0..<x.count where !deepEq(x[k], y[k]) { return false }
        return true
    case (.obj(let x), .obj(let y)):
        if x.count != y.count { return false }
        for (k, v) in x {
            guard let w = b.get(k) else { return false }
            if !deepEq(v, w) { return false }
        }
        return true
    default: return false
    }
}

func esc(_ t: [UInt8]) -> [UInt8] {
    var o: [UInt8] = []
    for c in t {
        if c == UInt8(ascii: "~") { o.append(contentsOf: [UInt8(ascii: "~"), UInt8(ascii: "0")]) }
        else if c == UInt8(ascii: "/") { o.append(contentsOf: [UInt8(ascii: "~"), UInt8(ascii: "1")]) }
        else { o.append(c) }
    }
    return o
}

func unesc(_ t: [UInt8]) -> [UInt8] {
    var o: [UInt8] = []
    var k = 0
    while k < t.count {
        if t[k] == UInt8(ascii: "~") && k + 1 < t.count {
            if t[k + 1] == UInt8(ascii: "1") { o.append(UInt8(ascii: "/")); k += 2; continue }
            if t[k + 1] == UInt8(ascii: "0") { o.append(UInt8(ascii: "~")); k += 2; continue }
        }
        o.append(t[k]); k += 1
    }
    return o
}

func cpCount(_ b: [UInt8]) -> Int {
    var n = 0
    for c in b where (c & 0xC0) != 0x80 { n += 1 }
    return n
}

struct Err: Hashable {
    let l: [UInt8]
    let p: [UInt8]
}

func lexLess(_ a: [UInt8], _ b: [UInt8]) -> Bool {
    return a.lexicographicallyPrecedes(b)
}

let args = CommandLine.arguments
let dir = URL(fileURLWithPath: args[1])
let sb = [UInt8](try! Data(contentsOf: dir.appendingPathComponent("schema.json")))
let ib = [UInt8](try! Data(contentsOf: dir.appendingPathComponent("instances.json")))
var sp = Parser(sb)
let root = sp.value()
var ip = Parser(ib)
let insts = ip.value()

let slash: [UInt8] = [UInt8(ascii: "/")]
func join(_ a: [UInt8], _ s: String) -> [UInt8] { return a + slash + Array(s.utf8) }
func joinB(_ a: [UInt8], _ s: [UInt8]) -> [UInt8] { return a + slash + s }

func accepts(_ inst: V, _ l: [UInt8], _ s: V, _ p: [UInt8]) -> Bool {
    var e = Set<Err>()
    validate(inst, l, s, p, &e)
    return e.isEmpty
}

func validate(_ inst: V, _ l: [UInt8], _ s: V, _ p: [UInt8], _ out: inout Set<Err>) {
    if case .bool(let b) = s {
        if !b { out.insert(Err(l: l, p: p)) }
        return
    }
    guard case .obj = s else { return }
    let m = s
    func add(_ kw: String) { out.insert(Err(l: l, p: join(p, kw))) }
    let it = inst.tname
    if let t = m.get("type") {
        var ts: [V] = []
        if case .arr(let a) = t { ts = a } else { ts = [t] }
        var ok = false
        for n in ts {
            if case .str(let nb) = n {
                let name = String(decoding: nb, as: UTF8.self)
                if name == it { ok = true }
                else if name == "integer", case .num(let d) = inst, d == d.rounded(.down) { ok = true }
            }
        }
        if !ok { add("type") }
    }
    if let e = m.get("enum"), case .arr(let list) = e {
        if !list.contains(where: { deepEq(inst, $0) }) { add("enum") }
    }
    if let c = m.get("const") {
        if !deepEq(inst, c) { add("const") }
    }
    if case .num(let d) = inst {
        if let v = m.get("minimum"), !(d >= v.num) { add("minimum") }
        if let v = m.get("maximum"), !(d <= v.num) { add("maximum") }
        if let v = m.get("exclusiveMinimum"), !(d > v.num) { add("exclusiveMinimum") }
        if let v = m.get("exclusiveMaximum"), !(d < v.num) { add("exclusiveMaximum") }
    }
    if case .str(let st) = inst {
        let len = Double(cpCount(st))
        if let v = m.get("minLength"), !(len >= v.num) { add("minLength") }
        if let v = m.get("maxLength"), !(len <= v.num) { add("maxLength") }
    }
    if case .arr(let a) = inst {
        let n = Double(a.count)
        if let v = m.get("minItems"), !(n >= v.num) { add("minItems") }
        if let v = m.get("maxItems"), !(n <= v.num) { add("maxItems") }
        if let u = m.get("uniqueItems"), case .bool(true) = u {
            var dup = false
            outer: for x in 0..<a.count {
                for y in (x + 1)..<max(a.count, x + 1) where deepEq(a[x], a[y]) { dup = true; break outer }
            }
            if dup { add("uniqueItems") }
        }
        var pl = 0
        if let pv = m.get("prefixItems"), case .arr(let pi) = pv {
            pl = pi.count
            for k in 0..<min(pl, a.count) {
                validate(a[k], join(l, String(k)), pi[k], join(join(p, "prefixItems"), String(k)), &out)
            }
        }
        if let items = m.get("items"), pl < a.count {
            let ipp = join(p, "items")
            for k in pl..<a.count { validate(a[k], join(l, String(k)), items, ipp, &out) }
        }
    }
    if case .obj(let o) = inst {
        let props = m.get("properties")
        for (k, v) in o {
            if let ps = props?.get(k) {
                validate(v, joinB(l, esc(k)), ps, joinB(join(p, "properties"), esc(k)), &out)
            }
        }
        if let r = m.get("required"), case .arr(let rl) = r {
            for (j, name) in rl.enumerated() {
                if case .str(let nb) = name, inst.get(nb) == nil {
                    out.insert(Err(l: l, p: join(join(p, "required"), String(j))))
                }
            }
        }
        if let ap = m.get("additionalProperties") {
            let app = join(p, "additionalProperties")
            for (k, v) in o where props?.get(k) == nil {
                validate(v, joinB(l, esc(k)), ap, app, &out)
            }
        }
        let n = Double(o.count)
        if let v = m.get("minProperties"), !(n >= v.num) { add("minProperties") }
        if let v = m.get("maxProperties"), !(n <= v.num) { add("maxProperties") }
    }
    if let x = m.get("allOf"), case .arr(let subs) = x {
        for (k, sub) in subs.enumerated() { validate(inst, l, sub, join(join(p, "allOf"), String(k)), &out) }
    }
    if let x = m.get("anyOf"), case .arr(let subs) = x {
        var ok = false
        for (k, sub) in subs.enumerated() where accepts(inst, l, sub, join(join(p, "anyOf"), String(k))) { ok = true; break }
        if !ok { add("anyOf") }
    }
    if let x = m.get("oneOf"), case .arr(let subs) = x {
        var c = 0
        for (k, sub) in subs.enumerated() where accepts(inst, l, sub, join(join(p, "oneOf"), String(k))) { c += 1 }
        if c != 1 { add("oneOf") }
    }
    if let sub = m.get("not") {
        if accepts(inst, l, sub, join(p, "not")) { add("not") }
    }
    if let ifs = m.get("if") {
        if accepts(inst, l, ifs, join(p, "if")) {
            if let t = m.get("then") { validate(inst, l, t, join(p, "then"), &out) }
        } else if let e = m.get("else") {
            validate(inst, l, e, join(p, "else"), &out)
        }
    }
    if let r = m.get("$ref"), case .str(let rb) = r {
        if rb == Array("#".utf8) {
            validate(inst, l, root, Array("#".utf8), &out)
        } else {
            let name = unesc(Array(rb[8...]))
            if let target = root.get("$defs")?.get(name) {
                validate(inst, l, target, Array("#/$defs/".utf8) + esc(name), &out)
            }
        }
    }
}

var outb: [UInt8] = []
func emit(_ s: String) { outb.append(contentsOf: Array(s.utf8)) }
var valid = 0, invalid = 0
let hash: [UInt8] = Array("#".utf8)
if case .arr(let list) = insts {
    for (n, inst) in list.enumerated() {
        var errs = Set<Err>()
        validate(inst, hash, root, hash, &errs)
        if errs.isEmpty {
            valid += 1
            emit("\(n): valid\n")
        } else {
            invalid += 1
            let sorted = errs.sorted { a, b in
                if a.l != b.l { return lexLess(a.l, b.l) }
                return lexLess(a.p, b.p)
            }
            emit("\(n): invalid (\(sorted.count))\n")
            for e in sorted {
                emit("  "); outb.append(contentsOf: e.l); emit(" "); outb.append(contentsOf: e.p); emit("\n")
            }
        }
    }
}
emit("valid=\(valid) invalid=\(invalid)\n")
FileHandle.standardOutput.write(Data(outb))
