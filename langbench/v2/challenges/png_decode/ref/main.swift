import Foundation

var CRCT = [UInt32](repeating: 0, count: 256)
for n in 0..<256 {
    var c = UInt32(n)
    for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1 }
    CRCT[n] = c
}
func crcUpdate(_ c0: UInt32, _ b: [UInt8], _ off: Int, _ len: Int) -> UInt32 {
    var c = c0
    var i = off
    let end = off + len
    while i < end { c = CRCT[Int((c ^ UInt32(b[i])) & 0xff)] ^ (c >> 8); i += 1 }
    return c
}
func be32(_ b: [UInt8], _ p: Int) -> UInt32 {
    return (UInt32(b[p]) << 24) | (UInt32(b[p + 1]) << 16) | (UInt32(b[p + 2]) << 8) | UInt32(b[p + 3])
}

let LBASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258]
let LEXT = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0]
let DBASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577]
let DEXT = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13]

struct Huff {
    var count = [Int](repeating: 0, count: 16)
    var sym: [Int]
    init(_ len: [Int]) {
        for l in len { count[l] += 1 }
        count[0] = 0
        var offs = [Int](repeating: 0, count: 16)
        for i in 1..<15 { offs[i + 1] = offs[i] + count[i] }
        sym = [Int](repeating: 0, count: max(len.count, 1))
        for (i, l) in len.enumerated() where l != 0 {
            sym[offs[l]] = i
            offs[l] += 1
        }
    }
}

final class Inf {
    let inp: [UInt8]
    var pos = 2, bitbuf = 0, bitcnt = 0
    var out: [UInt8] = []
    var nstored = 0, nfixed = 0, ndynamic = 0
    init(_ i: [UInt8]) { inp = i; out.reserveCapacity(65536) }
    @inline(__always) func bit() -> Int {
        if bitcnt == 0 {
            bitbuf = pos < inp.count ? Int(inp[pos]) : 0
            pos += 1; bitcnt = 8
        }
        let b = bitbuf & 1; bitbuf >>= 1; bitcnt -= 1
        return b
    }
    func bits(_ n: Int) -> Int { var v = 0; for i in 0..<n { v |= bit() << i }; return v }
    func decode(_ h: Huff) -> Int {
        var code = 0, first = 0, index = 0
        for len in 1..<16 {
            code |= bit()
            let count = h.count[len]
            if code - count < first { return h.sym[index + (code - first)] }
            index += count; first += count; first <<= 1; code <<= 1
        }
        return -1
    }
    func codes(_ lc: Huff, _ dc: Huff) -> Bool {
        while true {
            var sym = decode(lc)
            if sym < 0 { return false }
            if sym < 256 { out.append(UInt8(sym)) }
            else if sym == 256 { return true }
            else {
                sym -= 257
                if sym >= 29 { return false }
                let len = LBASE[sym] + bits(LEXT[sym])
                let d = decode(dc)
                if d < 0 || d >= 30 { return false }
                let dist = DBASE[d] + bits(DEXT[d])
                if dist > out.count { return false }
                for _ in 0..<len { out.append(out[out.count - dist]) }
            }
        }
    }
    func inflate() -> Bool {
        var last = 0
        repeat {
            last = bit()
            let t = bits(2)
            if t == 0 {
                nstored += 1
                bitcnt = 0
                if pos + 4 > inp.count { return false }
                let len = Int(inp[pos]) | (Int(inp[pos + 1]) << 8)
                pos += 4
                if pos + len > inp.count { return false }
                out.append(contentsOf: inp[pos..<pos + len])
                pos += len
            } else if t == 1 {
                nfixed += 1
                var l = [Int]()
                for i in 0..<288 { l.append(i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8) }
                if !codes(Huff(l), Huff([Int](repeating: 5, count: 30))) { return false }
            } else if t == 2 {
                ndynamic += 1
                let nlen = bits(5) + 257, ndist = bits(5) + 1, ncode = bits(4) + 4
                let ord = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15]
                var cl = [Int](repeating: 0, count: 19)
                for i in 0..<ncode { cl[ord[i]] = bits(3) }
                let ch = Huff(cl)
                var lens = [Int](repeating: 0, count: nlen + ndist)
                var idx = 0
                while idx < nlen + ndist {
                    let sym = decode(ch)
                    if sym < 0 { return false }
                    if sym < 16 { lens[idx] = sym; idx += 1 }
                    else {
                        var rep: Int, val = 0
                        if sym == 16 { if idx == 0 { return false }; val = lens[idx - 1]; rep = 3 + bits(2) }
                        else if sym == 17 { rep = 3 + bits(3) }
                        else { rep = 11 + bits(7) }
                        if idx + rep > nlen + ndist { return false }
                        while rep > 0 { lens[idx] = val; idx += 1; rep -= 1 }
                    }
                }
                if !codes(Huff(Array(lens[0..<nlen])), Huff(Array(lens[nlen...]))) { return false }
            } else { return false }
        } while last == 0
        return true
    }
}

