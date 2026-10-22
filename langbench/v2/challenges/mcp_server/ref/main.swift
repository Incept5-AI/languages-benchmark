import Foundation

typealias B = [UInt8]
indirect enum V {
    case null
    case bool(Bool)
    case num(String, Bool)
    case str(B)
    case arr([V])
    case obj([(B, V)])
    case raw(String)
}

func bs(_ s: String) -> B { return Array(s.utf8) }
func get(_ o: [(B, V)], _ k: String) -> V? {
    let kb = bs(k)
    for (kk, v) in o where kk == kb { return v }
    return nil
}

struct PErr: Error {}

func pushCp(_ b: inout B, _ cp: UInt32) {
    if cp < 0x80 { b.append(UInt8(cp)) }
    else if cp < 0x800 { b.append(UInt8(0xC0 | (cp >> 6))); b.append(UInt8(0x80 | (cp & 0x3F))) }
    else if cp < 0x10000 { b.append(UInt8(0xE0 | (cp >> 12))); b.append(UInt8(0x80 | ((cp >> 6) & 0x3F))); b.append(UInt8(0x80 | (cp & 0x3F))) }
    else { b.append(UInt8(0xF0 | (cp >> 18))); b.append(UInt8(0x80 | ((cp >> 12) & 0x3F))); b.append(UInt8(0x80 | ((cp >> 6) & 0x3F))); b.append(UInt8(0x80 | (cp & 0x3F))) }
}

func cps(_ b: B) -> [UInt32] {
    var v: [UInt32] = []
    var i = 0
    while i < b.count {
        let c = UInt32(b[i])
        if c < 0x80 { v.append(c); i += 1 }
        else if c >> 5 == 6 && i + 1 < b.count { v.append(((c & 0x1F) << 6) | (UInt32(b[i + 1]) & 0x3F)); i += 2 }
        else if c >> 4 == 14 && i + 2 < b.count { v.append(((c & 0x0F) << 12) | ((UInt32(b[i + 1]) & 0x3F) << 6) | (UInt32(b[i + 2]) & 0x3F)); i += 3 }
        else if i + 3 < b.count { v.append(((c & 0x07) << 18) | ((UInt32(b[i + 1]) & 0x3F) << 12) | ((UInt32(b[i + 2]) & 0x3F) << 6) | (UInt32(b[i + 3]) & 0x3F)); i += 4 }
        else { v.append(0xFFFD); i += 1 }
    }
    return v
}

