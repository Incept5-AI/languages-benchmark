import Foundation

struct TType {
    var off: Int64
    var abbr: String
}

struct Rule {
    var m: Int64, w: Int64, d: Int64, t: Int64
}

func fdiv(_ a: Int64, _ b: Int64) -> Int64 {
    var q = a / b
    if a % b != 0 && ((a < 0) != (b < 0)) { q -= 1 }
    return q
}
func fmod(_ a: Int64, _ b: Int64) -> Int64 { return a - fdiv(a, b) * b }

func daysFromCivil(_ y0: Int64, _ m: Int64, _ d: Int64) -> Int64 {
    let y = m <= 2 ? y0 - 1 : y0
    let era = fdiv(y, 400)
    let yoe = y - era * 400
    let mp = m > 2 ? m - 3 : m + 9
    let doy = (153 * mp + 2) / 5 + d - 1
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
    return era * 146097 + doe - 719468
}

func civilFromDays(_ z0: Int64) -> (Int64, Int64, Int64) {
    let z = z0 + 719468
    let era = fdiv(z, 146097)
    let doe = z - era * 146097
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365
    let y = yoe + era * 400
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
    let mp = (5 * doy + 2) / 153
    let d = doy - (153 * mp + 2) / 5 + 1
    let m = mp < 10 ? mp + 3 : mp - 9
    return (m <= 2 ? y + 1 : y, m, d)
}

func monthLen(_ y: Int64, _ m: Int64) -> Int64 {
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
    if m == 2 { return leap ? 29 : 28 }
    if m == 4 || m == 6 || m == 9 || m == 11 { return 30 }
    return 31
}

func ruleLocal(_ y: Int64, _ r: Rule) -> Int64 {
    let first = daysFromCivil(y, r.m, 1)
    let dow = fmod(first + 4, 7)
    var day = 1 + (r.d - dow + 7) % 7 + (r.w - 1) * 7
    let ml = monthLen(y, r.m)
    while day > ml { day -= 7 }
    return (first + day - 1) * 86400 + r.t
}

func yearOf(_ t: Int64) -> Int64 { return civilFromDays(fdiv(t, 86400)).0 }

final class P {
    let s: [UInt8]
    var i = 0
    init(_ s: [UInt8]) { self.s = s }
    func peek() -> UInt8 { return i < s.count ? s[i] : 0 }
    func name() -> String {
        var out: [UInt8] = []
        if peek() == UInt8(ascii: "<") {
            i += 1
            while peek() != 0 && peek() != UInt8(ascii: ">") { out.append(peek()); i += 1 }
            if peek() == UInt8(ascii: ">") { i += 1 }
        } else {
            while (peek() >= 65 && peek() <= 90) || (peek() >= 97 && peek() <= 122) { out.append(peek()); i += 1 }
        }
        return String(decoding: out, as: UTF8.self)
    }
    func num() -> Int64 {
        var v: Int64 = 0
        while peek() >= 48 && peek() <= 57 { v = v * 10 + Int64(peek() - 48); i += 1 }
        return v
    }
    func hms() -> Int64 {
        var sign: Int64 = 1
        if peek() == UInt8(ascii: "+") { i += 1 } else if peek() == UInt8(ascii: "-") { sign = -1; i += 1 }
        let h = num()
        var m: Int64 = 0, sec: Int64 = 0
        if peek() == UInt8(ascii: ":") {
            i += 1; m = num()
            if peek() == UInt8(ascii: ":") { i += 1; sec = num() }
        }
        return sign * (h * 3600 + m * 60 + sec)
    }
    func rule(_ r: inout Rule) {
        r.t = 7200
        if peek() == UInt8(ascii: "M") {
            i += 1
            r.m = num(); if peek() == UInt8(ascii: ".") { i += 1 }
            r.w = num(); if peek() == UInt8(ascii: ".") { i += 1 }
            r.d = num()
        }
        if peek() == UInt8(ascii: "/") { i += 1; r.t = hms() }
    }
}

