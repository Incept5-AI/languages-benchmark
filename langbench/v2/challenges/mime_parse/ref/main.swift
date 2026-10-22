import Foundation

typealias Bytes = [UInt8]

func isWs(_ c: UInt8) -> Bool { return c == 0x20 || c == 0x09 }

func trimWs(_ s: Bytes) -> Bytes {
    var a = 0, b = s.count
    while a < b && isWs(s[a]) { a += 1 }
    while b > a && isWs(s[b - 1]) { b -= 1 }
    return Array(s[a..<b])
}

func lower(_ s: Bytes) -> Bytes {
    return s.map { ($0 >= 65 && $0 <= 90) ? $0 + 32 : $0 }
}

func str(_ b: Bytes) -> String { return String(decoding: b, as: UTF8.self) }
func bytes(_ s: String) -> Bytes { return Array(s.utf8) }

func find(_ h: Bytes, _ end: Int, _ n: Bytes, _ from: Int) -> Int {
    var i = from
    while i + n.count <= end {
        var ok = true
        for k in 0..<n.count where h[i + k] != n[k] { ok = false; break }
        if ok { return i }
        i += 1
    }
    return -1
}

func hexv(_ c: UInt8) -> Int {
    switch c {
    case 48...57: return Int(c) - 48
    case 97...102: return Int(c) - 87
    case 65...70: return Int(c) - 55
    default: return -1
    }
}

func b64val(_ c: UInt8) -> Int {
    switch c {
    case 65...90: return Int(c) - 65
    case 97...122: return Int(c) - 97 + 26
    case 48...57: return Int(c) - 48 + 52
    case 43: return 62
    case 47: return 63
    default: return -1
    }
}

func b64decode(_ d: ArraySlice<UInt8>) -> Bytes {
    var out = Bytes()
    var acc = 0, n = 0
    for c in d {
        if c == 61 { break }
        let v = b64val(c)
        if v < 0 { continue }
        acc = ((acc << 6) | v) & 0xffffff
        n += 1
        if n == 4 {
            out.append(UInt8((acc >> 16) & 255)); out.append(UInt8((acc >> 8) & 255)); out.append(UInt8(acc & 255))
            acc = 0; n = 0
        }
    }
    if n == 2 { out.append(UInt8((acc >> 4) & 255)) }
    else if n == 3 { out.append(UInt8((acc >> 10) & 255)); out.append(UInt8((acc >> 2) & 255)) }
    return out
}

func qpdecode(_ d: Bytes, _ nl: Bytes) -> Bytes {
    var j = Bytes()
    var pos = 0
    while true {
        let i = find(d, d.count, nl, pos)
        let has = i >= 0
        let le = has ? i : d.count
        var e = le
        while e > pos && isWs(d[e - 1]) { e -= 1 }
        if e > pos && d[e - 1] == 61 {
            j.append(contentsOf: d[pos..<(e - 1)])
        } else {
            j.append(contentsOf: d[pos..<e])
            if has { j.append(contentsOf: nl) }
        }
        if !has { break }
        pos = i + nl.count
    }
    var out = Bytes()
    var i = 0
    while i < j.count {
        let c = j[i]
        if c == 61 && i + 2 < j.count {
            let a = hexv(j[i + 1]), b = hexv(j[i + 2])
            if a >= 0 && b >= 0 { out.append(UInt8(a * 16 + b)); i += 3; continue }
        }
        out.append(c)
        i += 1
    }
    return out
}

let CP1252: [UInt32] = [
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
    0x0152, 0x008D, 0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178]

// returns array of unicode scalars
func decodeCharset(_ b: Bytes, _ cs0: String) -> [Unicode.Scalar] {
    let cs = cs0.lowercased()
    if cs == "iso-8859-1" || cs == "latin1" || cs == "iso8859-1" {
        return b.map { Unicode.Scalar($0) }
    }
    if cs == "windows-1252" || cs == "cp1252" {
        return b.map { $0 >= 0x80 && $0 < 0xa0 ? Unicode.Scalar(CP1252[Int($0) - 0x80])! : Unicode.Scalar($0) }
    }
    return Array(String(decoding: b, as: UTF8.self).unicodeScalars)
}

func stripLang(_ cs: String) -> String {
    if let i = cs.firstIndex(of: "*") { return String(cs[..<i]) }
    return cs
}

struct Seg { var word: Bool; var text: Bytes; var cs: String; var bytes: Bytes }

