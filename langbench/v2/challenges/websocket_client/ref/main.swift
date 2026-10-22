import Foundation
import CommonCrypto

signal(SIGPIPE, SIG_IGN)

var outBuf = Data()
func println(_ s: String) {
    outBuf.append((s + "\n").data(using: .utf8)!)
}
func flushOut() {
    FileHandle.standardOutput.write(outBuf)
    outBuf = Data()
}

func sha1(_ d: [UInt8]) -> [UInt8] {
    var h = [UInt8](repeating: 0, count: Int(CC_SHA1_DIGEST_LENGTH))
    d.withUnsafeBufferPointer { p in
        _ = CC_SHA1(p.baseAddress, CC_LONG(d.count), &h)
    }
    return h
}

func randomBytes(_ n: Int) -> [UInt8] {
    var b = [UInt8](repeating: 0, count: n)
    arc4random_buf(&b, n)
    return b
}

final class Conn {
    let fd: Int32
    var buf = [UInt8](repeating: 0, count: 65536)
    var pos = 0
    var len = 0
    init(fd: Int32) { self.fd = fd }

    func fill() -> Bool {
        if pos < len { return true }
        let n = buf.withUnsafeMutableBytes { read(fd, $0.baseAddress, 65536) }
        if n <= 0 { return false }
        pos = 0
        len = n
        return true
    }
    func byte() -> Int? {
        if !fill() { return nil }
        let b = Int(buf[pos])
        pos += 1
        return b
    }
    func exact(_ n: Int) -> [UInt8]? {
        var out = [UInt8]()
        out.reserveCapacity(n)
        var need = n
        while need > 0 {
            if !fill() { return nil }
            let k = min(need, len - pos)
            out.append(contentsOf: buf[pos..<(pos + k)])
            pos += k
            need -= k
        }
        return out
    }
    func writeAll(_ d: [UInt8]) {
        var off = 0
        while off < d.count {
            let w = d.withUnsafeBytes { write(fd, $0.baseAddress! + off, d.count - off) }
            if w <= 0 { return }
            off += w
        }
    }
    func sendFrame(_ op: UInt8, _ p: [UInt8]) {
        var f = [UInt8]()
        f.reserveCapacity(p.count + 14)
        f.append(0x80 | op)
        let n = p.count
        if n <= 125 {
            f.append(0x80 | UInt8(n))
        } else if n <= 65535 {
            f.append(0x80 | 126)
            f.append(UInt8(n >> 8)); f.append(UInt8(n & 0xff))
        } else {
            f.append(0x80 | 127)
            let l = UInt64(n)
            for i in 0..<8 { f.append(UInt8((l >> UInt64(56 - 8 * i)) & 0xff)) }
        }
        var mk: [UInt8]
        repeat { mk = randomBytes(4) } while mk.allSatisfy { $0 == 0 }
        f.append(contentsOf: mk)
        for i in 0..<n { f.append(p[i] ^ mk[i & 3]) }
        writeAll(f)
    }
}