final class Zone {
    var times: [Int64] = []
    var idx: [Int] = []
    var types: [TType] = []
    var hasFooter = false
    var fstd = TType(off: 0, abbr: "")
    var fdst: TType? = nil
    var start = Rule(m: 3, w: 2, d: 0, t: 7200)
    var end = Rule(m: 11, w: 1, d: 0, t: 7200)

    init(_ b: [UInt8]) {
        func be(_ p: Int, _ n: Int) -> Int64 {
            var v: UInt64 = 0
            for k in 0..<n { v = (v << 8) | UInt64(b[p + k]) }
            if n == 4 { return Int64(Int32(bitPattern: UInt32(v))) }
            return Int64(bitPattern: v)
        }
        func cnt(_ p: Int) -> [Int] { return (0..<6).map { Int(be(p + 20 + 4 * $0, 4)) } }
        var c = cnt(0)
        let v1 = c[3] * 5 + c[4] * 6 + c[5] + c[2] * 8 + c[1] + c[0]
        var p = 44 + v1
        c = cnt(p)
        let isut = c[0], isstd = c[1], leap = c[2], tc = c[3], ty = c[4], ch = c[5]
        p += 44
        times = (0..<tc).map { be(p + 8 * $0, 8) }
        p += 8 * tc
        idx = (0..<tc).map { Int(b[p + $0]) }
        p += tc
        let tp = p
        p += 6 * ty
        let cp = p
        for i in 0..<ty {
            let off = be(tp + 6 * i, 4)
            let ai = min(Int(b[tp + 6 * i + 5]), ch)
            var e = ai
            while e < ch && b[cp + e] != 0 { e += 1 }
            types.append(TType(off: off, abbr: String(decoding: b[(cp + ai)..<(cp + e)], as: UTF8.self)))
        }
        p += ch + leap * 12 + isstd + isut
        var foot: [UInt8] = []
        if p < b.count && b[p] == 10 {
            p += 1
            while p < b.count && b[p] != 10 { foot.append(b[p]); p += 1 }
        }
        hasFooter = !foot.isEmpty
        if hasFooter {
            let ps = P(foot)
            let n = ps.name()
            let o = ps.hms()
            fstd = TType(off: -o, abbr: n)
            if ps.peek() != 0 && ps.peek() != UInt8(ascii: ",") {
                let dn = ps.name()
                var doff: Int64
                if ps.peek() != 0 && ps.peek() != UInt8(ascii: ",") { doff = -ps.hms() } else { doff = fstd.off + 3600 }
                fdst = TType(off: doff, abbr: dn)
                if ps.peek() == UInt8(ascii: ",") {
                    ps.i += 1
                    ps.rule(&start)
                    if ps.peek() == UInt8(ascii: ",") { ps.i += 1; ps.rule(&end) }
                }
            }
        }
    }

    func events(_ y: Int64) -> [Int64] {
        return [ruleLocal(y, start) - fstd.off, ruleLocal(y, end) - fdst!.off]
    }

    func footerType(_ t: Int64) -> TType {
        guard let d = fdst else { return fstd }
        let Y = yearOf(t)
        var best: Int64? = nil
        var bestDst = false
        for y in (Y - 2)...(Y + 2) {
            let ev = events(y)
            if ev[0] <= t && (best == nil || ev[0] > best!) { best = ev[0]; bestDst = true }
            if ev[1] <= t && (best == nil || ev[1] > best!) { best = ev[1]; bestDst = false }
        }
        return bestDst ? d : fstd
    }

    func typeAt(_ t: Int64) -> TType {
        let n = times.count
        if n == 0 { return hasFooter ? footerType(t) : types[0] }
        if t < times[0] { return types[0] }
        if t > times[n - 1] { return hasFooter ? footerType(t) : types[idx[n - 1]] }
        var lo = 0, hi = n - 1
        while lo < hi {
            let mid = (lo + hi + 1) / 2
            if times[mid] <= t { lo = mid } else { hi = mid - 1 }
        }
        return types[idx[lo]]
    }
}