func decode2047(_ b: Bytes) -> [Unicode.Scalar] {
    var segs: [Seg] = []
    var textStart = 0, i = 0
    while i + 1 < b.count {
        if b[i] == 61 && b[i + 1] == 63 {
            var j = i + 2
            while j < b.count && b[j] != 63 && !isWs(b[j]) { j += 1 }
            if j > i + 2 && j < b.count && b[j] == 63 {
                let cs = str(Array(b[(i + 2)..<j]))
                if j + 2 < b.count && [66, 98, 81, 113].contains(b[j + 1]) && b[j + 2] == 63 {
                    let enc = b[j + 1]
                    let ts = j + 3
                    var k = ts
                    while k < b.count && b[k] != 63 && !isWs(b[k]) { k += 1 }
                    if k + 1 < b.count && b[k] == 63 && b[k + 1] == 61 {
                        var by = Bytes()
                        if enc == 66 || enc == 98 { by = b64decode(b[ts..<k]) } else {
                            var p = ts
                            while p < k {
                                let c = b[p]
                                if c == 95 { by.append(0x20) }
                                else if c == 61 && p + 2 < k && hexv(b[p + 1]) >= 0 && hexv(b[p + 2]) >= 0 {
                                    by.append(UInt8(hexv(b[p + 1]) * 16 + hexv(b[p + 2])))
                                    p += 3
                                    continue
                                } else { by.append(c) }
                                p += 1
                            }
                        }
                        if textStart < i { segs.append(Seg(word: false, text: Array(b[textStart..<i]), cs: "", bytes: [])) }
                        segs.append(Seg(word: true, text: [], cs: stripLang(cs).lowercased(), bytes: by))
                        i = k + 2
                        textStart = i
                        continue
                    }
                }
            }
        }
        i += 1
    }
    if textStart < b.count { segs.append(Seg(word: false, text: Array(b[textStart...]), cs: "", bytes: [])) }
    var cleaned: [Seg] = []
    var prevWord = false
    for idx in 0..<segs.count {
        let sg = segs[idx]
        let drop = !sg.word && prevWord && idx + 1 < segs.count && segs[idx + 1].word && sg.text.allSatisfy(isWs)
        if !drop { prevWord = sg.word; cleaned.append(sg) }
    }
    var out: [Unicode.Scalar] = []
    var k = 0
    while k < cleaned.count {
        let sg = cleaned[k]
        if !sg.word { out.append(contentsOf: sg.text.map { Unicode.Scalar($0) }); k += 1; continue }
        var by = Bytes()
        while k < cleaned.count && cleaned[k].word && cleaned[k].cs == sg.cs {
            by.append(contentsOf: cleaned[k].bytes)
            k += 1
        }
        out.append(contentsOf: decodeCharset(by, sg.cs))
    }
    return out
}

struct Ctype { var main: String; var params: [(String, Bytes)] }

func parseCtype(_ v: Bytes, _ hasSub: Bool) -> Ctype {
    var i = 0
    let n = v.count
    func skip() { while i < n && isWs(v[i]) { i += 1 } }
    skip()
    let st = i
    while i < n && v[i] != 47 && v[i] != 59 && !isWs(v[i]) { i += 1 }
    var main = str(lower(Array(v[st..<i])))
    if hasSub {
        skip()
        if i < n && v[i] == 47 {
            i += 1
            skip()
            let s2 = i
            while i < n && v[i] != 59 && !isWs(v[i]) { i += 1 }
            main = main + "/" + str(lower(Array(v[s2..<i])))
        }
    }
    var params: [(String, Bytes)] = []
    while true {
        skip()
        if i >= n { break }
        if v[i] != 59 { i += 1; continue }
        i += 1
        skip()
        let a = i
        while i < n && v[i] != 61 && v[i] != 59 && !isWs(v[i]) { i += 1 }
        let attr = str(lower(Array(v[a..<i])))
        skip()
        if i < n && v[i] == 61 {
            i += 1
            skip()
            var val = Bytes()
            if i < n && v[i] == 34 {
                i += 1
                while i < n && v[i] != 34 {
                    if v[i] == 92 && i + 1 < n { i += 1 }
                    val.append(v[i])
                    i += 1
                }
                if i < n { i += 1 }
            } else {
                while i < n && v[i] != 59 && !isWs(v[i]) { val.append(v[i]); i += 1 }
            }
            if !attr.isEmpty { params.append((attr, val)) }
        }
    }
    return Ctype(main: main, params: params)
}

func getParam(_ c: Ctype, _ n: String) -> Bytes? {
    for p in c.params where p.0 == n { return p.1 }
    return nil
}

