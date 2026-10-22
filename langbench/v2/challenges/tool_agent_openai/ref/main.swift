import Foundation

indirect enum J {
    case null
    case bool(Bool)
    case num(String)
    case str(String)
    case arr([J])
    case obj([(String, J)])

    subscript(_ k: String) -> J? {
        if case .obj(let v) = self {
            for (kk, vv) in v where kk == k { return vv }
        }
        return nil
    }
    var string: String? { if case .str(let s) = self { return s }; return nil }
}

struct Parser {
    let s: [UInt8]
    var i = 0
    init(_ str: String) { s = Array(str.utf8) }
    mutating func ws() { while i < s.count && (s[i] == 32 || s[i] == 9 || s[i] == 10 || s[i] == 13) { i += 1 } }
    mutating func val() -> J {
        ws()
        let c = s[i]
        if c == UInt8(ascii: "{") {
            i += 1; var v: [(String, J)] = []; ws()
            if s[i] == UInt8(ascii: "}") { i += 1; return .obj(v) }
            while true {
                ws(); let k = str(); ws(); i += 1
                let x = val(); v.append((k, x)); ws()
                let d = s[i]; i += 1
                if d == UInt8(ascii: "}") { return .obj(v) }
            }
        }
        if c == UInt8(ascii: "[") {
            i += 1; var v: [J] = []; ws()
            if s[i] == UInt8(ascii: "]") { i += 1; return .arr(v) }
            while true {
                v.append(val()); ws()
                let d = s[i]; i += 1
                if d == UInt8(ascii: "]") { return .arr(v) }
            }
        }
        if c == UInt8(ascii: "\"") { return .str(str()) }
        if c == UInt8(ascii: "t") { i += 4; return .bool(true) }
        if c == UInt8(ascii: "f") { i += 5; return .bool(false) }
        if c == UInt8(ascii: "n") { i += 4; return .null }
        let st = i
        while i < s.count, "+-0123456789.eE".utf8.contains(s[i]) { i += 1 }
        return .num(String(decoding: s[st..<i], as: UTF8.self))
    }
    mutating func hex4() -> UInt32 {
        let h = String(decoding: s[i..<i+4], as: UTF8.self); i += 4
        return UInt32(h, radix: 16) ?? 0
    }
    mutating func str() -> String {
        i += 1
        var b: [UInt8] = []
        while true {
            let c = s[i]; i += 1
            if c == UInt8(ascii: "\"") { return String(decoding: b, as: UTF8.self) }
            if c == UInt8(ascii: "\\") {
                let e = s[i]; i += 1
                var scalar: UInt32
                switch e {
                case UInt8(ascii: "b"): scalar = 8
                case UInt8(ascii: "f"): scalar = 12
                case UInt8(ascii: "n"): scalar = 10
                case UInt8(ascii: "r"): scalar = 13
                case UInt8(ascii: "t"): scalar = 9
                case UInt8(ascii: "u"):
                    scalar = hex4()
                    if scalar >= 0xD800 && scalar < 0xDC00 && i + 6 <= s.count && s[i] == UInt8(ascii: "\\") && s[i+1] == UInt8(ascii: "u") {
                        let save = i
                        i += 2
                        let lo = hex4()
                        if lo >= 0xDC00 && lo < 0xE000 {
                            scalar = 0x10000 + ((scalar - 0xD800) << 10) + (lo - 0xDC00)
                        } else { i = save }
                    }
                default: scalar = UInt32(e)
                }
                let u = Unicode.Scalar(scalar) ?? Unicode.Scalar(0xFFFD)!
                b.append(contentsOf: Array(String(Character(u)).utf8))
            } else { b.append(c) }
        }
    }
}

func parse(_ s: String) -> J { var p = Parser(s); return p.val() }

func esc(_ s: String) -> String {
    var b = "\""
    for u in s.unicodeScalars {
        switch u.value {
        case 0x22: b += "\\\""
        case 0x5C: b += "\\\\"
        case 8: b += "\\b"
        case 12: b += "\\f"
        case 10: b += "\\n"
        case 13: b += "\\r"
        case 9: b += "\\t"
        case 0..<0x20: b += String(format: "\\u%04x", u.value)
        default: b.unicodeScalars.append(u)
        }
    }
    return b + "\""
}

