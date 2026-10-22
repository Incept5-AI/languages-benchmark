import Foundation
#if canImport(Darwin)
import Darwin
#endif

var outLines: [String] = []
func emit(_ s: String) { outLines.append(s) }
func flushOut() {
    var s = outLines.joined(separator: "\n")
    if !outLines.isEmpty { s += "\n" }
    FileHandle.standardOutput.write(s.data(using: .utf8)!)
    outLines.removeAll()
}

func esc(_ s: String) -> String {
    var o = String.UnicodeScalarView()
    for u in s.unicodeScalars {
        switch u {
        case "\\": o.append("\\"); o.append("\\")
        case "\n": o.append("\\"); o.append("n")
        case "\r": o.append("\\"); o.append("r")
        case "\t": o.append("\\"); o.append("t")
        default: o.append(u)
        }
    }
    return String(o)
}

// ---------- HTTP ----------
final class Conn {
    let fd: Int32
    var buf: [UInt8] = []
    var pos = 0
    var eof = false
    init(fd: Int32) { self.fd = fd }
    func fill() -> Bool {
        if eof { return false }
        var tmp = [UInt8](repeating: 0, count: 65536)
        let n = tmp.withUnsafeMutableBytes { read(fd, $0.baseAddress, 65536) }
        if n <= 0 { eof = true; return false }
        if pos > 0 { buf.removeFirst(pos); pos = 0 }
        buf.append(contentsOf: tmp[0..<n])
        return true
    }
    func readLine() -> String? {
        while true {
            if let i = buf[pos...].firstIndex(of: 10) {
                var line = Array(buf[pos..<i])
                pos = i + 1
                if line.last == 13 { line.removeLast() }
                return String(decoding: line, as: UTF8.self)
            }
            if !fill() {
                if pos < buf.count {
                    let line = Array(buf[pos...]); pos = buf.count
                    return String(decoding: line, as: UTF8.self)
                }
                return nil
            }
        }
    }
    func readN(_ n: Int) -> [UInt8] {
        while buf.count - pos < n { if !fill() { break } }
        let k = min(n, buf.count - pos)
        let r = Array(buf[pos..<(pos + k)])
        pos += k
        return r
    }
    func readAll() -> [UInt8] {
        while fill() {}
        let r = Array(buf[pos...]); pos = buf.count
        return r
    }
}

var host = "127.0.0.1"
var port: UInt16 = 80
var prefix = ""
var apiKey = ""

func post(_ body: [UInt8]) -> (Int, [UInt8]) {
    var hints = addrinfo()
    hints.ai_family = AF_INET
    hints.ai_socktype = SOCK_STREAM
    var res: UnsafeMutablePointer<addrinfo>? = nil
    guard getaddrinfo(host, String(port), &hints, &res) == 0, let ai = res else { return (0, []) }
    let fd = socket(ai.pointee.ai_family, ai.pointee.ai_socktype, ai.pointee.ai_protocol)
    if connect(fd, ai.pointee.ai_addr, ai.pointee.ai_addrlen) != 0 { freeaddrinfo(res); return (0, []) }
    freeaddrinfo(res)
    let head = "POST \(prefix)/v1/messages HTTP/1.1\r\nHost: \(host):\(port)\r\nContent-Length: \(body.count)\r\nx-api-key: \(apiKey)\r\nanthropic-version: 2023-06-01\r\ncontent-type: application/json\r\n\r\n"
    var req = Array(head.utf8)
    req.append(contentsOf: body)
    var off = 0
    while off < req.count {
        let n = req[off...].withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        if n <= 0 { break }
        off += n
    }
    let c = Conn(fd: fd)
    defer { close(fd) }
    let sl = c.readLine() ?? ""
    let parts = sl.split(separator: " ")
    let status = parts.count > 1 ? Int(parts[1]) ?? 0 : 0
    var cl = -1
    var chunked = false
    while let h = c.readLine(), !h.isEmpty {
        guard let i = h.firstIndex(of: ":") else { continue }
        let k = h[..<i].trimmingCharacters(in: .whitespaces).lowercased()
        let v = h[h.index(after: i)...].trimmingCharacters(in: .whitespaces)
        if k == "content-length" { cl = Int(v) ?? -1 }
        if k == "transfer-encoding" && v.lowercased().contains("chunked") { chunked = true }
    }
    var out: [UInt8] = []
    if chunked {
        while let line0 = c.readLine() {
            var line = line0
            if let s = line.firstIndex(of: ";") { line = String(line[..<s]) }
            line = line.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            guard let sz = Int(line, radix: 16) else { break }
            if sz == 0 { break }
            let d = c.readN(sz)
            out.append(contentsOf: d)
            if d.count < sz { break }
            _ = c.readLine()
        }
    } else if cl >= 0 {
        out = c.readN(cl)
    } else {
        out = c.readAll()
    }
    return (status, out)
}

