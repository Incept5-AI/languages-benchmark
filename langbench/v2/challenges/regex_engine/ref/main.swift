import Foundation

final class Node {
    enum Kind { case char, any, cls, bol, eol, cat, alt, group, rep }
    let kind: Kind
    var c = 0, cls = 0, gidx = 0, min = 0, max = 0
    var greedy = true
    var kids: [Node] = []
    init(_ k: Kind) { kind = k }
}

func inset(_ e: UInt8, _ c: Int) -> Bool {
    switch e {
    case UInt8(ascii: "d"): return c >= 48 && c <= 57
    case UInt8(ascii: "w"): return (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c == 95
    case UInt8(ascii: "s"): return c == 32 || c == 9 || c == 10 || c == 13 || c == 12 || c == 11
    case UInt8(ascii: "D"): return !inset(UInt8(ascii: "d"), c)
    case UInt8(ascii: "W"): return !inset(UInt8(ascii: "w"), c)
    case UInt8(ascii: "S"): return !inset(UInt8(ascii: "s"), c)
    default: return false
    }
}
func isClsEsc(_ e: UInt8) -> Bool { return Array("dwsDWS".utf8).contains(e) }

final class Parser {
    let p: [UInt8]
    var i = 0
    var ngroups = 0
    var classes: [[Bool]] = []
    init(_ p: [UInt8]) { self.p = p }
    func peek(_ k: Int) -> UInt8 { return i + k < p.count ? p[i + k] : 0 }
    func regex() -> Node {
        let alt = Node(.alt)
        alt.kids.append(branch())
        while peek(0) == UInt8(ascii: "|") { i += 1; alt.kids.append(branch()) }
        return alt
    }
    func readint() -> Int {
        var v = 0
        while peek(0) >= 48 && peek(0) <= 57 { v = v * 10 + Int(peek(0) - 48); i += 1 }
        return v
    }
    func branch() -> Node {
        let cat = Node(.cat)
        while i < p.count && peek(0) != UInt8(ascii: "|") && peek(0) != UInt8(ascii: ")") {
            var a = atom()
            let q = peek(0)
            var mn = -1, mx = -1
            if q == UInt8(ascii: "*") { mn = 0; i += 1 }
            else if q == UInt8(ascii: "+") { mn = 1; i += 1 }
            else if q == UInt8(ascii: "?") { mn = 0; mx = 1; i += 1 }
            else if q == UInt8(ascii: "{") {
                i += 1; mn = readint()
                if peek(0) == UInt8(ascii: "}") { mx = mn; i += 1 }
                else {
                    i += 1
                    if peek(0) == UInt8(ascii: "}") { mx = -1; i += 1 } else { mx = readint(); i += 1 }
                }
            }
            if mn >= 0 {
                let r = Node(.rep); r.min = mn; r.max = mx; r.greedy = true
                if peek(0) == UInt8(ascii: "?") { r.greedy = false; i += 1 }
                r.kids.append(a); a = r
            }
            cat.kids.append(a)
        }
        return cat
    }
    func atom() -> Node {
        let ch = peek(0)
        if ch == UInt8(ascii: "(") {
            i += 1
            if peek(0) == UInt8(ascii: "?") && peek(1) == UInt8(ascii: ":") { i += 2; let r = regex(); i += 1; return r }
            ngroups += 1
            let g = Node(.group); g.gidx = ngroups
            g.kids.append(regex()); i += 1
            return g
        }
        if ch == UInt8(ascii: ".") { i += 1; return Node(.any) }
        if ch == UInt8(ascii: "^") { i += 1; return Node(.bol) }
        if ch == UInt8(ascii: "$") { i += 1; return Node(.eol) }
        if ch == UInt8(ascii: "[") {
            i += 1
            var neg = false
            if peek(0) == UInt8(ascii: "^") { neg = true; i += 1 }
            var t = [Bool](repeating: false, count: 128)
            while i < p.count && peek(0) != UInt8(ascii: "]") {
                var lo: Int
                if peek(0) == UInt8(ascii: "\\") {
                    let e = peek(1); i += 2
                    if isClsEsc(e) { for c in 0..<128 where inset(e, c) { t[c] = true }; continue }
                    lo = Int(e)
                } else { lo = Int(peek(0)); i += 1 }
                var hi = lo
                if peek(0) == UInt8(ascii: "-") && peek(1) != UInt8(ascii: "]") {
                    i += 1
                    if peek(0) == UInt8(ascii: "\\") { hi = Int(peek(1)); i += 2 } else { hi = Int(peek(0)); i += 1 }
                }
                var c = lo
                while c <= hi && c < 128 { t[c] = true; c += 1 }
            }
            i += 1
            if neg { for c in 0..<128 { t[c] = !t[c] } }
            classes.append(t)
            let n = Node(.cls); n.cls = classes.count - 1; return n
        }
        if ch == UInt8(ascii: "\\") {
            let e = peek(1); i += 2
            if isClsEsc(e) {
                var t = [Bool](repeating: false, count: 128)
                for c in 0..<128 { t[c] = inset(e, c) }
                classes.append(t)
                let n = Node(.cls); n.cls = classes.count - 1; return n
            }
            let n = Node(.char); n.c = Int(e); return n
        }
        i += 1
        let n = Node(.char); n.c = Int(ch); return n
    }
}

let I_CHAR = 0, I_ANY = 1, I_CLASS = 2, I_BOL = 3, I_EOL = 4, I_SAVE = 5, I_SPLIT = 6, I_JMP = 7, I_LOOP = 8, I_ITEREND = 9, I_MATCH = 10

final class Comp {
    var op: [Int] = [], a: [Int] = [], b: [Int] = [], c: [Int] = []
    var maxlevel = 0
    @discardableResult func emit(_ o: Int, _ x: Int, _ y: Int, _ z: Int) -> Int {
        op.append(o); a.append(x); b.append(y); c.append(z); return op.count - 1
    }
    func compile(_ n: Node, _ level: Int) {
        if level > maxlevel { maxlevel = level }
        switch n.kind {
        case .char: emit(I_CHAR, n.c, 0, 0)
        case .any: emit(I_ANY, 0, 0, 0)
        case .cls: emit(I_CLASS, n.cls, 0, 0)
        case .bol: emit(I_BOL, 0, 0, 0)
        case .eol: emit(I_EOL, 0, 0, 0)
        case .cat: for k in n.kids { compile(k, level) }
        case .group:
            emit(I_SAVE, 2 * n.gidx, 0, 0); compile(n.kids[0], level); emit(I_SAVE, 2 * n.gidx + 1, 0, 0)
        case .alt:
            if n.kids.count == 1 { compile(n.kids[0], level); return }
            var jmps: [Int] = []
            for (i, k) in n.kids.enumerated() {
                if i + 1 < n.kids.count {
                    let s = emit(I_SPLIT, 0, 0, 0)
                    a[s] = op.count
                    compile(k, level)
                    jmps.append(emit(I_JMP, 0, 0, 0))
                    b[s] = op.count
                } else { compile(k, level) }
            }
            for j in jmps { a[j] = op.count }
        case .rep:
            let k = n.kids[0]
            for _ in 0..<n.min { compile(k, level) }
            let g = n.greedy ? 1 : 0
            if n.max < 0 {
                let l = emit(I_LOOP, 0, 0, g)
                a[l] = op.count
                compile(k, level + 1)
                let e = emit(I_ITEREND, l, 0, 0)
                b[l] = op.count; b[e] = op.count
            } else if n.max > n.min {
                var loops: [Int] = [], ends: [Int] = []
                for _ in 0..<(n.max - n.min) {
                    let l = emit(I_LOOP, 0, 0, g)
                    a[l] = op.count
                    compile(k, level + 1)
                    let e = emit(I_ITEREND, 0, 0, 0)
                    a[e] = op.count
                    loops.append(l); ends.append(e)
                }
                for l in loops { b[l] = op.count }
                for e in ends { b[e] = op.count }
            }
        }
    }
}

let args = CommandLine.arguments
if args.count < 2 { exit(0) }
let url = URL(fileURLWithPath: args[1]).appendingPathComponent("cases.txt")
let data = (try? Data(contentsOf: url)) ?? Data()
let bytes = [UInt8](data)
var lines: [ArraySlice<UInt8>] = bytes.split(separator: 10, omittingEmptySubsequences: false)
if let last = lines.last, last.isEmpty { lines.removeLast() }
var out = ""
var memo: [UInt8] = []
for (li, line) in lines.enumerated() {
    let lineno = li + 1
    guard let tab = line.firstIndex(of: 9) else { continue }
    let pat = Array(line[line.startIndex..<tab])
    let s = Array(line[(tab + 1)...]).map { Int($0 & 127) }
    let n = s.count
    let ps = Parser(pat)
    let root = ps.regex()
    let cp = Comp()
    cp.compile(root, 0)
    cp.emit(I_MATCH, 0, 0, 0)
    let OP = cp.op, A = cp.a, B = cp.b, C = cp.c
    let classes = ps.classes
    let ng = ps.ngroups
    let D = cp.maxlevel + 1
    let msz = OP.count * (n + 1) * D
    if memo.count < msz { memo = [UInt8](repeating: 0, count: msz) }
    else { for i in 0..<msz { memo[i] = 0 } }
    let ncap = 2 * (ng + 1)
    var caps = [Int](repeating: -1, count: ncap)
    var found = false
    var ms = 0, me = 0
    var logs: [Int] = []
    var stk: [Int] = []
    var st = 0
    while st <= n && !found {
        for i in 0..<ncap { caps[i] = -1 }
        logs.removeAll(keepingCapacity: true)
        stk.removeAll(keepingCapacity: true)
        var pc = 0, pos = st, d = 0
        while true {
            let key = (pc * (n + 1) + pos) * D + d
            var ok = true
            if memo[key] != 0 { ok = false }
            else {
                memo[key] = 1
                switch OP[pc] {
                case I_CHAR: if pos < n && s[pos] == A[pc] { pos += 1; d = 0; pc += 1 } else { ok = false }
                case I_ANY: if pos < n { pos += 1; d = 0; pc += 1 } else { ok = false }
                case I_CLASS: if pos < n && classes[A[pc]][s[pos]] { pos += 1; d = 0; pc += 1 } else { ok = false }
                case I_BOL: if pos == 0 { pc += 1 } else { ok = false }
                case I_EOL: if pos == n { pc += 1 } else { ok = false }
                case I_SAVE: logs.append(A[pc]); logs.append(caps[A[pc]]); caps[A[pc]] = pos; pc += 1
                case I_JMP: pc = A[pc]
                case I_SPLIT: stk.append(B[pc]); stk.append(pos); stk.append(d); stk.append(logs.count); pc = A[pc]
                case I_LOOP:
                    if C[pc] != 0 { stk.append(B[pc]); stk.append(pos); stk.append(d); stk.append(logs.count); pc = A[pc]; d += 1 }
                    else { stk.append(A[pc]); stk.append(pos); stk.append(d + 1); stk.append(logs.count); pc = B[pc] }
                case I_ITEREND: if d >= 1 { d -= 1; pc = B[pc] } else { pc = A[pc] }
                default: found = true; ms = st; me = pos
                }
            }
            if found { break }
            if !ok {
                if stk.isEmpty { break }
                let lh = stk.removeLast(); d = stk.removeLast(); pos = stk.removeLast(); pc = stk.removeLast()
                while logs.count > lh { let old = logs.removeLast(); let slot = logs.removeLast(); caps[slot] = old }
            }
        }
        st += 1
    }
    if !found { out += "\(lineno): no match\n" }
    else {
        out += "\(lineno): \(ms)-\(me)"
        if ng >= 1 {
            for g in 1...ng {
                if caps[2 * g] < 0 || caps[2 * g + 1] < 0 { out += " -" } else { out += " \(caps[2 * g])-\(caps[2 * g + 1])" }
            }
        }
        out += "\n"
    }
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