func pctDecode(_ b: Bytes) -> Bytes {
    var o = Bytes()
    var i = 0
    while i < b.count {
        if b[i] == 37 && i + 2 < b.count && hexv(b[i + 1]) >= 0 && hexv(b[i + 2]) >= 0 {
            o.append(UInt8(hexv(b[i + 1]) * 16 + hexv(b[i + 2])))
            i += 3
        } else { o.append(b[i]); i += 1 }
    }
    return o
}

func splitCs(_ v: Bytes) -> (String, Bytes) {
    if let p1 = v.firstIndex(of: 39) {
        if let p2 = v[(p1 + 1)...].firstIndex(of: 39) {
            return (str(Array(v[..<p1])), Array(v[(p2 + 1)...]))
        }
    }
    return ("", v)
}

func rfc2231(_ c: Ctype, _ a: String) -> [Unicode.Scalar]? {
    if let single = getParam(c, a + "*") {
        let (cs, rest) = splitCs(single)
        return decodeCharset(pctDecode(rest), cs.isEmpty ? "us-ascii" : stripLang(cs))
    }
    let pre = a + "*"
    var segs: [(Int, Bool, Bytes)] = []
    for (n, v) in c.params {
        if !n.hasPrefix(pre) { continue }
        var rest = String(n.dropFirst(pre.count))
        var enc = false
        if rest.hasSuffix("*") { enc = true; rest = String(rest.dropLast()) }
        if !rest.isEmpty && rest.allSatisfy({ $0 >= "0" && $0 <= "9" }), let k = Int(rest) {
            segs.append((k, enc, v))
        }
    }
    if segs.isEmpty { return nil }
    segs.sort { $0.0 < $1.0 }
    var cs = ""
    var by = Bytes()
    var firstEnc = true
    for (_, enc, v) in segs {
        if enc {
            if firstEnc {
                firstEnc = false
                let (c2, rest) = splitCs(v)
                cs = c2
                by.append(contentsOf: pctDecode(rest))
            } else { by.append(contentsOf: pctDecode(v)) }
        } else { by.append(contentsOf: v) }
    }
    return decodeCharset(by, cs.isEmpty ? "us-ascii" : stripLang(cs))
}

var crcTable: [UInt32] = {
    var t = [UInt32](repeating: 0, count: 256)
    for i in 0..<256 {
        var c = UInt32(i)
        for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1 }
        t[i] = c
    }
    return t
}()

func crc32(_ d: Bytes) -> UInt32 {
    var c: UInt32 = 0xFFFFFFFF
    for b in d { c = crcTable[Int((c ^ UInt32(b)) & 0xff)] ^ (c >> 8) }
    return c ^ 0xFFFFFFFF
}

func hex(_ v: UInt32, _ w: Int) -> String {
    let s = String(v, radix: 16)
    return String(repeating: "0", count: max(0, w - s.count)) + s
}

func quote(_ s: [Unicode.Scalar]) -> String {
    var o = "\""
    for ch in s {
        let cp = ch.value
        if cp == 92 { o += "\\\\" }
        else if cp == 34 { o += "\\\"" }
        else if cp == 10 { o += "\\n" }
        else if cp == 9 { o += "\\t" }
        else if cp < 0x20 || (cp >= 0x7f && cp <= 0x9f) { o += "\\u" + hex(cp, 4) }
        else { o.unicodeScalars.append(ch) }
    }
    return o + "\""
}

struct Entity { var headers: [String: Bytes]; var body: Bytes }

func parseEntity(_ d: Bytes, _ nl: Bytes) -> Entity {
    var lines: [Bytes] = []
    var pos = 0
    var body = Bytes()
    while pos < d.count {
        let i = find(d, d.count, nl, pos)
        let le = i >= 0 ? i : d.count
        let next = i >= 0 ? i + nl.count : d.count
        if le == pos { body = Array(d[min(next, d.count)...]); break }
        let line = Array(d[pos..<le])
        if (line[0] == 32 || line[0] == 9) && !lines.isEmpty { lines[lines.count - 1].append(contentsOf: line) }
        else { lines.append(line) }
        pos = next
    }
    var headers: [String: Bytes] = [:]
    for l in lines {
        guard let c = l.firstIndex(of: 58) else { continue }
        let name = str(lower(trimWs(Array(l[..<c]))))
        if headers[name] == nil { headers[name] = trimWs(Array(l[(c + 1)...])) }
    }
    return Entity(headers: headers, body: body)
}