func write(_ v: J) -> String {
    switch v {
    case .null: return "null"
    case .bool(let x): return x ? "true" : "false"
    case .num(let t): return t
    case .str(let s): return esc(s)
    case .arr(let a): return "[" + a.map(write).joined(separator: ",") + "]"
    case .obj(let o): return "{" + o.map { esc($0.0) + ":" + write($0.1) }.joined(separator: ",") + "}"
    }
}

func toInt(_ v: J?) -> Int128 {
    if case .num(let t)? = v {
        if let x = Int128(t) { return x }
        if let d = Double(t) { return Int128(d) }
    }
    return 0
}

func normInt(_ s: String) -> String {
    var t = Substring(s.trimmingCharacters(in: .whitespaces))
    while t.count > 1 && t.first == "0" { t = t.dropFirst() }
    return t.isEmpty ? "0" : String(t)
}

func csv(_ str: String) -> [[String]] {
    var c = Array(str.unicodeScalars)
    if let f = c.first, f.value == 0xFEFF { c.removeFirst() }
    let n = c.count
    var i = 0
    var rows: [[String]] = []
    while i < n {
        var row: [String] = []
        while true {
            var f = String.UnicodeScalarView()
            if i < n && c[i] == "\"" {
                i += 1
                while i < n {
                    if c[i] == "\"" {
                        if i + 1 < n && c[i+1] == "\"" { f.append("\""); i += 2 }
                        else { i += 1; break }
                    } else { f.append(c[i]); i += 1 }
                }
            }
            while i < n && c[i] != "," && c[i] != "\r" && c[i] != "\n" { f.append(c[i]); i += 1 }
            row.append(String(f))
            if i < n && c[i] == "," { i += 1; continue }
            if i < n && c[i] == "\r" { i += 1 }
            if i < n && c[i] == "\n" { i += 1 }
            break
        }
        rows.append(row)
    }
    return rows
}

func post(host: String, port: Int, hostHdr: String, path: String, key: String, body: String) -> (Int, String) {
    var hints = addrinfo()
    hints.ai_family = AF_UNSPEC
    hints.ai_socktype = SOCK_STREAM
    var res: UnsafeMutablePointer<addrinfo>? = nil
    getaddrinfo(host, String(port), &hints, &res)
    var fd: Int32 = -1
    var p = res
    while let ai = p {
        fd = socket(ai.pointee.ai_family, ai.pointee.ai_socktype, ai.pointee.ai_protocol)
        if fd >= 0 {
            if connect(fd, ai.pointee.ai_addr, ai.pointee.ai_addrlen) == 0 { break }
            close(fd); fd = -1
        }
        p = ai.pointee.ai_next
    }
    freeaddrinfo(res)
    let bb = Array(body.utf8)
    let hdr = "POST \(path) HTTP/1.1\r\nHost: \(hostHdr)\r\nAuthorization: Bearer \(key)\r\nContent-Type: application/json\r\nContent-Length: \(bb.count)\r\nConnection: close\r\n\r\n"
    let data = Array(hdr.utf8) + bb
    var off = 0
    while off < data.count {
        let w = data[off...].withUnsafeBufferPointer { Darwin.write(fd, $0.baseAddress, $0.count) }
        if w <= 0 { break }
        off += w
    }
    var all: [UInt8] = []
    var buf = [UInt8](repeating: 0, count: 65536)
    while true {
        let r = read(fd, &buf, buf.count)
        if r <= 0 { break }
        all.append(contentsOf: buf[0..<r])
    }
    close(fd)
    var sep = -1
    if all.count >= 4 {
        for k in 0...(all.count - 4) where all[k] == 13 && all[k+1] == 10 && all[k+2] == 13 && all[k+3] == 10 { sep = k; break }
    }
    let head = String(decoding: all[0..<sep], as: UTF8.self)
    let lines = head.components(separatedBy: "\r\n")
    let status = Int(lines[0].split(separator: " ")[1]) ?? 0
    var cl = -1
    for l in lines.dropFirst() {
        if let ci = l.firstIndex(of: ":") {
            if l[..<ci].trimmingCharacters(in: .whitespaces).lowercased() == "content-length" {
                cl = Int(l[l.index(after: ci)...].trimmingCharacters(in: .whitespaces)) ?? -1
            }
        }
    }
    let start = sep + 4
    let end = cl >= 0 ? min(all.count, start + cl) : all.count
    return (status, String(decoding: all[start..<end], as: UTF8.self))
}

