import Foundation

let BASE: UInt64 = 1_000_000_000

func trim(_ a: inout [UInt32]) { while let l = a.last, l == 0 { a.removeLast() } }
func mcmp(_ a: [UInt32], _ b: [UInt32]) -> Int {
    if a.count != b.count { return a.count < b.count ? -1 : 1 }
    var i = a.count - 1
    while i >= 0 { if a[i] != b[i] { return a[i] < b[i] ? -1 : 1 }; i -= 1 }
    return 0
}
func madd(_ a: [UInt32], _ b: [UInt32]) -> [UInt32] {
    let n = max(a.count, b.count)
    var r = [UInt32](); r.reserveCapacity(n + 1)
    var c: UInt64 = 0
    for i in 0..<n {
        let s = c + UInt64(i < a.count ? a[i] : 0) + UInt64(i < b.count ? b[i] : 0)
        r.append(UInt32(s % BASE)); c = s / BASE
    }
    if c > 0 { r.append(UInt32(c)) }
    return r
}
func msub(_ a: [UInt32], _ b: [UInt32]) -> [UInt32] {
    var r = [UInt32](); var br: Int64 = 0
    for i in 0..<a.count {
        var s = Int64(a[i]) - br - Int64(i < b.count ? b[i] : 0)
        if s < 0 { s += Int64(BASE); br = 1 } else { br = 0 }
        r.append(UInt32(s))
    }
    trim(&r); return r
}
func mmul(_ a: [UInt32], _ b: [UInt32]) -> [UInt32] {
    if a.isEmpty || b.isEmpty { return [] }
    var r = [UInt64](repeating: 0, count: a.count + b.count + 1)
    for i in 0..<a.count {
        var c: UInt64 = 0
        for j in 0..<b.count {
            let t = r[i + j] + UInt64(a[i]) * UInt64(b[j]) + c
            r[i + j] = t % BASE; c = t / BASE
        }
        var k = i + b.count
        while c > 0 { let t = r[k] + c; r[k] = t % BASE; c = t / BASE; k += 1 }
    }
    var v = r.map { UInt32($0) }
    trim(&v); return v
}
func mfromDigits(_ s: String) -> [UInt32] {
    let b = Array(s.utf8)
    var r = [UInt32]()
    var end = b.count
    while end > 0 {
        let st = max(0, end - 9)
        var x: UInt32 = 0
        for i in st..<end { x = x * 10 + UInt32(b[i] - 48) }
        r.append(x); end = st
    }
    trim(&r); return r
}
func mtoStr(_ a: [UInt32]) -> String {
    if a.isEmpty { return "0" }
    var s = String(a[a.count - 1])
    var i = a.count - 2
    while i >= 0 {
        let p = String(a[i])
        s += String(repeating: "0", count: 9 - p.count) + p
        i -= 1
    }
    return s
}
func mdivmod(_ a: [UInt32], _ b: [UInt32]) -> ([UInt32], [UInt32]) {
    var q = ""
    var rem = [UInt32]()
    for ch in mtoStr(a).utf8 {
        rem = madd(mmul(rem, [10]), ch == 48 ? [] : [UInt32(ch - 48)])
        var d = 0
        while mcmp(rem, b) >= 0 { rem = msub(rem, b); d += 1 }
        q += String(d)
    }
    return (mfromDigits(q), rem)
}

struct Big {
    var neg: Bool; var m: [UInt32]
    static let zero = Big(neg: false, m: [])
    func norm() -> Big { var x = self; trim(&x.m); if x.m.isEmpty { x.neg = false }; return x }
    var sign: Int { m.isEmpty ? 0 : (neg ? -1 : 1) }
    func negate() -> Big { Big(neg: !neg, m: m).norm() }
    func add(_ o: Big) -> Big {
        if neg == o.neg { return Big(neg: neg, m: madd(m, o.m)).norm() }
        if mcmp(m, o.m) < 0 { return Big(neg: o.neg, m: msub(o.m, m)).norm() }
        return Big(neg: neg, m: msub(m, o.m)).norm()
    }
    func sub(_ o: Big) -> Big { add(o.negate()) }
    func mul(_ o: Big) -> Big { Big(neg: neg != o.neg, m: mmul(m, o.m)).norm() }
    func cmp(_ o: Big) -> Int {
        if sign != o.sign { return sign < o.sign ? -1 : 1 }
        let c = mcmp(m, o.m)
        return neg ? -c : c
    }
}

