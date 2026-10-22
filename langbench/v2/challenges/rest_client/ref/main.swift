import Foundation
#if canImport(Darwin)
import Darwin
#endif

struct Resp {
    var status: Int
    var headers: [String: String]
    var body: [UInt8]
    var bodyStr: String { String(decoding: body, as: UTF8.self) }
}

struct Fatal: Error { let msg: String }

func find(_ a: [UInt8], _ pat: [UInt8], _ from: Int) -> Int? {
    if a.count < pat.count { return nil }
    var i = from
    while i + pat.count <= a.count {
        var ok = true
        for j in 0..<pat.count where a[i + j] != pat[j] { ok = false; break }
        if ok { return i }
        i += 1
    }
    return nil
}

let CRLF: [UInt8] = [13, 10]
let CRLF2: [UInt8] = [13, 10, 13, 10]

func tryParse(_ a: [UInt8], eof: Bool) -> Resp? {
    guard let he = find(a, CRLF2, 0) else { return nil }
    let head = String(decoding: a[0..<he], as: UTF8.self)
    let lines = head.components(separatedBy: "\r\n")
    let parts = lines[0].split(separator: " ", maxSplits: 2)
    let status = parts.count > 1 ? Int(parts[1].trimmingCharacters(in: .whitespaces)) ?? 0 : 0
    var headers: [String: String] = [:]
    for l in lines.dropFirst() {
        if let c = l.firstIndex(of: ":") {
            headers[l[..<c].trimmingCharacters(in: .whitespaces).lowercased()] =
                l[l.index(after: c)...].trimmingCharacters(in: .whitespaces)
        }
    }
    var p = he + 4
    if let te = headers["transfer-encoding"], te.lowercased().contains("chunked") {
        var body: [UInt8] = []
        while true {
            guard let le = find(a, CRLF, p) else { return eof ? Resp(status: status, headers: headers, body: body) : nil }
            var szs = String(decoding: a[p..<le], as: UTF8.self)
            if let s = szs.firstIndex(of: ";") { szs = String(szs[..<s]) }
            let sz = Int(szs.trimmingCharacters(in: .whitespaces), radix: 16) ?? 0
            p = le + 2
            if sz == 0 {
                while true {
                    guard let t = find(a, CRLF, p) else { return eof ? Resp(status: status, headers: headers, body: body) : nil }
                    if t == p { return Resp(status: status, headers: headers, body: body) }
                    p = t + 2
                }
            }
            if p + sz > a.count {
                if !eof { return nil }
                body.append(contentsOf: a[p...])
                return Resp(status: status, headers: headers, body: body)
            }
            body.append(contentsOf: a[p..<(p + sz)])
            p += sz
            if p + 2 > a.count { return eof ? Resp(status: status, headers: headers, body: body) : nil }
            p += 2
        }
    }
    if let cls = headers["content-length"], var n = Int(cls) {
        if a.count - p < n {
            if !eof { return nil }
            n = a.count - p
        }
        return Resp(status: status, headers: headers, body: Array(a[p..<(p + n)]))
    }
    if !eof { return nil }
    return Resp(status: status, headers: headers, body: Array(a[p...]))
}

var gHost = "127.0.0.1"
var gPort = "80"

func request(_ method: String, _ path: String, _ hdrs: [(String, String)], _ body: [UInt8]?) -> Resp {
    var hints = addrinfo()
    hints.ai_family = AF_UNSPEC
    hints.ai_socktype = SOCK_STREAM
    var res: UnsafeMutablePointer<addrinfo>? = nil
    guard getaddrinfo(gHost, gPort, &hints, &res) == 0 else { fatalError("resolve") }
    var fd: Int32 = -1
    var ai = res
    while let a = ai {
        fd = socket(a.pointee.ai_family, a.pointee.ai_socktype, a.pointee.ai_protocol)
        if fd >= 0 {
            if connect(fd, a.pointee.ai_addr, a.pointee.ai_addrlen) == 0 { break }
            close(fd)
            fd = -1
        }
        ai = a.pointee.ai_next
    }
    freeaddrinfo(res)
    if fd < 0 { fatalError("connect") }
    var req = "\(method) \(path) HTTP/1.1\r\nHost: \(gHost):\(gPort)\r\n"
    for (k, v) in hdrs { req += "\(k): \(v)\r\n" }
    if let b = body { req += "Content-Length: \(b.count)\r\n" }
    req += "Connection: close\r\n\r\n"
    var out = Array(req.utf8)
    if let b = body { out += b }
    var off = 0
    while off < out.count {
        let w = out[off...].withUnsafeBufferPointer { write(fd, $0.baseAddress, $0.count) }
        if w <= 0 { break }
        off += w
    }
    var data: [UInt8] = []
    var buf = [UInt8](repeating: 0, count: 65536)
    while true {
        let n = buf.withUnsafeMutableBufferPointer { read(fd, $0.baseAddress, $0.count) }
        if n <= 0 { break }
        data.append(contentsOf: buf[0..<n])
        if let r = tryParse(data, eof: false) { close(fd); return r }
    }
    close(fd)
    return tryParse(data, eof: true) ?? Resp(status: 0, headers: [:], body: [])
}

func isUnreservedForm(_ c: UInt8) -> Bool {
    return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || (c >= 48 && c <= 57) || c == 45 || c == 46 || c == 95 || c == 42
}

func hex(_ c: UInt8) -> String { String(format: "%%%02X", c) }

func formEnc(_ s: String) -> String {
    var o = ""
    for c in s.utf8 {
        if isUnreservedForm(c) { o.append(Character(UnicodeScalar(c))) }
        else if c == 32 { o += "+" }
        else { o += hex(c) }
    }
    return o
}

