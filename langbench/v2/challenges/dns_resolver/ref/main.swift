import Foundation
#if canImport(Darwin)
import Darwin
#endif

func u16(_ m: [UInt8], _ p: Int) -> Int { return (Int(m[p]) << 8) | Int(m[p + 1]) }

func readName(_ m: [UInt8], _ start: Int) -> (String, Int) {
    var pos = start
    var labels: [String] = []
    var end = -1
    var jumps = 0
    while pos < m.count {
        let l = Int(m[pos])
        if l & 0xC0 == 0xC0 {
            if pos + 1 >= m.count { break }
            if end < 0 { end = pos + 2 }
            jumps += 1
            if jumps > 255 { break }
            pos = ((l & 0x3F) << 8) | Int(m[pos + 1])
            continue
        }
        if l == 0 { if end < 0 { end = pos + 1 }; break }
        var s = ""
        var i = pos + 1
        while i < min(pos + 1 + l, m.count) {
            var b = m[i]
            if b >= 65 && b <= 90 { b += 32 }
            s.unicodeScalars.append(Unicode.Scalar(b))
            i += 1
        }
        labels.append(s)
        pos += 1 + l
    }
    return (labels.isEmpty ? "." : labels.joined(separator: "."), end < 0 ? m.count : end)
}

func typeName(_ t: Int) -> String {
    switch t {
    case 1: return "A"
    case 5: return "CNAME"
    case 15: return "MX"
    case 16: return "TXT"
    case 28: return "AAAA"
    default: return "TYPE\(t)"
    }
}

func aaaa(_ m: [UInt8], _ s: Int) -> String {
    let g = (0..<8).map { u16(m, s + 2 * $0) }
    var bs = -1, bl = 0
    var i = 0
    while i < 8 {
        if g[i] == 0 {
            var j = i
            while j < 8 && g[j] == 0 { j += 1 }
            if j - i > bl { bl = j - i; bs = i }
            i = j
        } else { i += 1 }
    }
    let h: (ArraySlice<Int>) -> String = { $0.map { String($0, radix: 16) }.joined(separator: ":") }
    if bl < 2 { return h(g[0..<8]) }
    return h(g[0..<bs]) + "::" + h(g[(bs + bl)..<8])
}

func readFull(_ fd: Int32, _ n: Int) -> [UInt8] {
    var b = [UInt8](repeating: 0, count: n)
    var got = 0
    while got < n {
        let r = b.withUnsafeMutableBytes { read(fd, $0.baseAddress! + got, n - got) }
        if r <= 0 { break }
        got += r
    }
    return b
}

let args = CommandLine.arguments
let dir = args[1]
let port = UInt16(args[2].trimmingCharacters(in: .whitespaces))!
let data = (try? Data(contentsOf: URL(fileURLWithPath: dir).appendingPathComponent("queries.txt"))) ?? Data()
let content = String(decoding: data, as: UTF8.self)

var sa = sockaddr_in()
sa.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
sa.sin_family = sa_family_t(AF_INET)
sa.sin_port = port.bigEndian
sa.sin_addr.s_addr = UInt32(0x7f000001).bigEndian
let saLen = socklen_t(MemoryLayout<sockaddr_in>.size)

let us = socket(AF_INET, SOCK_DGRAM, 0)
var out = ""
let RN = ["NOERROR", "FORMERR", "SERVFAIL", "NXDOMAIN", "NOTIMP", "REFUSED"]
let TYPES: [String: Int] = ["A": 1, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28]