struct Dec { var v: Big; var s: Int }
func parse(_ t0: String) -> Dec {
    var t = Substring(t0); var neg = false
    if t.hasPrefix("-") { neg = true; t = t.dropFirst() }
    var ip = t, fp = Substring("")
    if let d = t.firstIndex(of: ".") { ip = t[..<d]; fp = t[t.index(after: d)...] }
    return Dec(v: Big(neg: neg, m: mfromDigits(String(ip) + String(fp))).norm(), s: fp.count)
}
func rnd(_ d: Dec, _ m: Int) -> Big {
    var s = mtoStr(d.v.m)
    if d.s <= m {
        s += String(repeating: "0", count: m - d.s)
        return Big(neg: d.v.neg, m: mfromDigits(s)).norm()
    }
    let k = d.s - m
    if s.count <= k { s = String(repeating: "0", count: k + 1 - s.count) + s }
    let b = Array(s.utf8)
    let q = String(decoding: b[0..<(b.count - k)], as: UTF8.self)
    let tail = Array(b[(b.count - k)...])
    let half = Array(("5" + String(repeating: "0", count: k - 1)).utf8)
    var c = 0
    for i in 0..<k { if tail[i] != half[i] { c = tail[i] < half[i] ? -1 : 1; break } }
    var qm = mfromDigits(q)
    let odd = (b[b.count - k - 1] - 48) % 2 == 1
    if c > 0 || (c == 0 && odd) { qm = madd(qm, [1]) }
    return Big(neg: d.v.neg, m: qm).norm()
}
func fmt(_ v: Big, _ m: Int) -> String {
    var s = mtoStr(v.m)
    if s.count <= m { s = String(repeating: "0", count: m + 1 - s.count) + s }
    let b = Array(s)
    let ip = b[0..<(b.count - m)], fp = b[(b.count - m)...]
    var g = ""
    let n = ip.count
    for (i, ch) in ip.enumerated() {
        if i > 0 && (n - i) % 3 == 0 { g += "," }
        g.append(ch)
    }
    var r = v.sign < 0 ? "-" : ""
    r += g
    if m > 0 { r += "." + String(fp) }
    return r
}

final class Acct { var cur: String; var bal: Big; var limit: Big
    init(cur: String, bal: Big, limit: Big) { self.cur = cur; self.bal = bal; self.limit = limit } }
var curs = [String: Int]()
var accts = [String: Acct]()

func unknown(_ names: [String]) -> String? {
    for n in names where accts[n] == nil { return "unknown account " + n }
    return nil
}
func insufficient(_ a: Acct, _ amt: Big) -> Bool { a.bal.sub(amt).cmp(a.limit.negate()) < 0 }