final class Parser {
    let s: B
    var p = 0
    init(_ s: B) { self.s = s }
    func peek() -> UInt8? { return p < s.count ? s[p] : nil }
    func ws() { while p < s.count && (s[p] == 32 || s[p] == 9 || s[p] == 10 || s[p] == 13) { p += 1 } }
    func isDigit(_ c: UInt8?) -> Bool { if let c = c { return c >= 48 && c <= 57 }; return false }
    func value() throws -> V {
        guard let c = peek() else { throw PErr() }
        switch c {
        case UInt8(ascii: "{"): return try obj()
        case UInt8(ascii: "["): return try arr()
        case UInt8(ascii: "\""): return .str(try str())
        case UInt8(ascii: "t"): try lit("true"); return .bool(true)
        case UInt8(ascii: "f"): try lit("false"); return .bool(false)
        case UInt8(ascii: "n"): try lit("null"); return .null
        default:
            if c == UInt8(ascii: "-") || isDigit(c) { return try num() }
            throw PErr()
        }
    }
    func lit(_ w: String) throws {
        let wb = bs(w)
        if p + wb.count > s.count || Array(s[p..<(p + wb.count)]) != wb { throw PErr() }
        p += wb.count
    }
    func obj() throws -> V {
        p += 1
        var m: [(B, V)] = []
        ws()
        if peek() == UInt8(ascii: "}") { p += 1; return .obj(m) }
        while true {
            ws()
            if peek() != UInt8(ascii: "\"") { throw PErr() }
            let k = try str()
            ws()
            if peek() != UInt8(ascii: ":") { throw PErr() }
            p += 1; ws()
            let v = try value()
            m.removeAll { $0.0 == k }
            m.append((k, v))
            ws()
            guard let c = peek() else { throw PErr() }
            p += 1
            if c == UInt8(ascii: "}") { return .obj(m) }
            if c != UInt8(ascii: ",") { throw PErr() }
        }
    }
    func arr() throws -> V {
        p += 1
        var a: [V] = []
        ws()
        if peek() == UInt8(ascii: "]") { p += 1; return .arr(a) }
        while true {
            ws()
            a.append(try value())
            ws()
            guard let c = peek() else { throw PErr() }
            p += 1
            if c == UInt8(ascii: "]") { return .arr(a) }
            if c != UInt8(ascii: ",") { throw PErr() }
        }
    }
    func hex4() throws -> UInt32 {
        if p + 4 > s.count { throw PErr() }
        var v: UInt32 = 0
        for _ in 0..<4 {
            let c = s[p]; p += 1
            var d: UInt32
            if c >= 48 && c <= 57 { d = UInt32(c - 48) }
            else if c >= 97 && c <= 102 { d = UInt32(c - 87) }
            else if c >= 65 && c <= 70 { d = UInt32(c - 55) }
            else { throw PErr() }
            v = v * 16 + d
        }
        return v
    }
    func str() throws -> B {
        p += 1
        var b: B = []
        while true {
            guard let c = peek() else { throw PErr() }
            p += 1
            if c == UInt8(ascii: "\"") { return b }
            if c < 0x20 { throw PErr() }
            if c == UInt8(ascii: "\\") {
                guard let e = peek() else { throw PErr() }
                p += 1
                switch e {
                case UInt8(ascii: "\""), UInt8(ascii: "\\"), UInt8(ascii: "/"): b.append(e)
                case UInt8(ascii: "b"): b.append(8)
                case UInt8(ascii: "f"): b.append(12)
                case UInt8(ascii: "n"): b.append(10)
                case UInt8(ascii: "r"): b.append(13)
                case UInt8(ascii: "t"): b.append(9)
                case UInt8(ascii: "u"):
                    var cp = try hex4()
                    if cp >= 0xD800 && cp < 0xDC00 && p + 1 < s.count && s[p] == UInt8(ascii: "\\") && s[p + 1] == UInt8(ascii: "u") {
                        let save = p
                        p += 2
                        let lo = try hex4()
                        if lo >= 0xDC00 && lo < 0xE000 { cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00) } else { p = save }
                    }
                    pushCp(&b, cp)
                default: throw PErr()
                }
            } else { b.append(c) }
        }
    }
    func num() throws -> V {
        let st = p
        var isInt = true
        if peek() == UInt8(ascii: "-") { p += 1 }
        if peek() == UInt8(ascii: "0") { p += 1 }
        else if isDigit(peek()) { while isDigit(peek()) { p += 1 } }
        else { throw PErr() }
        if peek() == UInt8(ascii: ".") {
            isInt = false; p += 1
            let d = p
            while isDigit(peek()) { p += 1 }
            if p == d { throw PErr() }
        }
        if peek() == UInt8(ascii: "e") || peek() == UInt8(ascii: "E") {
            isInt = false; p += 1
            if peek() == UInt8(ascii: "+") || peek() == UInt8(ascii: "-") { p += 1 }
            let d = p
            while isDigit(peek()) { p += 1 }
            if p == d { throw PErr() }
        }
        return .num(String(decoding: s[st..<p], as: UTF8.self), isInt)
    }
}

func parse(_ s: B) throws -> V {
    let p = Parser(s)
    p.ws()
    let v = try p.value()
    p.ws()
    if p.p != s.count { throw PErr() }
    return v
}