func paeth(_ a: Int, _ b: Int, _ c: Int) -> Int {
    let p = a + b - c, pa = abs(p - a), pb = abs(p - b), pc = abs(p - c)
    if pa <= pb && pa <= pc { return a }
    if pb <= pc { return b }
    return c
}

func typeAt(_ buf: [UInt8], _ p: Int) -> String {
    return String(decoding: buf[p + 4..<p + 8], as: UTF8.self)
}

func processFile(_ dir: String, _ name: String, _ o: inout String) {
    o += "== \(name)\n"
    guard let data = FileManager.default.contents(atPath: dir + "/" + name) else { return }
    let buf = [UInt8](data)
    let sz = buf.count
    var p = 8, n = 0
    while p + 12 <= sz {
        let len = Int(be32(buf, p))
        n += 1
        let c = crcUpdate(0xffffffff, buf, p + 4, len + 4) ^ 0xffffffff
        if c != be32(buf, p + 8 + len) {
            o += "error: CRC mismatch in chunk \(n) \(typeAt(buf, p))\n"
            return
        }
        p += 12 + len
    }
    var w = 0, h = 0, depth = 0, ctype = 0
    var pal = [UInt8](), trns = [UInt8](), hastrns = false
    var idat = [UInt8]()
    var types = [String](), runs = [Int]()
    p = 8
    while p + 12 <= sz {
        let len = Int(be32(buf, p))
        let ty = typeAt(buf, p)
        let d = p + 8
        if let lt = types.last, lt == ty { runs[runs.count - 1] += 1 } else { types.append(ty); runs.append(1) }
        switch ty {
        case "IHDR":
            w = Int(be32(buf, d)); h = Int(be32(buf, d + 4)); depth = Int(buf[d + 8]); ctype = Int(buf[d + 9])
        case "PLTE": pal = Array(buf[d..<d + len])
        case "tRNS": hastrns = true; trns = Array(buf[d..<d + len])
        case "IDAT": idat.append(contentsOf: buf[d..<d + len])
        default: break
        }
        p += 12 + len
    }
    var cl = [String]()
    for i in 0..<types.count { cl.append(runs[i] > 1 ? "\(types[i])*\(runs[i])" : types[i]) }
    let s = Inf(idat)
    _ = s.inflate()
    var a1: UInt32 = 1, a2: UInt32 = 0
    let out = s.out
    for b in out { a1 = (a1 + UInt32(b)) % 65521; a2 = (a2 + a1) % 65521 }
    let adl = (a2 << 16) | a1
    let want: UInt32 = s.pos + 4 <= idat.count ? be32(idat, s.pos) : ~adl
    if adl != want { o += "error: Adler-32 mismatch\n"; return }

    let chans = (ctype == 0 || ctype == 3) ? 1 : ctype == 2 ? 3 : ctype == 4 ? 2 : 4
    let bppBits = chans * depth
    let stride = (w * bppBits + 7) / 8
    let bpp = max(1, (bppBits + 7) / 8)
    var prow = [UInt8](repeating: 0, count: stride), cur = [UInt8](repeating: 0, count: stride)
    var fc = [Int](repeating: 0, count: 5)
    var sum = [UInt64](repeating: 0, count: 4)
    var crc: UInt32 = 0xffffffff
    var rgba = [UInt8](repeating: 0, count: w * 4)
    var ip = 0
    let maxv = depth >= 16 ? 0xffff : (1 << depth) - 1
    var tv = [0, 0, 0]
    var ti = 0
    while ti < 3 && 2 * ti + 1 < trns.count { tv[ti] = (Int(trns[2 * ti]) << 8) | Int(trns[2 * ti + 1]); ti += 1 }
    func cv(_ v: Int) -> Int { depth == 16 ? v >> 8 : depth == 8 ? v : v * 255 / maxv }
    var v = [0, 0, 0, 0]
    let outlen = out.count
    for _ in 0..<h {
        let ft = ip < outlen ? Int(out[ip]) : 0
        ip += 1
        if ft < 5 { fc[ft] += 1 }
        for i in 0..<stride { cur[i] = ip + i < outlen ? out[ip + i] : 0 }
        ip += stride
        for i in 0..<stride {
            let a = i >= bpp ? Int(cur[i - bpp]) : 0
            let b = Int(prow[i])
            let c = i >= bpp ? Int(prow[i - bpp]) : 0
            var x = Int(cur[i])
            switch ft {
            case 1: x += a
            case 2: x += b
            case 3: x += (a + b) / 2
            case 4: x += paeth(a, b, c)
            default: break
            }
            cur[i] = UInt8(x & 0xff)
        }
        for x in 0..<w {
            for ch in 0..<chans {
                if depth == 16 {
                    let o2 = (x * chans + ch) * 2
                    v[ch] = (Int(cur[o2]) << 8) | Int(cur[o2 + 1])
                } else if depth == 8 {
                    v[ch] = Int(cur[x * chans + ch])
                } else {
                    let bit = (x * chans + ch) * depth
                    v[ch] = (Int(cur[bit / 8]) >> (8 - depth - bit % 8)) & maxv
                }
            }
            var r = 0, g = 0, b = 0, a = 255
            if ctype == 3 {
                let i = v[0]
                r = i * 3 < pal.count ? Int(pal[i * 3]) : 0
                g = i * 3 + 1 < pal.count ? Int(pal[i * 3 + 1]) : 0
                b = i * 3 + 2 < pal.count ? Int(pal[i * 3 + 2]) : 0
                a = i < trns.count ? Int(trns[i]) : 255
            } else if ctype == 0 {
                r = cv(v[0]); g = r; b = r
                if hastrns && trns.count >= 2 && v[0] == tv[0] { a = 0 }
            } else if ctype == 2 {
                r = cv(v[0]); g = cv(v[1]); b = cv(v[2])
                if hastrns && trns.count >= 6 && v[0] == tv[0] && v[1] == tv[1] && v[2] == tv[2] { a = 0 }
            } else if ctype == 4 {
                r = cv(v[0]); g = r; b = r; a = cv(v[1])
            } else {
                r = cv(v[0]); g = cv(v[1]); b = cv(v[2]); a = cv(v[3])
            }
            rgba[x * 4] = UInt8(r); rgba[x * 4 + 1] = UInt8(g); rgba[x * 4 + 2] = UInt8(b); rgba[x * 4 + 3] = UInt8(a)
            sum[0] += UInt64(r); sum[1] += UInt64(g); sum[2] += UInt64(b); sum[3] += UInt64(a)
        }
        crc = crcUpdate(crc, rgba, 0, rgba.count)
        swap(&prow, &cur)
    }
    let hex = String(crc ^ 0xffffffff, radix: 16)
    let crcs = String(repeating: "0", count: 8 - hex.count) + hex
    o += "IHDR \(w)x\(h) depth=\(depth) color=\(ctype)\n"
    o += "chunks \(cl.joined(separator: " "))\n"
    o += "deflate stored=\(s.nstored) fixed=\(s.nfixed) dynamic=\(s.ndynamic)\n"
    o += "filters none=\(fc[0]) sub=\(fc[1]) up=\(fc[2]) average=\(fc[3]) paeth=\(fc[4])\n"
    o += "rgba sum=\(sum[0]),\(sum[1]),\(sum[2]),\(sum[3]) crc32=\(crcs)\n"
}

let args = CommandLine.arguments
if args.count >= 2 {
    let dir = args[1]
    var names = ((try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []).filter { $0.hasSuffix(".png") }
    names.sort { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
    var o = ""
    for n in names { processFile(dir, n, &o) }
    FileHandle.standardOutput.write(o.data(using: .utf8)!)
}
