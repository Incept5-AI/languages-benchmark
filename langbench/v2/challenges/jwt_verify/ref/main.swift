import Foundation
import CommonCrypto

func eq(_ a: String?, _ b: String?) -> Bool {
    guard let a = a, let b = b else { return false }
    return a.utf8.elementsEqual(b.utf8)
}

indirect enum JV {
    case null, bool(Bool), num(Double), str(String), arr([JV]), obj([(String, JV)])
    subscript(_ k: String) -> JV? {
        if case .obj(let kv) = self { for (a, b) in kv where eq(a, k) { return b } }
        return nil
    }
    var string: String? { if case .str(let s) = self { return s }; return nil }
    var number: Double? { if case .num(let d) = self { return d }; return nil }
}

struct JErr: Error {}

struct Parser {
    let s: [UInt8]
    var p = 0
    var depth = 0
    init(_ b: [UInt8]) { s = b }
    mutating func ws() { while p < s.count, s[p] == 32 || s[p] == 9 || s[p] == 10 || s[p] == 13 { p += 1 } }
    mutating func all() throws -> JV { let v = try value(); ws(); if p != s.count { throw JErr() }; return v }
    mutating func lit(_ w: String) -> Bool {
        let b = Array(w.utf8)
        if p + b.count <= s.count && Array(s[p..<p + b.count]) == b { p += b.count; return true }
        return false
    }
    mutating func value() throws -> JV {
        ws()
        guard p < s.count else { throw JErr() }
        depth += 1; defer { depth -= 1 }
        if depth > 1000 { throw JErr() }
        let c = s[p]
        if c == UInt8(ascii: "{") {
            p += 1
            var kv: [(String, JV)] = []
            ws()
            if p < s.count && s[p] == UInt8(ascii: "}") { p += 1; return .obj(kv) }
            while true {
                ws()
                let k = try string()
                ws()
                guard p < s.count, s[p] == UInt8(ascii: ":") else { throw JErr() }
                p += 1
                kv.append((k, try value()))
                ws()
                guard p < s.count else { throw JErr() }
                let d = s[p]; p += 1
                if d == UInt8(ascii: ",") { continue }
                if d == UInt8(ascii: "}") { return .obj(kv) }
                throw JErr()
            }
        } else if c == UInt8(ascii: "[") {
            p += 1
            var a: [JV] = []
            ws()
            if p < s.count && s[p] == UInt8(ascii: "]") { p += 1; return .arr(a) }
            while true {
                a.append(try value())
                ws()
                guard p < s.count else { throw JErr() }
                let d = s[p]; p += 1
                if d == UInt8(ascii: ",") { continue }
                if d == UInt8(ascii: "]") { return .arr(a) }
                throw JErr()
            }
        } else if c == UInt8(ascii: "\"") {
            return .str(try string())
        } else if lit("true") { return .bool(true) }
        else if lit("false") { return .bool(false) }
        else if lit("null") { return .null }
        return .num(try number())
    }
    func isDigit() -> Bool { p < s.count && s[p] >= 48 && s[p] <= 57 }
    mutating func number() throws -> Double {
        let st = p
        if p < s.count && s[p] == UInt8(ascii: "-") { p += 1 }
        guard p < s.count else { throw JErr() }
        if s[p] == 48 { p += 1 } else if isDigit() { while isDigit() { p += 1 } } else { throw JErr() }
        if p < s.count && s[p] == UInt8(ascii: ".") { p += 1; if !isDigit() { throw JErr() }; while isDigit() { p += 1 } }
        if p < s.count && (s[p] == UInt8(ascii: "e") || s[p] == UInt8(ascii: "E")) {
            p += 1
            if p < s.count && (s[p] == UInt8(ascii: "+") || s[p] == UInt8(ascii: "-")) { p += 1 }
            if !isDigit() { throw JErr() }
            while isDigit() { p += 1 }
        }
        let t = String(decoding: s[st..<p], as: UTF8.self)
        return strtod(t, nil)
    }
    mutating func hex4() throws -> UInt32 {
        guard p + 4 <= s.count else { throw JErr() }
        var v: UInt32 = 0
        for _ in 0..<4 {
            let c = s[p]; p += 1
            var h: UInt32
            if c >= 48 && c <= 57 { h = UInt32(c - 48) }
            else if c >= 97 && c <= 102 { h = UInt32(c - 87) }
            else if c >= 65 && c <= 70 { h = UInt32(c - 55) }
            else { throw JErr() }
            v = v * 16 + h
        }
        return v
    }
    mutating func string() throws -> String {
        guard p < s.count, s[p] == UInt8(ascii: "\"") else { throw JErr() }
        p += 1
        var out: [UInt8] = []
        func put(_ cp: UInt32) {
            var buf: [UInt8] = []
            if let sc = Unicode.Scalar(cp) { buf = Array(String(Character(sc)).utf8) } else { buf = [0xEF, 0xBF, 0xBD] }
            out.append(contentsOf: buf)
        }
        while true {
            guard p < s.count else { throw JErr() }
            let c = s[p]; p += 1
            if c == UInt8(ascii: "\"") { break }
            if c < 0x20 { throw JErr() }
            if c == UInt8(ascii: "\\") {
                guard p < s.count else { throw JErr() }
                let e = s[p]; p += 1
                switch e {
                case UInt8(ascii: "\""): out.append(0x22)
                case UInt8(ascii: "\\"): out.append(0x5C)
                case UInt8(ascii: "/"): out.append(0x2F)
                case UInt8(ascii: "b"): out.append(8)
                case UInt8(ascii: "f"): out.append(12)
                case UInt8(ascii: "n"): out.append(10)
                case UInt8(ascii: "r"): out.append(13)
                case UInt8(ascii: "t"): out.append(9)
                case UInt8(ascii: "u"):
                    var cp = try hex4()
                    if cp >= 0xD800 && cp < 0xDC00 && p + 6 <= s.count && s[p] == 0x5C && s[p + 1] == UInt8(ascii: "u") {
                        let save = p
                        p += 2
                        if let lo = try? hex4(), lo >= 0xDC00 && lo < 0xE000 {
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                        } else { p = save }
                    }
                    put(cp)
                default: throw JErr()
                }
            } else { out.append(c) }
        }
        return String(decoding: out, as: UTF8.self)
    }
}