func session(_ url: String) {
    var rest = Substring(url)
    if rest.hasPrefix("ws://") { rest = rest.dropFirst(5) }
    let hostport: String
    let target: String
    if let i = rest.firstIndex(of: "/") {
        hostport = String(rest[..<i]); target = String(rest[i...])
    } else {
        hostport = String(rest); target = "/"
    }
    var host = hostport
    var port = 80
    if let c = hostport.lastIndex(of: ":") {
        host = String(hostport[..<c])
        port = Int(hostport[hostport.index(after: c)...]) ?? 80
    }
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return }
    defer { close(fd) }
    var sa = sockaddr_in()
    sa.sin_family = sa_family_t(AF_INET)
    sa.sin_port = in_port_t(UInt16(port).bigEndian)
    inet_pton(AF_INET, host, &sa.sin_addr)
    let rc = withUnsafePointer(to: &sa) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    if rc < 0 { return }
    var one: Int32 = 1
    setsockopt(fd, Int32(IPPROTO_TCP), TCP_NODELAY, &one, socklen_t(MemoryLayout<Int32>.size))
    let conn = Conn(fd: fd)
    let key = Data(randomBytes(16)).base64EncodedString()
    let req = "GET \(target) HTTP/1.1\r\nHost: \(hostport)\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: \(key)\r\nSec-WebSocket-Version: 13\r\n\r\n"
    conn.writeAll(Array(req.utf8))

    var head = [UInt8]()
    while true {
        guard let b = conn.byte() else { return }
        head.append(UInt8(b))
        let n = head.count
        if n >= 4 && head[n - 4] == 13 && head[n - 3] == 10 && head[n - 2] == 13 && head[n - 1] == 10 { break }
        if n >= 2 && head[n - 2] == 10 && head[n - 1] == 10 { break }
    }
    let headStr = String(decoding: head, as: UTF8.self)
    let lines = headStr.components(separatedBy: "\n").map { $0.hasSuffix("\r") ? String($0.dropLast()) : $0 }
    let status = lines[0]
    var code = ""
    var reason = ""
    if let s1 = status.firstIndex(of: " ") {
        let after = status[status.index(after: s1)...]
        if let s2 = after.firstIndex(of: " ") {
            code = String(after[..<s2]); reason = String(after[after.index(after: s2)...])
        } else {
            code = String(after)
        }
    }
    if code != "101" {
        println("handshake failed: \(code) \(reason)")
        return
    }
    var accept: String? = nil
    for l in lines.dropFirst() {
        if let c = l.firstIndex(of: ":") {
            if l[..<c].trimmingCharacters(in: .whitespaces).lowercased() == "sec-websocket-accept" {
                accept = l[l.index(after: c)...].trimmingCharacters(in: .whitespaces)
            }
        }
    }
    let expect = Data(sha1(Array((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").utf8))).base64EncodedString()
    if accept != expect {
        println("handshake failed: bad Sec-WebSocket-Accept")
        return
    }

    var closeSent = false
    var pendingSha = false
    var msgOp = -1
    var msg = [UInt8]()
    while true {
        guard let b0 = conn.byte(), let b1 = conn.byte() else { return }
        let fin = (b0 & 0x80) != 0
        let op = b0 & 0x0f
        var n = UInt64(b1 & 0x7f)
        if n == 126 {
            guard let x = conn.exact(2) else { return }
            n = UInt64(x[0]) << 8 | UInt64(x[1])
        } else if n == 127 {
            guard let x = conn.exact(8) else { return }
            n = 0
            for v in x { n = (n << 8) | UInt64(v) }
        }
        var mk: [UInt8]? = nil
        if (b1 & 0x80) != 0 {
            guard let m = conn.exact(4) else { return }
            mk = m
        }
        guard var p = conn.exact(Int(n)) else { return }
        if let m = mk { for i in 0..<p.count { p[i] ^= m[i & 3] } }

        switch op {
        case 9:
            conn.sendFrame(10, p); continue
        case 10:
            continue
        case 8:
            if p.count >= 2 {
                let cc = Int(p[0]) << 8 | Int(p[1])
                let r = String(decoding: p[2...], as: UTF8.self)
                println(r.isEmpty ? "closed \(cc)" : "closed \(cc) \(r)")
            } else {
                println("closed 1005")
            }
            if !closeSent { conn.sendFrame(8, Array(p.prefix(p.count >= 2 ? 2 : 0))) }
            return
        case 1, 2:
            msgOp = op
            msg = p
        case 0:
            msg.append(contentsOf: p)
        default:
            continue
        }
        if !fin { continue }
        let data = msg
        msg = []
        if msgOp == 1 {
            let cmd = String(decoding: data, as: UTF8.self)
            if cmd.hasPrefix("ADD ") {
                let parts = cmd.split(separator: " ")
                let a = Int64(parts.count > 1 ? parts[1] : "0") ?? 0
                let b = Int64(parts.count > 2 ? parts[2] : "0") ?? 0
                let rep = String(a + b)
                conn.sendFrame(1, Array(rep.utf8))
                println("\(cmd) -> \(rep)")
            } else if cmd.hasPrefix("REV") {
                var t = ""
                if let sp = cmd.firstIndex(of: " ") { t = String(cmd[cmd.index(after: sp)...]) }
                var sc = String.UnicodeScalarView()
                sc.append(contentsOf: t.unicodeScalars.reversed())
                let rep = String(sc)
                conn.sendFrame(1, Array(rep.utf8))
                println("\(cmd) -> \(rep)")
            } else if cmd == "SHA1" {
                pendingSha = true
            } else if cmd == "BYE" {
                conn.sendFrame(8, [0x03, 0xE8])
                closeSent = true
            }
        } else if msgOp == 2 && pendingSha {
            pendingSha = false
            let hx = sha1(data).map { String(format: "%02x", $0) }.joined()
            conn.sendFrame(1, Array(hx.utf8))
            println("SHA1 \(data.count) -> \(hx)")
        }
    }
}

let args = CommandLine.arguments.dropFirst()
var idx = 0
for url in args {
    idx += 1
    println("session \(idx)")
    session(url)
}
flushOut()
exit(0)