let args = CommandLine.arguments
let dir = URL(fileURLWithPath: args[1])
let base = args[2]
let rest = String(base[base.range(of: "://")!.upperBound...])
var hp = rest, prefix = ""
if let s = rest.firstIndex(of: "/") { hp = String(rest[..<s]); prefix = String(rest[s...]) }
var host = hp, port = 80
if let c = hp.lastIndex(of: ":") { host = String(hp[..<c]); port = Int(hp[hp.index(after: c)...]) ?? 80 }

func readFile(_ name: String) -> String {
    let d = (try? Data(contentsOf: dir.appendingPathComponent(name))) ?? Data()
    return String(decoding: d, as: UTF8.self)
}

let task = parse(readFile("task.json"))
let rates = parse(readFile("rates.json"))["rates"] ?? .null
var inv: [[UInt8]: [String]] = [:]
for r in csv(readFile("inventory.csv")).dropFirst() where r.count >= 5 { inv[Array(r[0].utf8)] = r }
let key = task["api_key"]?.string ?? ""
var messages: [J] = [
    .obj([("role", .str("system")), ("content", task["system"] ?? .null)]),
    .obj([("role", .str("user")), ("content", task["user"] ?? .null)]),
]
var out = ""
var pt: Int128 = 0, ct: Int128 = 0, tt: Int128 = 0
var requests = 0

func flushOut() {
    FileHandle.standardOutput.write(out.data(using: .utf8)!)
}

while true {
    let req: J = .obj([
        ("model", task["model"] ?? .null),
        ("messages", .arr(messages)),
        ("tools", task["tools"] ?? .arr([])),
        ("tool_choice", .str("auto")),
    ])
    let (status, body) = post(host: host, port: port, hostHdr: hp, path: prefix + "/chat/completions", key: key, body: write(req))
    requests += 1
    if status != 200 {
        let m = parse(body)["error"]?["message"]?.string ?? ""
        out += "ERROR \(status) \(m)\n"
        flushOut()
        exit(0)
    }
    let resp = parse(body)
    if let u = resp["usage"] {
        pt += toInt(u["prompt_tokens"]); ct += toInt(u["completion_tokens"]); tt += toInt(u["total_tokens"])
    }
    var choice: J = .null
    if case .arr(let a)? = resp["choices"], !a.isEmpty { choice = a[0] }
    let msg = choice["message"] ?? .null
    let fr = choice["finish_reason"]?.string ?? ""
    if fr == "tool_calls" {
        messages.append(msg)
        if let c = msg["content"]?.string { out += "SAY \(c)\n" }
        var calls: [J] = []
        if case .arr(let a)? = msg["tool_calls"] { calls = a }
        var results: [(String, String)] = []
        for call in calls {
            let id = call["id"]?.string ?? ""
            let fn = call["function"] ?? .null
            let name = fn["name"]?.string ?? ""
            let argstr = fn["arguments"]?.string ?? "{}"
            out += "CALL \(id) \(name)\n"
            var res: String
            if name == "inventory_lookup" {
                let a = parse(argstr)
                let sku = a["sku"]?.string ?? ""
                if let r = inv[Array(sku.utf8)] {
                    res = "{\"sku\":\(esc(r[0])),\"name\":\(esc(r[1])),\"price_cents\":\(normInt(r[2])),\"currency\":\(esc(r[3])),\"stock\":\(normInt(r[4]))}"
                } else {
                    res = "{\"error\":\(esc("unknown sku: " + sku))}"
                }
            } else if name == "convert_currency" {
                let a = parse(argstr)
                let amt = toInt(a["amount_cents"])
                let from = a["from"]?.string ?? ""
                let to = a["to"]?.string ?? ""
                let rf = toInt(rates[from]), rt = toInt(rates[to])
                let conv = (2 * amt * rt + rf) / (2 * rf)
                res = "{\"amount_cents\":\(conv),\"currency\":\(esc(to))}"
            } else {
                res = "{\"error\":\(esc("unknown tool: " + name))}"
            }
            messages.append(.obj([("role", .str("tool")), ("tool_call_id", .str(id)), ("content", .str(res))]))
            results.append((id, res))
        }
        for (id, r) in results { out += "RESULT \(id) \(r)\n" }
    } else {
        out += "FINAL \(msg["content"]?.string ?? "")\n"
        break
    }
}
out += "USAGE prompt=\(pt) completion=\(ct) total=\(tt) requests=\(requests)\n"
flushOut()