func splitMultipart(_ body: Bytes, _ bnd: Bytes, _ nl: Bytes) -> [Bytes] {
    let db: Bytes = [45, 45] + bnd
    var parts: [Bytes] = []
    var pos = 0, cur = -1
    var closed = false
    func allWs(_ s: Int, _ e: Int) -> Bool {
        var i = s
        while i < e { if !isWs(body[i]) { return false }; i += 1 }
        return true
    }
    while true {
        let i = find(body, body.count, nl, pos)
        let has = i >= 0
        let le = has ? i : body.count
        let next = has ? i + nl.count : body.count
        var kind = 0
        if le - pos >= db.count && Array(body[pos..<(pos + db.count)]) == db {
            let r = pos + db.count
            if allWs(r, le) { kind = 1 }
            else if le - r >= 2 && body[r] == 45 && body[r + 1] == 45 && allWs(r + 2, le) { kind = 2 }
        }
        if kind != 0 {
            if cur >= 0 { parts.append(Array(body[cur..<max(cur, pos - nl.count)])) }
            if kind == 2 { closed = true; break }
            cur = next
        }
        if !has { break }
        pos = next
    }
    if !closed && cur >= 0 && cur <= body.count { parts.append(Array(body[cur...])) }
    return parts
}

func render(_ d: Bytes, _ nl: Bytes, _ depth: Int, _ out: inout String) {
    let e = parseEntity(d, nl)
    let ind = String(repeating: "  ", count: depth)
    var ct = Ctype(main: "text/plain", params: [])
    if let v = e.headers["content-type"] { ct = parseCtype(v, true) }
    let cte = str(lower(e.headers["content-transfer-encoding"] ?? bytes("7bit")))
    var cd: Ctype? = nil
    if let v = e.headers["content-disposition"] { cd = parseCtype(v, false) }
    let ty = ct.main.split(separator: "/", omittingEmptySubsequences: false).first.map(String.init) ?? ""
    if ty == "multipart" {
        let parts = splitMultipart(e.body, getParam(ct, "boundary") ?? [], nl)
        out += "\(ind)\(ct.main) parts=\(parts.count)\n"
        for p in parts { render(p, nl, depth + 1, &out) }
        return
    }
    let raw: Bytes
    if cte == "base64" { raw = b64decode(e.body[...]) }
    else if cte == "quoted-printable" { raw = qpdecode(e.body, nl) }
    else { raw = e.body }
    let disp = cd?.main
    if ty == "text" && disp != "attachment" {
        let cs = str(lower(getParam(ct, "charset") ?? bytes("us-ascii")))
        let sc = decodeCharset(raw, cs)
        var txt: [Unicode.Scalar] = []
        var i = 0
        while i < sc.count {
            if sc[i].value == 13 {
                txt.append("\n")
                if i + 1 < sc.count && sc[i + 1].value == 10 { i += 1 }
            } else { txt.append(sc[i]) }
            i += 1
        }
        out += "\(ind)\(ct.main) charset=\(cs) cte=\(cte) chars=\(txt.count)\n"
        out += "\(ind)  \(quote(txt))\n"
    } else {
        var name: [Unicode.Scalar]? = nil
        if let c = cd {
            name = rfc2231(c, "filename")
            if name == nil, let f = getParam(c, "filename") { name = decode2047(f) }
        }
        if name == nil { name = rfc2231(ct, "name") }
        if name == nil, let f = getParam(ct, "name") { name = decode2047(f) }
        out += "\(ind)\(ct.main) cte=\(cte) disposition=\(disp ?? "-") name=\(name.map(quote) ?? "-") size=\(raw.count) crc32=\(hex(crc32(raw), 8))\n"
    }
}

let dir = CommandLine.arguments[1]
var names = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
names = names.filter { $0.hasSuffix(".eml") }
names.sort { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
var out = ""
for (idx, n) in names.enumerated() {
    if idx > 0 { out += "\n" }
    let d = Bytes(FileManager.default.contents(atPath: dir + "/" + n) ?? Data())
    var nl: Bytes = [10]
    if let p = d.firstIndex(of: 10), p > 0, d[p - 1] == 13 { nl = [13, 10] }
    let e = parseEntity(d, nl)
    out += "== \(n)\n"
    for (h, label) in [("from", "From"), ("subject", "Subject")] {
        if let v = e.headers[h] { out += "\(label): \(quote(decode2047(v)))\n" }
        else { out += "\(label): -\n" }
    }
    render(d, nl, 0, &out)
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
