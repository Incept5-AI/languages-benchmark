import Foundation

let dir = CommandLine.arguments[1]
let data = try! Data(contentsOf: URL(fileURLWithPath: dir).appendingPathComponent("scene.json"))
let root = try! JSONSerialization.jsonObject(with: data) as! [String: Any]

func num(_ v: Any?) -> Int64 {
    if let n = v as? NSNumber { return n.int64Value }
    return 0
}
func arr(_ v: Any?) -> [Any] { return (v as? [Any]) ?? [] }

let W = Int(num(root["width"]))
let H = Int(num(root["height"]))
let bg = arr(root["background"]).map { UInt8(num($0)) }
var img = [UInt8](repeating: 0, count: W * H * 3)
for i in 0..<(W * H) {
    img[i * 3] = bg[0]; img[i * 3 + 1] = bg[1]; img[i * 3 + 2] = bg[2]
}

var out = ""
let shapes = arr(root["shapes"])
for (si, sAny) in shapes.enumerated() {
    let s = sAny as! [String: Any]
    let type = s["type"] as! String
    let col = arr(s["color"]).map { num($0) }
    let A = col[3]
    var kind = 0
    var x0: Int64 = 0, y0: Int64 = 0, x1: Int64 = 0, y1: Int64 = 0
    var cx: Int64 = 0, cy: Int64 = 0, r: Int64 = 0
    var lx1: Int64 = 0, ly1: Int64 = 0, lx2: Int64 = 0, ly2: Int64 = 0
    var px = [Int64](repeating: 0, count: 3), py = [Int64](repeating: 0, count: 3)
    switch type {
    case "rect":
        kind = 0
        let rx = num(s["x"]), ry = num(s["y"])
        x0 = rx; y0 = ry; x1 = rx + num(s["w"]) - 1; y1 = ry + num(s["h"]) - 1
    case "circle":
        kind = 1
        cx = num(s["cx"]); cy = num(s["cy"]); r = num(s["r"])
        x0 = cx - r; x1 = cx + r; y0 = cy - r; y1 = cy + r
    case "triangle":
        kind = 2
        let pts = arr(s["points"])
        for i in 0..<3 {
            let p = arr(pts[i])
            px[i] = num(p[0]); py[i] = num(p[1])
        }
        x0 = px.min()!; x1 = px.max()!; y0 = py.min()!; y1 = py.max()!
    default:
        kind = 3
        lx1 = num(s["x1"]); ly1 = num(s["y1"]); lx2 = num(s["x2"]); ly2 = num(s["y2"]); r = num(s["r"])
        x0 = min(lx1, lx2) - r; x1 = max(lx1, lx2) + r
        y0 = min(ly1, ly2) - r; y1 = max(ly1, ly2) + r
    }
    x0 = max(x0, 0); y0 = max(y0, 0)
    x1 = min(x1, Int64(W - 1)); y1 = min(y1, Int64(H - 1))
    let r2 = r * r
    let dx = lx2 - lx1, dy = ly2 - ly1, L = dx * dx + dy * dy
    var count = 0
    if x0 <= x1 && y0 <= y1 {
        img.withUnsafeMutableBufferPointer { buf in
            for y in y0...y1 {
                for x in x0...x1 {
                    var cov = false
                    switch kind {
                    case 0: cov = true
                    case 1: cov = (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2
                    case 2:
                        var p = true, n = true
                        for i in 0..<3 {
                            let j = (i + 1) % 3
                            let e = (px[j] - px[i]) * (y - py[i]) - (py[j] - py[i]) * (x - px[i])
                            if e < 0 { p = false }
                            if e > 0 { n = false }
                        }
                        cov = p || n
                    default:
                        let t = (x - lx1) * dx + (y - ly1) * dy
                        if t <= 0 {
                            cov = (x - lx1) * (x - lx1) + (y - ly1) * (y - ly1) <= r2
                        } else if t >= L {
                            cov = (x - lx2) * (x - lx2) + (y - ly2) * (y - ly2) <= r2
                        } else {
                            let c = (x - lx1) * dy - (y - ly1) * dx
                            cov = c * c <= r2 * L
                        }
                    }
                    if cov {
                        count += 1
                        let o = Int((y * Int64(W) + x) * 3)
                        for c in 0..<3 {
                            let d = Int64(buf[o + c])
                            buf[o + c] = UInt8((col[c] * A + d * (255 - A) + 127) / 255)
                        }
                    }
                }
            }
        }
    }
    out += "\(si + 1) \(type) \(count)\n"
}

var seen = [UInt8](repeating: 0, count: 1 << 21)
var k = 0
for i in 0..<(W * H) {
    let v = (Int(img[i * 3]) << 16) | (Int(img[i * 3 + 1]) << 8) | Int(img[i * 3 + 2])
    if seen[v >> 3] & UInt8(1 << (v & 7)) == 0 {
        seen[v >> 3] |= UInt8(1 << (v & 7))
        k += 1
    }
}
out += "colors \(k)\n"

// PNG encoding with stored DEFLATE blocks
var crcTable = [UInt32](repeating: 0, count: 256)
for n in 0..<256 {
    var c = UInt32(n)
    for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB88320 ^ (c >> 1)) : (c >> 1) }
    crcTable[n] = c
}
func be32(_ v: UInt32) -> [UInt8] {
    return [UInt8(v >> 24 & 0xff), UInt8(v >> 16 & 0xff), UInt8(v >> 8 & 0xff), UInt8(v & 0xff)]
}
func chunk(_ type: String, _ d: [UInt8]) -> [UInt8] {
    let t = Array(type.utf8)
    var c: UInt32 = 0xffffffff
    for b in t { c = crcTable[Int((c ^ UInt32(b)) & 0xff)] ^ (c >> 8) }
    for b in d { c = crcTable[Int((c ^ UInt32(b)) & 0xff)] ^ (c >> 8) }
    return be32(UInt32(d.count)) + t + d + be32(c ^ 0xffffffff)
}
let stride = W * 3
var raw = [UInt8](repeating: 0, count: H * (stride + 1))
for y in 0..<H {
    for i in 0..<stride { raw[y * (stride + 1) + 1 + i] = img[y * stride + i] }
}
var z: [UInt8] = [0x78, 0x01]
z.reserveCapacity(raw.count + raw.count / 65535 * 5 + 16)
var off = 0
while true {
    let n = min(65535, raw.count - off)
    let last = off + n >= raw.count
    z.append(last ? 1 : 0)
    z.append(UInt8(n & 0xff)); z.append(UInt8(n >> 8))
    let nn = ~n & 0xffff
    z.append(UInt8(nn & 0xff)); z.append(UInt8(nn >> 8))
    z.append(contentsOf: raw[off..<(off + n)])
    off += n
    if last { break }
}
var a: UInt32 = 1, b: UInt32 = 0
for v in raw { a = (a + UInt32(v)) % 65521; b = (b + a) % 65521 }
z += be32((b << 16) | a)
var ihdr = be32(UInt32(W)) + be32(UInt32(H))
ihdr += [8, 2, 0, 0, 0]
var png: [UInt8] = [137, 80, 78, 71, 13, 10, 26, 10]
png += chunk("IHDR", ihdr)
png += chunk("IDAT", z)
png += chunk("IEND", [])
try! Data(png).write(to: URL(fileURLWithPath: "out.png"))
FileHandle.standardOutput.write(out.data(using: .utf8)!)