let hexd = Array("0123456789abcdef".utf8)
func u4(_ o: inout B, _ c: UInt32) {
    o.append(UInt8(ascii: "\\")); o.append(UInt8(ascii: "u"))
    o.append(hexd[Int((c >> 12) & 15)]); o.append(hexd[Int((c >> 8) & 15)]); o.append(hexd[Int((c >> 4) & 15)]); o.append(hexd[Int(c & 15)])
}
func serStr(_ o: inout B, _ s: B) {
    o.append(34)
    for cp in cps(s) {
        if cp == 0x22 { o.append(92); o.append(34) }
        else if cp == 0x5C { o.append(92); o.append(92) }
        else if cp >= 0x20 && cp < 0x7F { o.append(UInt8(cp)) }
        else if cp < 0x10000 { u4(&o, cp) }
        else { let c = cp - 0x10000; u4(&o, 0xD800 + (c >> 10)); u4(&o, 0xDC00 + (c & 0x3FF)) }
    }
    o.append(34)
}
func ser(_ o: inout B, _ v: V) {
    switch v {
    case .null: o += bs("null")
    case .bool(let b): o += bs(b ? "true" : "false")
    case .num(let r, _): o += bs(r)
    case .raw(let r): o += bs(r)
    case .str(let s): serStr(&o, s)
    case .arr(let a):
        o.append(UInt8(ascii: "["))
        for (i, x) in a.enumerated() { if i > 0 { o.append(UInt8(ascii: ",")) }; ser(&o, x) }
        o.append(UInt8(ascii: "]"))
    case .obj(let m):
        o.append(UInt8(ascii: "{"))
        for (i, (k, x)) in m.enumerated() { if i > 0 { o.append(UInt8(ascii: ",")) }; serStr(&o, k); o.append(UInt8(ascii: ":")); ser(&o, x) }
        o.append(UInt8(ascii: "}"))
    }
}

let TOOLS = #"[{"name":"kv_set","title":"Set a value","description":"Store a string value under a key, replacing any previous value.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1},"value":{"type":"string"}},"required":["key","value"],"additionalProperties":false}},{"name":"kv_get","title":"Get a value","description":"Return the value stored under a key.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1}},"required":["key"],"additionalProperties":false}},{"name":"kv_list","title":"List keys","description":"List the stored keys that start with a prefix, sorted by Unicode code point.","inputSchema":{"type":"object","properties":{"prefix":{"type":"string"}},"additionalProperties":false},"outputSchema":{"type":"object","properties":{"keys":{"type":"array","items":{"type":"string"}},"count":{"type":"integer"}},"required":["keys","count"]}},{"name":"sum","title":"Sum integers","description":"Add up a non-empty list of integers.","inputSchema":{"type":"object","properties":{"numbers":{"type":"array","items":{"type":"integer"},"minItems":1}},"required":["numbers"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"sum":{"type":"integer"},"count":{"type":"integer"}},"required":["sum","count"]},"annotations":{"readOnlyHint":true,"idempotentHint":true}},{"name":"text_stats","title":"Text statistics","description":"Count the words, Unicode code points and UTF-8 bytes of a text.","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"words":{"type":"integer"},"codepoints":{"type":"integer"},"utf8_bytes":{"type":"integer"}},"required":["words","codepoints","utf8_bytes"]},"annotations":{"readOnlyHint":true}}]"#

func S(_ x: String) -> V { return .str(bs(x)) }
func obj(_ items: [(String, V)]) -> V { return .obj(items.map { (bs($0.0), $0.1) }) }
func int(_ n: Int64) -> V { return .num(String(n), true) }