func b64v(_ c: UInt8) -> Int {
    switch c {
    case 65...90: return Int(c) - 65
    case 97...122: return Int(c) - 97 + 26
    case 48...57: return Int(c) - 48 + 52
    case 45: return 62
    case 95: return 63
    default: return -1
    }
}
func b64dec(_ s: [UInt8]) -> [UInt8]? {
    if s.count % 4 == 1 { return nil }
    var out: [UInt8] = []
    var acc = 0, bits = 0
    for c in s {
        let v = b64v(c)
        if v < 0 { return nil }
        acc = ((acc << 6) | v) & 0xFFFFFF
        bits += 6
        if bits >= 8 { bits -= 8; out.append(UInt8((acc >> bits) & 0xFF)) }
    }
    return out
}
func parseObj(_ b: [UInt8]) -> JV? {
    var p = Parser(b)
    guard let v = try? p.all(), case .obj = v else { return nil }
    return v
}

let dir = CommandLine.arguments[1]
let cfgData = FileManager.default.contents(atPath: dir + "/config.json") ?? Data()
var cp = Parser(Array(cfgData))
let cfg: JV = (try? cp.all()) ?? .obj([])
var keys: [JV] = []
if case .arr(let a)? = cfg["keys"] { keys = a }
let now = cfg["now"]?.number ?? 0
let leeway = cfg["leeway"]?.number ?? 0
let issuer = cfg["issuer"]?.string
let audience = cfg["audience"]?.string

