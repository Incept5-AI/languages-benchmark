import Foundation

let LOWF: [Double] = [697, 770, 852, 941]
let HIGHF: [Double] = [1209, 1336, 1477, 1633]
let KEYS: [[Character]] = [Array("123A"), Array("456B"), Array("789C"), Array("*0#D")]

func u16(_ b: [UInt8], _ p: Int) -> Int { Int(b[p]) | (Int(b[p + 1]) << 8) }
func u32(_ b: [UInt8], _ p: Int) -> Int { u16(b, p) | (u16(b, p + 2) << 16) }

func processFile(_ name: String, _ buf: [UInt8]) -> String {
    let len = buf.count
    var fmtR: Range<Int>? = nil
    var dataR: Range<Int>? = nil
    var pos = 12
    while pos + 8 <= len {
        let n = u32(buf, pos + 4)
        let start = pos + 8
        let end = min(start + n, len)
        let id = Array(buf[pos..<pos + 4])
        if id == Array("fmt ".utf8) { fmtR = start..<end }
        else if id == Array("data".utf8) { dataR = start..<end }
        pos = start + n + (n & 1)
    }
    guard let fr = fmtR, fr.count >= 16 else { return "\(name): unsupported\n" }
    let fo = fr.lowerBound
    let tag = u16(buf, fo), ch = u16(buf, fo + 2), rate = u32(buf, fo + 4), bits = u16(buf, fo + 14)
    var eff = tag
    if tag == 0xFFFE { eff = fr.count >= 26 ? u16(buf, fo + 24) : 0 }
    if eff != 1 || bits != 16 { return "\(name): unsupported\n" }
    let dr = dataR ?? 0..<0
    let frames = dr.count / (2 * ch)
    let N = rate / 50
    let nb = frames / N
    var out = "\(name): \(rate) Hz, \(ch) ch, \(nb) blocks\n"
    var lab = [Character?](repeating: nil, count: nb)
    var x = [Double](repeating: 0, count: N)
    for b in 0..<nb {
        var E = 0.0
        for i in 0..<N {
            let off = dr.lowerBound + (b * N + i) * 2 * ch
            let s = Double(Int16(bitPattern: UInt16(buf[off]) | (UInt16(buf[off + 1]) << 8)))
            x[i] = s
            E += s * s
        }
        if E < 1000000.0 * Double(N) { continue }
        var r = [Double](repeating: 0, count: 8)
        for k in 0..<8 {
            let fq = k < 4 ? LOWF[k] : HIGHF[k - 4]
            let c = 2 * cos(2 * Double.pi * fq / Double(rate))
            var s1 = 0.0, s2 = 0.0
            for i in 0..<N { let s = x[i] + c * s1 - s2; s2 = s1; s1 = s }
            let P = s1 * s1 + s2 * s2 - c * s1 * s2
            r[k] = 2 * P / (Double(N) * E)
        }
        var bl = 0, bh = 0
        for k in 1..<4 { if r[k] > r[bl] { bl = k }; if r[4 + k] > r[4 + bh] { bh = k } }
        if r[bl] >= 0.1 && r[4 + bh] >= 0.1 && r[bl] + r[4 + bh] >= 0.6 { lab[b] = KEYS[bl][bh] }
    }
    var br = lab
    if nb >= 3 {
        for b in 1..<(nb - 1) {
            if lab[b] == nil, let l = lab[b - 1], lab[b + 1] == l { br[b] = l }
        }
    }
    var digits = ""
    var i = 0
    while i < nb {
        var j = i
        while j + 1 < nb && br[j + 1] == br[i] { j += 1 }
        if let d = br[i], j - i + 1 >= 2 {
            out += "\(d) \(i * 20) \((j + 1) * 20)\n"
            digits.append(d)
        }
        i = j + 1
    }
    out += "digits: \(digits)\n"
    return out
}

let args = CommandLine.arguments
if args.count >= 2 {
    let dir = args[1]
    let all = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
    let names = all.filter { $0.hasSuffix(".wav") }.sorted { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
    var parts: [String] = []
    for nm in names {
        let data = FileManager.default.contents(atPath: dir + "/" + nm) ?? Data()
        parts.append(processFile(nm, [UInt8](data)))
    }
    FileHandle.standardOutput.write(parts.joined(separator: "\n").data(using: .utf8)!)
}