func p2(_ v: Int64) -> String { return v < 10 ? "0\(v)" : "\(v)" }

func fmtOff(_ off: Int64) -> String {
    let sign = off < 0 ? "-" : "+"
    let a = abs(off)
    var r = sign + p2(a / 3600) + ":" + p2((a / 60) % 60)
    if a % 60 != 0 { r += ":" + p2(a % 60) }
    return r
}

func fmtLocal(_ t: Int64, _ off: Int64) -> String {
    let l = t + off
    let (y, m, d) = civilFromDays(fdiv(l, 86400))
    let s = fmod(l, 86400)
    var ys = String(y)
    while ys.count < 4 { ys = "0" + ys }
    return ys + "-" + p2(m) + "-" + p2(d) + "T" + p2(s / 3600) + ":" + p2((s / 60) % 60) + ":" + p2(s % 60) + fmtOff(off)
}

let dir = CommandLine.arguments[1]
let qdata = FileManager.default.contents(atPath: dir + "/queries.txt") ?? Data()
let qtext = String(decoding: qdata, as: UTF8.self)
var zones: [String: Zone] = [:]
var out = ""

for rawSub in qtext.split(separator: "\n", omittingEmptySubsequences: false) {
    var line = String(rawSub)
    if line.hasSuffix("\r") { line.removeLast() }
    if line.trimmingCharacters(in: .whitespaces).isEmpty { continue }
    let parts = line.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
    if parts.count < 3 { continue }
    var zz = zones[parts[1]]
    if zz == nil {
        guard let data = FileManager.default.contents(atPath: dir + "/" + parts[1]) else { continue }
        zz = Zone([UInt8](data))
        zones[parts[1]] = zz
    }
    let z = zz!
    if parts[0] == "UTC" {
        let t = Int64(parts[2])!
        let tt = z.typeAt(t)
        out += "\(line) -> \(fmtLocal(t, tt.off)) \(tt.abbr)\n"
    } else {
        let a = Array(parts[2].utf8)
        func num(_ s: Int, _ e: Int) -> Int64 { return Int64(String(decoding: a[s..<e], as: UTF8.self))! }
        let W = daysFromCivil(num(0, 4), num(5, 7), num(8, 10)) * 86400 + num(11, 13) * 3600 + num(14, 16) * 60 + num(17, 19)
        var offs = z.types.map { $0.off }
        if z.hasFooter {
            offs.append(z.fstd.off)
            if let d = z.fdst { offs.append(d.off) }
        }
        let minoff = offs.min()!, maxoff = offs.max()!
        var res: [Int64] = []
        for o in offs {
            let t = W - o
            if z.typeAt(t).off == o && !res.contains(t) { res.append(t) }
        }
        if !res.isEmpty {
            res.sort()
            let s = res.map { t -> String in
                let tt = z.typeAt(t)
                return "\(t) \(fmtOff(tt.off)) \(tt.abbr)"
            }
            out += "\(line) -> " + s.joined(separator: " | ") + "\n"
        } else {
            let lo = W - maxoff - 2, hi = W - minoff + 2
            var c = z.times.filter { $0 >= lo && $0 <= hi }
            let lastT = z.times.last ?? (Int64.min / 2)
            if !z.times.isEmpty && lastT + 1 >= lo && lastT + 1 <= hi { c.append(lastT + 1) }
            if z.fdst != nil {
                for y in (yearOf(lo) - 1)...(yearOf(hi) + 1) {
                    for x in z.events(y) where x > lastT && x >= lo && x <= hi { c.append(x) }
                }
            }
            c.sort()
            var t = W - z.typeAt(W).off
            for tr in c {
                let ob = z.typeAt(tr - 1).off, oa = z.typeAt(tr).off
                if tr + ob <= W && W < tr + oa { t = W - ob; break }
            }
            let tt = z.typeAt(t)
            out += "\(line) -> GAP \(t) \(fmtLocal(t, tt.off)) \(tt.abbr)\n"
        }
    }
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