func check(_ tok: [UInt8]) -> String {
    let segs = tok.split(separator: UInt8(ascii: "."), omittingEmptySubsequences: false).map { Array($0) }
    if segs.count != 3 { return "MALFORMED" }
    var dec: [[UInt8]] = []
    for sg in segs {
        if sg.isEmpty { return "MALFORMED" }
        guard let d = b64dec(sg) else { return "MALFORMED" }
        dec.append(d)
    }
    guard let hdr = parseObj(dec[0]), let pl = parseObj(dec[1]) else { return "MALFORMED" }
    guard let alg = hdr["alg"]?.string else { return "UNSUPPORTED_ALG" }
    let ha: CCHmacAlgorithm; let hl: Int
    switch alg {
    case "HS256": ha = CCHmacAlgorithm(kCCHmacAlgSHA256); hl = 32
    case "HS384": ha = CCHmacAlgorithm(kCCHmacAlgSHA384); hl = 48
    case "HS512": ha = CCHmacAlgorithm(kCCHmacAlgSHA512); hl = 64
    default: return "UNSUPPORTED_ALG"
    }
    guard let kid = hdr["kid"]?.string else { return "UNKNOWN_KEY" }
    guard let key = keys.first(where: { eq($0["kid"]?.string, kid) }) else { return "UNKNOWN_KEY" }
    if !eq(key["alg"]?.string, alg) { return "ALG_MISMATCH" }
    let secret: [UInt8] = key["k"]?.string.flatMap { b64dec(Array($0.utf8)) } ?? []
    let input = segs[0] + [UInt8(ascii: ".")] + segs[1]
    var mac = [UInt8](repeating: 0, count: 64)
    secret.withUnsafeBufferPointer { sp in
        input.withUnsafeBufferPointer { ip in
            mac.withUnsafeMutableBufferPointer { mp in
                CCHmac(ha, sp.baseAddress ?? UnsafePointer(bitPattern: 1), sp.count, ip.baseAddress, ip.count, mp.baseAddress)
            }
        }
    }
    if dec[2].count != hl || Array(mac[0..<hl]) != dec[2] { return "BAD_SIGNATURE" }
    if let exp = pl["exp"]?.number, now >= exp + leeway { return "EXPIRED" }
    if let nbf = pl["nbf"]?.number, now + leeway < nbf { return "NOT_YET_VALID" }
    guard let iss = pl["iss"]?.string, eq(iss, issuer) else { return "BAD_ISSUER" }
    var ok = false
    if let aud = pl["aud"], let ca = audience {
        switch aud {
        case .str(let s): ok = Array(s.utf8) == Array(ca.utf8)
        case .arr(let a): ok = a.contains { if let s = $0.string { return Array(s.utf8) == Array(ca.utf8) }; return false }
        default: ok = false
        }
    }
    if !ok { return "BAD_AUDIENCE" }
    let sub = pl["sub"]?.string ?? ""
    var scopes: [[UInt8]] = []
    if let sc = pl["scope"]?.string {
        var seen = Set<[UInt8]>()
        for piece in Array(sc.utf8).split(separator: 32) {
            let a = Array(piece)
            if seen.insert(a).inserted { scopes.append(a) }
        }
        scopes.sort { $0.lexicographicallyPrecedes($1) }
    }
    return "VALID sub=" + sub + " scope=" + scopes.map { String(decoding: $0, as: UTF8.self) }.joined(separator: ",")
}

let raw = Array(FileManager.default.contents(atPath: dir + "/tokens.txt") ?? Data())
var out = ""
var n = 0, valid = 0, invalid = 0
for lineSub in raw.split(separator: 10, omittingEmptySubsequences: false) {
    var line = Array(lineSub)
    if line.last == 13 { line.removeLast() }
    while let f = line.first, f == 32 || f == 9 { line.removeFirst() }
    while let l = line.last, l == 32 || l == 9 { line.removeLast() }
    if line.isEmpty { continue }
    n += 1
    let r = check(line)
    if r.hasPrefix("VALID") { valid += 1 } else { invalid += 1 }
    out += "\(n): \(r)\n"
}
out += "\nvalid=\(valid) invalid=\(invalid)\n"
FileHandle.standardOutput.write(out.data(using: .utf8)!)