func exec(_ f: [String]) -> String? {
    let cmd = f[0]
    switch cmd {
    case "currency": curs[f[1]] = Int(f[2])!; return nil
    case "open":
        if accts[f[1]] != nil { return "account exists " + f[1] }
        let m = curs[f[2]]!
        accts[f[1]] = Acct(cur: f[2], bal: .zero, limit: f.count > 3 ? rnd(parse(f[3]), m) : .zero)
        return nil
    case "deposit", "withdraw":
        if let u = unknown([f[1]]) { return u }
        let a = accts[f[1]]!
        let amt = rnd(parse(f[2]), curs[a.cur]!)
        if amt.sign <= 0 { return "non-positive amount" }
        if cmd == "withdraw" {
            if insufficient(a, amt) { return "insufficient funds " + f[1] }
            a.bal = a.bal.sub(amt)
        } else { a.bal = a.bal.add(amt) }
        return nil
    case "transfer":
        if let u = unknown([f[1], f[2]]) { return u }
        let a = accts[f[1]]!, b = accts[f[2]]!
        if a.cur != b.cur { return "currency mismatch" }
        let amt = rnd(parse(f[3]), curs[a.cur]!)
        if amt.sign <= 0 { return "non-positive amount" }
        if insufficient(a, amt) { return "insufficient funds " + f[1] }
        a.bal = a.bal.sub(amt); b.bal = b.bal.add(amt)
        return nil
    case "convert":
        if let u = unknown([f[1], f[2]]) { return u }
        let a = accts[f[1]]!, b = accts[f[2]]!
        let ma = curs[a.cur]!, mb = curs[b.cur]!
        let amt = rnd(parse(f[3]), ma)
        if amt.sign <= 0 { return "non-positive amount" }
        if insufficient(a, amt) { return "insufficient funds " + f[1] }
        let r = parse(f[4])
        let got = rnd(Dec(v: amt.mul(r.v), s: ma + r.s), mb)
        a.bal = a.bal.sub(amt); b.bal = b.bal.add(got)
        return nil
    case "interest":
        if let u = unknown([f[1]]) { return u }
        let a = accts[f[1]]!
        let m = curs[a.cur]!
        let r = parse(f[2])
        a.bal = a.bal.add(rnd(Dec(v: a.bal.mul(r.v), s: m + r.s), m))
        return nil
    case "split":
        var names = [f[1]]
        var w = [[UInt32]]()
        for t in f[3...] {
            let c = t.lastIndex(of: ":")!
            names.append(String(t[..<c]))
            w.append(mfromDigits(String(t[t.index(after: c)...])))
        }
        if let u = unknown(names) { return u }
        let a = accts[f[1]]!
        for n in names.dropFirst() where accts[n]!.cur != a.cur { return "currency mismatch" }
        let amt = rnd(parse(f[2]), curs[a.cur]!)
        if amt.sign <= 0 { return "non-positive amount" }
        if insufficient(a, amt) { return "insufficient funds " + f[1] }
        var wsum = [UInt32]()
        for x in w { wsum = madd(wsum, x) }
        var q = [[UInt32]](), rem = [[UInt32]]()
        var given = [UInt32]()
        for x in w {
            let (qq, rr) = mdivmod(mmul(amt.m, x), wsum)
            given = madd(given, qq); q.append(qq); rem.append(rr)
        }
        let left = Int(mtoStr(msub(amt.m, given)))!
        let idx = (0..<w.count).sorted { x, y in
            let c = mcmp(rem[y], rem[x]); return c != 0 ? c < 0 : x < y }
        for j in 0..<left { q[idx[j]] = madd(q[idx[j]], [1]) }
        a.bal = a.bal.sub(amt)
        for i in 0..<w.count {
            let t = accts[names[i + 1]]!
            t.bal = t.bal.add(Big(neg: false, m: q[i]).norm())
        }
        return nil
    default: return nil
    }
}

let dir = CommandLine.arguments[1]
let data = FileManager.default.contents(atPath: (dir as NSString).appendingPathComponent("ledger.txt")) ?? Data()
let text = String(decoding: data, as: UTF8.self)
var lines = text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
var out = ""
for (i, raw) in lines.enumerated() {
    let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if line.isEmpty || line.hasPrefix("#") { continue }
    let f = line.split(separator: " ").map(String.init)
    if let r = exec(f) { out += "line \(i + 1): REJECT \(r)\n" }
}
out += "\n"
let names = accts.keys.sorted { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
var tot = [String: Big](), cnt = [String: Int]()
for n in names {
    let a = accts[n]!
    out += "\(n) \(a.cur) \(fmt(a.bal, curs[a.cur]!))\n"
    tot[a.cur] = (tot[a.cur] ?? .zero).add(a.bal)
    cnt[a.cur, default: 0] += 1
}
out += "\n"
for c in tot.keys.sorted(by: { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }) {
    out += "total \(c) \(fmt(tot[c]!, curs[c]!)) (\(cnt[c]!) accounts)\n"
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