func pctEnc(_ s: String) -> String {
    var o = ""
    for c in s.utf8 {
        if (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || (c >= 48 && c <= 57) || c == 45 || c == 46 || c == 95 || c == 126 {
            o.append(Character(UnicodeScalar(c)))
        } else { o += hex(c) }
    }
    return o
}

let args = CommandLine.arguments
let cfgData = try! Data(contentsOf: URL(fileURLWithPath: args[1]).appendingPathComponent("client.json"))
let cfg = try! JSONSerialization.jsonObject(with: cfgData) as! [String: Any]
let clientId = cfg["client_id"] as! String
let clientSecret = cfg["client_secret"] as! String
let scope = cfg["scope"] as! String
let pageSize = (cfg["page_size"] as! NSNumber).int64Value

var urlStr = args[2]
if urlStr.hasPrefix("http://") { urlStr = String(urlStr.dropFirst(7)) }
var basePath = ""
var hostPort = urlStr
if let s = urlStr.firstIndex(of: "/") {
    hostPort = String(urlStr[..<s])
    basePath = String(urlStr[s...])
}
if basePath.hasSuffix("/") { basePath.removeLast() }
if let c = hostPort.lastIndex(of: ":") {
    gHost = String(hostPort[..<c])
    gPort = String(hostPort[hostPort.index(after: c)...])
} else {
    gHost = hostPort
}

func getToken() throws -> String {
    let raw = formEnc(clientId) + ":" + formEnc(clientSecret)
    let basic = Data(raw.utf8).base64EncodedString()
    let body = Array(("grant_type=client_credentials&scope=" + formEnc(scope)).utf8)
    let r = request("POST", basePath + "/oauth/token", [
        ("Authorization", "Basic " + basic),
        ("Content-Type", "application/x-www-form-urlencoded"),
        ("Accept", "application/json"),
    ], body)
    if r.status != 200 { throw Fatal(msg: "ERROR \(r.status) \(r.bodyStr)") }
    let j = try JSONSerialization.jsonObject(with: Data(r.body)) as! [String: Any]
    return j["access_token"] as? String ?? ""
}

func cents(_ s: String) -> Int64 {
    var a = Substring(s)
    var neg = false
    if a.hasPrefix("-") { neg = true; a = a.dropFirst() }
    var ip = a
    var fp = Substring("")
    if let d = a.firstIndex(of: ".") {
        ip = a[..<d]
        fp = a[a.index(after: d)...]
    }
    var fps = String(fp)
    while fps.count < 2 { fps += "0" }
    let v = Int64(ip)! * 100 + Int64(fps)!
    return neg ? -v : v
}

func fmt(_ c: Int64) -> String {
    let a = c.magnitude
    let frac = a % 100
    return (c < 0 ? "-" : "") + "\(a / 100)." + (frac < 10 ? "0" : "") + "\(frac)"
}

func lexLess(_ a: [UInt8], _ b: [UInt8]) -> Bool { a.lexicographicallyPrecedes(b) }

struct Key: Hashable { let name: [UInt8]; let cur: [UInt8] }

var totals: [Key: Int64] = [:]
var statuses: [[UInt8]: Int] = [:]
var nOrders = 0, pages = 0, retries = 0, reauth = 0

func run() throws {
    var token = try getToken()
    var fresh = true
    var cursor: String? = nil
    while true {
        var p = basePath + "/orders?limit=\(pageSize)"
        if let c = cursor { p += "&cursor=" + pctEnc(c) }
        let r = request("GET", p, [("Authorization", "Bearer " + token), ("Accept", "application/json")], nil)
        let wasFresh = fresh
        fresh = false
        if r.status == 200 {
            pages += 1
            let m = try JSONSerialization.jsonObject(with: Data(r.body)) as! [String: Any]
            if let data = m["data"] as? [Any] {
                for item in data {
                    guard let o = item as? [String: Any] else { continue }
                    nOrders += 1
                    let st = o["status"] as? String ?? ""
                    statuses[Array(st.utf8), default: 0] += 1
                    if st != "cancelled" {
                        let name = (o["customer"] as? [String: Any])?["name"] as? String ?? ""
                        let cur = o["currency"] as? String ?? ""
                        let amt = o["amount"] as? String ?? "0"
                        totals[Key(name: Array(name.utf8), cur: Array(cur.utf8)), default: 0] += cents(amt)
                    }
                }
            }
            if let nc = m["next_cursor"] as? String { cursor = nc } else { break }
        } else if r.status == 429 {
            retries += 1
            let secs = Int(r.headers["retry-after"]?.trimmingCharacters(in: .whitespaces) ?? "1") ?? 1
            usleep(UInt32(secs * 1_000_000 + 50_000))
        } else if r.status == 503 {
            retries += 1
            usleep(100_000)
        } else if r.status == 401 && !wasFresh {
            token = try getToken()
            reauth += 1
            fresh = true
        } else {
            throw Fatal(msg: "ERROR \(r.status) \(r.bodyStr)")
        }
    }
}

var outBytes: [UInt8] = []
do {
    try run()
    let keys = totals.keys.sorted { a, b in
        if a.name != b.name { return lexLess(a.name, b.name) }
        return lexLess(a.cur, b.cur)
    }
    for k in keys {
        outBytes += Array("total ".utf8) + k.cur + Array(" \(fmt(totals[k]!)) ".utf8) + k.name + [10]
    }
    for s in statuses.keys.sorted(by: lexLess) {
        outBytes += Array("status ".utf8) + s + Array(" \(statuses[s]!)\n".utf8)
    }
    outBytes += Array("orders \(nOrders) pages \(pages) retries \(retries) reauth \(reauth)\n".utf8)
} catch let f as Fatal {
    outBytes = Array((f.msg + "\n").utf8)
}
FileHandle.standardOutput.write(Data(outBytes))