func errorResp(_ id: V, _ code: Int, _ msg: B) -> B {
    var o = bs("{\"jsonrpc\":\"2.0\",\"id\":")
    ser(&o, id)
    o += bs(",\"error\":{\"code\":\(code),\"message\":")
    serStr(&o, msg)
    o += bs("}}")
    return o
}
func errorResp(_ id: V, _ code: Int, _ msg: String) -> B { return errorResp(id, code, bs(msg)) }
func resultResp(_ id: V, _ r: V) -> B {
    var o = bs("{\"jsonrpc\":\"2.0\",\"id\":")
    ser(&o, id)
    o += bs(",\"result\":")
    ser(&o, r)
    o.append(UInt8(ascii: "}"))
    return o
}
func textResult(_ t: B, _ isError: Bool) -> V {
    return obj([("content", .arr([obj([("type", S("text")), ("text", .str(t))])])), ("isError", .bool(isError))])
}
func fail(_ t: String) -> V { return textResult(bs(t), true) }
func structResult(_ sv: V) -> V {
    var t: B = []
    ser(&t, sv)
    return obj([("content", .arr([obj([("type", S("text")), ("text", .str(t))])])), ("structuredContent", sv), ("isError", .bool(false))])
}

// (name, isString, required, nonEmpty)
let SPECS: [String: [(String, Bool, Bool, Bool)]] = [
    "kv_set": [("key", true, true, true), ("value", true, true, false)],
    "kv_get": [("key", true, true, true)],
    "kv_list": [("prefix", true, false, false)],
    "sum": [("numbers", false, true, true)],
    "text_stats": [("text", true, true, false)],
]

var initialized = false
var kv: [B: B] = [:]

func lt(_ a: B, _ b: B) -> Bool { return a.lexicographicallyPrecedes(b) }

func callTool(_ name: String, _ argsV: V?) -> V {
    let sp = SPECS[name]!
    var args: [(B, V)] = []
    if let a = argsV {
        if case .obj(let m) = a { args = m } else { return fail("arguments must be an object") }
    }
    var unknown: B? = nil
    for (k, _) in args {
        if !sp.contains(where: { bs($0.0) == k }) {
            if unknown == nil || lt(k, unknown!) { unknown = k }
        }
    }
    if let u = unknown { return textResult(bs("unknown argument: ") + u, true) }
    for (pn, isStr, req, nonEmpty) in sp {
        guard let v = get(args, pn) else {
            if req { return fail("missing required argument: \(pn)") }
            continue
        }
        if isStr {
            guard case .str(let x) = v else { return fail("argument \(pn) must be a string") }
            if nonEmpty && x.isEmpty { return fail("argument \(pn) must not be empty") }
        } else {
            guard case .arr(let a) = v else { return fail("argument \(pn) must be an array of integers") }
            for x in a { if case .num(_, true) = x {} else { return fail("argument \(pn) must be an array of integers") } }
            if nonEmpty && a.isEmpty { return fail("argument \(pn) must not be empty") }
        }
    }
    func gs(_ k: String) -> B { if let v = get(args, k), case .str(let x) = v { return x }; return [] }
    switch name {
    case "kv_set":
        let k = gs("key")
        let existed = kv[k] != nil
        kv[k] = gs("value")
        return textResult(bs(existed ? "updated " : "created ") + k, false)
    case "kv_get":
        let k = gs("key")
        if let v = kv[k] { return textResult(v, false) }
        return textResult(bs("key not found: ") + k, true)
    case "kv_list":
        let pre = gs("prefix")
        let keys = kv.keys.filter { $0.starts(with: pre) }.sorted(by: lt)
        return structResult(obj([("keys", .arr(keys.map { .str($0) })), ("count", int(Int64(keys.count)))]))
    case "sum":
        guard let v = get(args, "numbers"), case .arr(let a) = v else { return fail("") }
        var sum: Int64 = 0
        for x in a { if case .num(let r, _) = x { sum = sum &+ (Int64(r) ?? 0) } }
        return structResult(obj([("sum", int(sum)), ("count", int(Int64(a.count)))]))
    default:
        let t = gs("text")
        var words: Int64 = 0, n: Int64 = 0, bytes: Int64 = 0, inw = false
        for cp in cps(t) {
            n += 1
            bytes += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4
            let sep = cp == 32 || cp == 9 || cp == 10 || cp == 13
            if !sep && !inw { words += 1 }
            inw = !sep
        }
        return structResult(obj([("words", int(words)), ("codepoints", int(n)), ("utf8_bytes", int(bytes))]))
    }
}