// ---------- SSE ----------
final class Block {
    var type = "", text = "", sig = "", id = "", name = "", json = ""
}
var uIn = 0, uOut = 0, uCache = 0

func intOf(_ v: Any?) -> Int {
    if let n = v as? NSNumber { return n.intValue }
    return 0
}

func parseSSE(_ body: [UInt8]) -> ([Block], String) {
    var blocks: [Int: Block] = [:]
    var stop = ""
    var ev: String? = nil
    var data: [String] = []
    func dispatch() {
        defer { ev = nil; data = [] }
        if data.isEmpty { return }
        guard let d = data.joined(separator: "\n").data(using: .utf8),
              let o = (try? JSONSerialization.jsonObject(with: d, options: [.fragmentsAllowed])) as? [String: Any] else { return }
        let t = ev ?? (o["type"] as? String ?? "")
        switch t {
        case "message_start":
            if let m = o["message"] as? [String: Any], let u = m["usage"] as? [String: Any] {
                uIn += intOf(u["input_tokens"]); uCache += intOf(u["cache_read_input_tokens"])
            }
        case "content_block_start":
            if let cb = o["content_block"] as? [String: Any] {
                let b = Block()
                b.type = cb["type"] as? String ?? ""
                b.id = cb["id"] as? String ?? ""
                b.name = cb["name"] as? String ?? ""
                blocks[intOf(o["index"])] = b
            }
        case "content_block_delta":
            if let b = blocks[intOf(o["index"])], let dl = o["delta"] as? [String: Any] {
                switch dl["type"] as? String ?? "" {
                case "thinking_delta": b.text += dl["thinking"] as? String ?? ""
                case "signature_delta": b.sig += dl["signature"] as? String ?? ""
                case "text_delta": b.text += dl["text"] as? String ?? ""
                case "input_json_delta": b.json += dl["partial_json"] as? String ?? ""
                default: break
                }
            }
        case "message_delta":
            if let dl = o["delta"] as? [String: Any], let s = dl["stop_reason"] as? String { stop = s }
            if let u = o["usage"] as? [String: Any] { uOut += intOf(u["output_tokens"]) }
        default: break
        }
    }
    var lines: [ArraySlice<UInt8>] = body.split(separator: 10, omittingEmptySubsequences: false)
    if let l = lines.last, l.isEmpty { lines.removeLast() }
    for var raw in lines {
        if raw.last == 13 { raw = raw.dropLast() }
        let line = String(decoding: raw, as: UTF8.self)
        if line.isEmpty { dispatch(); continue }
        if line.hasPrefix(":") { continue }
        var f = line, v = ""
        if let c = line.firstIndex(of: ":") {
            f = String(line[..<c])
            v = String(line[line.index(after: c)...])
            if v.hasPrefix(" ") { v.removeFirst() }
        }
        if f == "event" { ev = v } else if f == "data" { data.append(v) }
    }
    dispatch()
    return (blocks.keys.sorted().map { blocks[$0]! }, stop)
}

func printReply(_ bl: [Block], _ stop: String) {
    for b in bl {
        switch b.type {
        case "thinking": emit("thinking " + esc(b.text)); emit("signature " + esc(b.sig))
        case "text": emit("text " + esc(b.text))
        case "tool_use": emit("tool_use \(esc(b.id)) \(esc(b.name)) \(esc(b.json))")
        default: break
        }
    }
    emit("stop_reason " + esc(stop))
}