for raw in content.split(separator: "\n", omittingEmptySubsequences: false) {
    let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if line.isEmpty { continue }
    let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
    var name = parts[0]
    let ty = parts.count > 1 ? parts[1] : "A"
    let qtype = TYPES[ty] ?? 1
    if name.hasSuffix(".") { name.removeLast() }
    let id = Int(UInt16.random(in: 0...UInt16.max))
    var q: [UInt8] = [UInt8(id >> 8), UInt8(id & 0xff), 1, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    if !name.isEmpty {
        for lab in name.split(separator: ".", omittingEmptySubsequences: false) {
            let b = Array(lab.utf8)
            q.append(UInt8(b.count))
            q.append(contentsOf: b)
        }
    }
    q.append(contentsOf: [0, UInt8(qtype >> 8), UInt8(qtype & 0xff), 0, 1])
    _ = q.withUnsafeBytes { qp in
        withUnsafePointer(to: &sa) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sendto(us, qp.baseAddress, q.count, 0, $0, saLen) }
        }
    }
    var buf = [UInt8](repeating: 0, count: 65536)
    var msg: [UInt8] = []
    while true {
        let n = buf.withUnsafeMutableBytes { recvfrom(us, $0.baseAddress, 65536, 0, nil, nil) }
        if n < 12 { continue }
        if u16(buf, 0) == id && (buf[2] & 0x80) != 0 { msg = Array(buf[0..<n]); break }
    }
    if msg[2] & 0x02 != 0 {
        let ts = socket(AF_INET, SOCK_STREAM, 0)
        _ = withUnsafePointer(to: &sa) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(ts, $0, saLen) }
        }
        let fr = [UInt8(q.count >> 8), UInt8(q.count & 0xff)] + q
        var w = 0
        while w < fr.count {
            let r = fr.withUnsafeBytes { write(ts, $0.baseAddress! + w, fr.count - w) }
            if r <= 0 { break }
            w += r
        }
        let lb = readFull(ts, 2)
        msg = readFull(ts, u16(lb, 0))
        close(ts)
    }
    let rc = Int(msg[3] & 0x0f)
    let lname = name.lowercased()
    out += "\(lname.isEmpty ? "." : lname) \(ty) \(rc <= 5 ? RN[rc] : "RCODE\(rc)")\n"
    let qd = u16(msg, 4), an = u16(msg, 6)
    var pos = 12
    for _ in 0..<qd { pos = readName(msg, pos).1 + 4 }
    for _ in 0..<an {
        let (owner, p) = readName(msg, pos)
        pos = p
        let t = u16(msg, pos)
        let ttl = (UInt32(msg[pos + 4]) << 24) | (UInt32(msg[pos + 5]) << 16) | (UInt32(msg[pos + 6]) << 8) | UInt32(msg[pos + 7])
        let rdl = u16(msg, pos + 8)
        let rs = pos + 10
        var d = ""
        if t == 1 && rdl == 4 {
            d = (0..<4).map { String(msg[rs + $0]) }.joined(separator: ".")
        } else if t == 28 && rdl == 16 {
            d = aaaa(msg, rs)
        } else if t == 5 {
            d = readName(msg, rs).0
        } else if t == 15 {
            d = "\(u16(msg, rs)) \(readName(msg, rs + 2).0)"
        } else if t == 16 {
            var parts: [String] = []
            var k = 0
            while k < rdl {
                let l = Int(msg[rs + k]); k += 1
                var s = "\""
                var c = 0
                while c < l && k < rdl {
                    let b = msg[rs + k]
                    if b == 0x22 { s += "\\\"" }
                    else if b == 0x5c { s += "\\\\" }
                    else if b >= 0x20 && b <= 0x7e { s.unicodeScalars.append(Unicode.Scalar(b)) }
                    else { s += "\\" + String(format: "%03d", Int(b)) }
                    c += 1; k += 1
                }
                parts.append(s + "\"")
            }
            d = parts.joined(separator: " ")
        } else {
            d = "\\# \(rdl)"
            if rdl > 0 { d += " " + (0..<rdl).map { String(format: "%02x", msg[rs + $0]) }.joined() }
        }
        out += "  \(owner) \(ttl) \(typeName(t)) \(d)\n"
        pos = rs + rdl
    }
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