func handle(_ line: B) -> B? {
    guard let msg = try? parse(line) else { return errorResp(.null, -32700, "Parse error") }
    guard case .obj(let m) = msg else { return errorResp(.null, -32600, "Invalid Request") }
    if get(m, "method") == nil && (get(m, "result") != nil || get(m, "error") != nil) { return nil }
    let id = get(m, "id")
    var idOk = false
    if let i = id { switch i { case .str: idOk = true; case .num(_, let isInt): idOk = isInt; default: break } }
    var jsonrpcOk = false
    if let j = get(m, "jsonrpc"), case .str(let x) = j, x == bs("2.0") { jsonrpcOk = true }
    var method: B? = nil
    if let mv = get(m, "method"), case .str(let x) = mv { method = x }
    if !jsonrpcOk || method == nil || (id != nil && !idOk) {
        return errorResp(idOk ? id! : .null, -32600, "Invalid Request")
    }
    guard let rid = id else { return nil }
    let meth = String(decoding: method!, as: UTF8.self)
    let mb = method!
    if !(mb == bs("initialize") || mb == bs("ping") || mb == bs("tools/list") || mb == bs("tools/call")) {
        return errorResp(rid, -32601, "Method not found")
    }
    var pm: [(B, V)]? = nil
    if let p = get(m, "params") {
        guard case .obj(let po) = p else { return errorResp(rid, -32602, "Invalid params") }
        pm = po
    }
    if !initialized && meth != "initialize" && meth != "ping" { return errorResp(rid, -32002, "Server not initialized") }
    switch meth {
    case "initialize":
        if initialized { return errorResp(rid, -32600, "Already initialized") }
        guard let p = pm, let pv = get(p, "protocolVersion"), case .str(let v0) = pv else { return errorResp(rid, -32602, "Invalid params") }
        var v = v0
        if !(v == bs("2025-06-18") || v == bs("2025-03-26") || v == bs("2024-11-05")) { v = bs("2025-06-18") }
        initialized = true
        var r = bs("{\"protocolVersion\":")
        serStr(&r, v)
        r += bs(",\"capabilities\":{\"tools\":{\"listChanged\":false}},\"serverInfo\":{\"name\":\"cb2-kv\",\"title\":\"CB2 key-value server\",\"version\":\"1.4.2\"}}")
        return resultResp(rid, .raw(String(decoding: r, as: UTF8.self)))
    case "ping": return resultResp(rid, .raw("{}"))
    case "tools/list": return resultResp(rid, .raw("{\"tools\":" + TOOLS + "}"))
    default:
        guard let p = pm, let nv = get(p, "name"), case .str(let nb) = nv else { return errorResp(rid, -32602, "Invalid params") }
        let ns = String(decoding: nb, as: UTF8.self)
        if SPECS[ns] == nil || bs(ns) != nb { return errorResp(rid, -32602, bs("Unknown tool: ") + nb) }
        return resultResp(rid, callTool(ns, get(p, "arguments")))
    }
}

func run() {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    let bytes = B(data)
    var out: B = []
    var st = 0
    var i = 0
    while i <= bytes.count {
        if i == bytes.count || bytes[i] == 10 {
            if i == bytes.count && st == i { break }
            let raw = Array(bytes[st..<i])
            st = i + 1
            if !raw.allSatisfy({ $0 == 32 || $0 == 9 || $0 == 13 }) {
                let line = B(String(decoding: raw, as: UTF8.self).utf8)
                if let r = handle(line) { out += r; out.append(10) }
            }
        }
        i += 1
    }
    FileHandle.standardOutput.write(Data(out))
}

let th = Thread { run(); exit(0) }
th.stackSize = 1 << 29
th.start()
while true { sleep(1000) }