func runTool(_ name: String, _ input: [String: Any]) -> (Bool, String) {
    if name == "measure" {
        let text = input["text"] as? String ?? ""
        let unit = input["unit"] as? String ?? (input["unit"] == nil ? "" : "\(input["unit"]!)")
        switch unit {
        case "code_points": return (true, String(text.unicodeScalars.count))
        case "utf8_bytes": return (true, String(text.utf8.count))
        case "utf16_units": return (true, String(text.utf16.count))
        default: return (false, "unsupported unit: \(unit)")
        }
    }
    if name == "reverse" {
        let text = input["text"] as? String ?? ""
        var v = String.UnicodeScalarView()
        v.append(contentsOf: text.unicodeScalars.reversed())
        return (true, String(v))
    }
    return (false, "unknown tool: \(name)")
}

func handleError(_ status: Int, _ body: [UInt8]) -> Bool {
    if status == 200 { return false }
    var et = "", em = ""
    if let o = (try? JSONSerialization.jsonObject(with: Data(body), options: [.fragmentsAllowed])) as? [String: Any],
       let e = o["error"] as? [String: Any] {
        et = e["type"] as? String ?? ""; em = e["message"] as? String ?? ""
    }
    emit("ERROR \(status) \(esc(et)): \(esc(em))")
    return true
}

func run() {
    let args = CommandLine.arguments
    let dataDir = args[1], base = args[2]
    var rest = base
    if rest.hasPrefix("http://") { rest.removeFirst(7) }
    var hp = rest
    if let i = rest.firstIndex(of: "/") {
        hp = String(rest[..<i]); prefix = String(rest[i...])
        while prefix.hasSuffix("/") { prefix.removeLast() }
    }
    if let i = hp.lastIndex(of: ":") {
        host = String(hp[..<i]); port = UInt16(hp[hp.index(after: i)...]) ?? 80
    } else { host = hp }
    guard let rd = FileManager.default.contents(atPath: dataDir + "/request.json"),
          let req = (try? JSONSerialization.jsonObject(with: rd, options: [.fragmentsAllowed])) as? [String: Any] else { return }
    apiKey = req["api_key"] as? String ?? ""
    let user: [String: Any] = ["role": "user", "content": req["prompt"] ?? NSNull()]
    func body(_ msgs: [Any]) -> [UInt8] {
        let b: [String: Any] = [
            "model": req["model"] ?? NSNull(),
            "max_tokens": req["max_tokens"] ?? NSNull(),
            "stream": true,
            "thinking": ["type": "enabled", "budget_tokens": req["thinking_budget"] ?? NSNull()] as [String: Any],
            "system": req["system"] ?? NSNull(),
            "tools": req["tools"] ?? NSNull(),
            "messages": msgs,
        ]
        let d = try! JSONSerialization.data(withJSONObject: b, options: [.fragmentsAllowed])
        return [UInt8](d)
    }
    emit("request 1")
    var msgs: [Any] = [user]
    let (st1, b1) = post(body(msgs))
    if handleError(st1, b1) { return }
    let (r1, s1) = parseSSE(b1)
    printReply(r1, s1)
    var asst: [Any] = [], results: [Any] = []
    for b in r1 {
        switch b.type {
        case "thinking": asst.append(["type": "thinking", "thinking": b.text, "signature": b.sig])
        case "text": asst.append(["type": "text", "text": b.text])
        case "tool_use":
            var input: Any = [String: Any]()
            if !b.json.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               let p = try? JSONSerialization.jsonObject(with: b.json.data(using: .utf8)!, options: [.fragmentsAllowed]) {
                input = p
            }
            asst.append(["type": "tool_use", "id": b.id, "name": b.name, "input": input] as [String: Any])
            let (ok, res) = runTool(b.name, input as? [String: Any] ?? [:])
            emit("\(ok ? "result" : "error") \(esc(b.id)) \(esc(res))")
            var tr: [String: Any] = ["type": "tool_result", "tool_use_id": b.id, "content": res]
            if !ok { tr["is_error"] = true }
            results.append(tr)
        default: break
        }
    }
    emit("request 2")
    msgs.append(["role": "assistant", "content": asst] as [String: Any])
    msgs.append(["role": "user", "content": results] as [String: Any])
    let (st2, b2) = post(body(msgs))
    if handleError(st2, b2) { return }
    let (r2, s2) = parseSSE(b2)
    printReply(r2, s2)
    emit("usage input=\(uIn) output=\(uOut) cache_read=\(uCache)")
}

signal(SIGPIPE, SIG_IGN)
run()
flushOut()
exit(0)
