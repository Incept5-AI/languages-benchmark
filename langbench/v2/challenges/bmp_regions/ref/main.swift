import Foundation

func u32(_ d: [UInt8], _ o: Int) -> UInt32 {
    return UInt32(d[o]) | (UInt32(d[o + 1]) << 8) | (UInt32(d[o + 2]) << 16) | (UInt32(d[o + 3]) << 24)
}
func u16(_ d: [UInt8], _ o: Int) -> Int {
    return Int(d[o]) | (Int(d[o + 1]) << 8)
}
func isInk(_ r: Int, _ g: Int, _ b: Int) -> Bool {
    return (299 * r + 587 * g + 114 * b) / 1000 < 128
}
func chan(_ px: UInt32, _ mask: UInt32) -> Int {
    if mask == 0 { return 0 }
    let s = mask.trailingZeroBitCount
    let n = mask.nonzeroBitCount
    let v = UInt64((px & mask) >> UInt32(s))
    let m: UInt64 = (UInt64(1) << UInt64(n)) - 1
    return Int((v * 255 + m / 2) / m)
}

func processFile(_ dir: String, _ name: String, _ out: inout String) {
    out += "== \(name)\n"
    guard let data = FileManager.default.contents(atPath: dir + "/" + name) else { return }
    let d = [UInt8](data)
    let off = Int(u32(d, 10))
    let hsz = Int(u32(d, 14))
    let w = Int(Int32(bitPattern: u32(d, 18)))
    let hraw = Int(Int32(bitPattern: u32(d, 22)))
    let bpp = u16(d, 28)
    let comp = u32(d, 30)
    let clrUsed = Int(u32(d, 46))
    if comp != 0 && comp != 3 {
        out += "error: unsupported compression \(comp)\n"
        return
    }
    let bottomUp = hraw > 0
    let h = abs(hraw)
    var rm: UInt32 = 0, gm: UInt32 = 0, bm: UInt32 = 0
    if comp == 3 {
        let p = hsz >= 108 ? 54 : 14 + hsz
        rm = u32(d, p); gm = u32(d, p + 4); bm = u32(d, p + 8)
    }
    var palInk: [Bool] = []
    if bpp <= 8 {
        let n = clrUsed != 0 ? clrUsed : (1 << bpp)
        let pal = 14 + hsz
        for i in 0..<n {
            let e = pal + 4 * i
            palInk.append(isInk(Int(d[e + 2]), Int(d[e + 1]), Int(d[e])))
        }
    }
    let stride = (w * bpp + 31) / 32 * 4
    var ink = [UInt8](repeating: 0, count: w * h)
    var inkCount = 0
    for y in 0..<h {
        let srow = bottomUp ? h - 1 - y : y
        let row = off + srow * stride
        for x in 0..<w {
            var isI: Bool
            if bpp <= 8 {
                var idx: Int
                if bpp == 8 { idx = Int(d[row + x]) }
                else if bpp == 4 { idx = (Int(d[row + (x >> 1)]) >> ((x & 1) == 1 ? 0 : 4)) & 15 }
                else { idx = (Int(d[row + (x >> 3)]) >> (7 - (x & 7))) & 1 }
                isI = idx < palInk.count && palInk[idx]
            } else if bpp == 24 {
                let p = row + 3 * x
                isI = isInk(Int(d[p + 2]), Int(d[p + 1]), Int(d[p]))
            } else if comp == 0 {
                let p = row + 4 * x
                isI = isInk(Int(d[p + 2]), Int(d[p + 1]), Int(d[p]))
            } else {
                let px = u32(d, row + 4 * x)
                isI = isInk(chan(px, rm), chan(px, gm), chan(px, bm))
            }
            if isI { ink[y * w + x] = 1; inkCount += 1 }
        }
    }
    out += "size \(w)x\(h) bpp \(bpp) header \(hsz) \(bottomUp ? "bottom-up" : "top-down") ink \(inkCount)\n"
    var lines = ""
    var ncomp = 0
    var stack: [Int] = []
    for y in 0..<h {
        for x in 0..<w {
            let i0 = y * w + x
            if ink[i0] != 1 { continue }
            ink[i0] = 2
            stack.append(i0)
            var minx = x, maxx = x, miny = y, maxy = y, cnt = 0
            while let i = stack.popLast() {
                let cy = i / w, cx = i % w
                cnt += 1
                if cx < minx { minx = cx }; if cx > maxx { maxx = cx }
                if cy < miny { miny = cy }; if cy > maxy { maxy = cy }
                for dy in -1...1 {
                    for dx in -1...1 {
                        let nx = cx + dx, ny = cy + dy
                        if nx < 0 || ny < 0 || nx >= w || ny >= h { continue }
                        let j = ny * w + nx
                        if ink[j] == 1 { ink[j] = 2; stack.append(j) }
                    }
                }
            }
            ncomp += 1
            lines += "\(x),\(y) bbox \(minx),\(miny)-\(maxx),\(maxy) pixels \(cnt)\n"
        }
    }
    out += "components \(ncomp)\n"
    out += lines
}

let args = CommandLine.arguments
if args.count >= 2 {
    let dir = args[1]
    let all = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
    var names = all.filter { $0.hasSuffix(".bmp") }
    names.sort { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
    var out = ""
    for n in names { processFile(dir, n, &out) }
    FileHandle.standardOutput.write(out.data(using: .utf8)!)
}
