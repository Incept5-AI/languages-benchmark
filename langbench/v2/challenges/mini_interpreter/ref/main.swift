import Foundation

// ---------- values ----------
final class TList { var a: [Value]; init(_ a: [Value]) { self.a = a } }
final class FnDef { let params: [Int]; let body: [Stmt]; init(_ p: [Int], _ b: [Stmt]) { params = p; body = b } }
final class Closure { let def: FnDef; let env: Scope; init(_ d: FnDef, _ e: Scope) { def = d; env = e } }

enum Value {
    case nil_
    case int(Int)
    case bool(Bool)
    case str([UInt8])
    case list(TList)
    case fn(Closure)
    case builtin(Int)
}

struct TErr: Error { let line: Int; let msg: String }

final class Scope {
    let parent: Scope?
    var ids: [Int] = []
    var vals: [Value] = []
    init(_ p: Scope?) { parent = p }
    @inline(__always) func declare(_ id: Int, _ v: Value) { ids.append(id); vals.append(v) }
}

var nameIds: [String: Int] = [:]
var names: [String] = []
func intern(_ s: String) -> Int {
    if let i = nameIds[s] { return i }
    let i = names.count
    nameIds[s] = i
    names.append(s)
    return i
}

// ---------- lexer ----------
enum TKind { case int, str, id, kw, op, eof }
struct Tok { let kind: TKind; let text: String; var ival: Int = 0; var bytes: [UInt8] = []; let line: Int }
let KW: Set<String> = ["let", "fn", "if", "else", "while", "for", "in", "return", "break", "continue", "and", "or", "not", "true", "false", "nil"]

func lex(_ s: [UInt8]) -> [Tok] {
    var out: [Tok] = []
    var i = 0, line = 1
    let n = s.count
    func isDigit(_ c: UInt8) -> Bool { return c >= 48 && c <= 57 }
    func isAlpha(_ c: UInt8) -> Bool { return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c == 95 }
    while i < n {
        let c = s[i]
        if c == 10 { line += 1; i += 1; continue }
        if c == 32 || c == 9 || c == 13 { i += 1; continue }
        if c == 35 { while i < n && s[i] != 10 { i += 1 }; continue }
        if isDigit(c) {
            var j = i
            while j < n && isDigit(s[j]) { j += 1 }
            let text = String(decoding: s[i..<j], as: UTF8.self)
            var t = Tok(kind: .int, text: text, line: line)
            t.ival = Int(text)!
            out.append(t)
            i = j; continue
        }
        if isAlpha(c) {
            var j = i
            while j < n && (isAlpha(s[j]) || isDigit(s[j])) { j += 1 }
            let w = String(decoding: s[i..<j], as: UTF8.self)
            out.append(Tok(kind: KW.contains(w) ? .kw : .id, text: w, line: line))
            i = j; continue
        }
        if c == 34 {
            var sb: [UInt8] = []
            i += 1
            while i < n && s[i] != 34 {
                if s[i] == 92 && i + 1 < n {
                    let e = s[i + 1]
                    sb.append(e == 110 ? 10 : (e == 116 ? 9 : e))
                    i += 2
                } else { sb.append(s[i]); i += 1 }
            }
            i += 1
            var t = Tok(kind: .str, text: "", line: line)
            t.bytes = sb
            out.append(t)
            continue
        }
        if i + 1 < n && s[i + 1] == 61 && (c == 61 || c == 33 || c == 60 || c == 62) {
            out.append(Tok(kind: .op, text: String(decoding: s[i..<i + 2], as: UTF8.self), line: line))
            i += 2; continue
        }
        out.append(Tok(kind: .op, text: String(UnicodeScalar(c)), line: line))
        i += 1
    }
    out.append(Tok(kind: .eof, text: "", line: line))
    return out
}

// ---------- AST ----------
let NORMAL = 0, BRK = 1, CONT = 2, RET = 3
var retVal: Value = .nil_

class Expr { func eval(_ s: Scope) throws -> Value { fatalError() } }
class Stmt { func exec(_ s: Scope) throws -> Int { fatalError() } }

final class Lit: Expr { let v: Value; init(_ v: Value) { self.v = v }; override func eval(_ s: Scope) throws -> Value { return v } }
final class Var: Expr {
    let id: Int; let line: Int
    init(_ id: Int, _ line: Int) { self.id = id; self.line = line }
    override func eval(_ s: Scope) throws -> Value {
        var c: Scope? = s
        while let sc = c {
            var i = sc.ids.count - 1
            while i >= 0 { if sc.ids[i] == id { return sc.vals[i] }; i -= 1 }
            c = sc.parent
        }
        throw TErr(line: line, msg: "undefined variable " + names[id])
    }
}
final class ListLit: Expr {
    let items: [Expr]; init(_ it: [Expr]) { items = it }
    override func eval(_ s: Scope) throws -> Value {
        var a: [Value] = []; a.reserveCapacity(items.count)
        for e in items { a.append(try e.eval(s)) }
        return .list(TList(a))
    }
}
final class FnExpr: Expr { let d: FnDef; init(_ d: FnDef) { self.d = d }; override func eval(_ s: Scope) throws -> Value { return .fn(Closure(d, s)) } }
final class Call: Expr {
    let callee: Expr; let args: [Expr]; let line: Int
    init(_ c: Expr, _ a: [Expr], _ l: Int) { callee = c; args = a; line = l }
    override func eval(_ s: Scope) throws -> Value {
        let f = try callee.eval(s)
        var av: [Value] = []; av.reserveCapacity(args.count)
        for a in args { av.append(try a.eval(s)) }
        return try callValue(f, av, line)
    }
}
final class Index: Expr {
    let obj: Expr; let idx: Expr; let line: Int
    init(_ o: Expr, _ i: Expr, _ l: Int) { obj = o; idx = i; line = l }
    override func eval(_ s: Scope) throws -> Value {
        let o = try obj.eval(s), i = try idx.eval(s)
        if case .int(let k) = i {
            if case .list(let l) = o {
                if k < 0 || k >= l.a.count { throw TErr(line: line, msg: "index \(k) out of range for length \(l.a.count)") }
                return l.a[k]
            }
            if case .str(let b) = o {
                if k < 0 || k >= b.count { throw TErr(line: line, msg: "index \(k) out of range for length \(b.count)") }
                return .str([b[k]])
            }
        }
        throw TErr(line: line, msg: "cannot index \(typeName(o)) with \(typeName(i))")
    }
}
final class And: Expr { let l: Expr, r: Expr; init(_ a: Expr, _ b: Expr) { l = a; r = b }
    override func eval(_ s: Scope) throws -> Value { let a = try l.eval(s); if !truthy(a) { return a }; return try r.eval(s) } }
final class Or: Expr { let l: Expr, r: Expr; init(_ a: Expr, _ b: Expr) { l = a; r = b }
    override func eval(_ s: Scope) throws -> Value { let a = try l.eval(s); if truthy(a) { return a }; return try r.eval(s) } }
final class Not: Expr { let e: Expr; init(_ e: Expr) { self.e = e }
    override func eval(_ s: Scope) throws -> Value { return .bool(!truthy(try e.eval(s))) } }
final class Neg: Expr {
    let e: Expr; let line: Int; init(_ e: Expr, _ l: Int) { self.e = e; line = l }
    override func eval(_ s: Scope) throws -> Value {
        let v = try e.eval(s)
        guard case .int(let x) = v else { throw TErr(line: line, msg: "cannot apply - to \(typeName(v))") }
        if x == Int.min { throw TErr(line: line, msg: "integer overflow") }
        return .int(-x)
    }
}
let OPS = ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">="]
let OP_ADD = 0, OP_SUB = 1, OP_MUL = 2, OP_DIV = 3, OP_MOD = 4, OP_EQ = 5, OP_NE = 6, OP_LT = 7, OP_LE = 8, OP_GT = 9, OP_GE = 10

func bytesCompare(_ a: [UInt8], _ b: [UInt8]) -> Int {
    let n = min(a.count, b.count)
    var i = 0
    while i < n { if a[i] != b[i] { return a[i] < b[i] ? -1 : 1 }; i += 1 }
    return a.count < b.count ? -1 : (a.count > b.count ? 1 : 0)
}

final class Bin: Expr {
    let op: Int; let l: Expr; let r: Expr; let line: Int
    init(_ op: Int, _ l: Expr, _ r: Expr, _ line: Int) { self.op = op; self.l = l; self.r = r; self.line = line }
    func bad(_ a: Value, _ b: Value) -> TErr { return TErr(line: line, msg: "cannot apply \(OPS[op]) to \(typeName(a)) and \(typeName(b))") }
    override func eval(_ s: Scope) throws -> Value {
        let a = try l.eval(s), b = try r.eval(s)
        if case .int(let x) = a, case .int(let y) = b {
            switch op {
            case OP_ADD: let (z, o) = x.addingReportingOverflow(y); if o { throw TErr(line: line, msg: "integer overflow") }; return .int(z)
            case OP_SUB: let (z, o) = x.subtractingReportingOverflow(y); if o { throw TErr(line: line, msg: "integer overflow") }; return .int(z)
            case OP_MUL: let (z, o) = x.multipliedReportingOverflow(by: y); if o { throw TErr(line: line, msg: "integer overflow") }; return .int(z)
            case OP_DIV:
                if y == 0 { throw TErr(line: line, msg: "division by zero") }
                if x == Int.min && y == -1 { throw TErr(line: line, msg: "integer overflow") }
                return .int(x / y)
            case OP_MOD:
                if y == 0 { throw TErr(line: line, msg: "division by zero") }
                if y == -1 { return .int(0) }
                return .int(x % y)
            case OP_EQ: return .bool(x == y)
            case OP_NE: return .bool(x != y)
            case OP_LT: return .bool(x < y)
            case OP_LE: return .bool(x <= y)
            case OP_GT: return .bool(x > y)
            default: return .bool(x >= y)
            }
        }
        switch op {
        case OP_EQ: return .bool(valEq(a, b))
        case OP_NE: return .bool(!valEq(a, b))
        case OP_ADD:
            if case .str(let x) = a, case .str(let y) = b { return .str(x + y) }
            if case .list(let x) = a, case .list(let y) = b { return .list(TList(x.a + y.a)) }
            throw bad(a, b)
        case OP_LT, OP_LE, OP_GT, OP_GE:
            if case .str(let x) = a, case .str(let y) = b {
                let c = bytesCompare(x, y)
                switch op { case OP_LT: return .bool(c < 0); case OP_LE: return .bool(c <= 0); case OP_GT: return .bool(c > 0); default: return .bool(c >= 0) }
            }
            throw bad(a, b)
        default:
            throw bad(a, b)
        }
    }
}

@inline(__always) func runIn(_ body: [Stmt], _ sc: Scope) throws -> Int {
    for st in body { let r = try st.exec(sc); if r != NORMAL { return r } }
    return NORMAL
}
final class Block: Stmt { let body: [Stmt]; init(_ b: [Stmt]) { body = b }
    override func exec(_ s: Scope) throws -> Int { return try runIn(body, Scope(s)) } }
final class Let: Stmt { let id: Int; let e: Expr; init(_ id: Int, _ e: Expr) { self.id = id; self.e = e }
    override func exec(_ s: Scope) throws -> Int { let v = try e.eval(s); s.declare(id, v); return NORMAL } }
final class FnDecl: Stmt { let id: Int; let d: FnDef; init(_ id: Int, _ d: FnDef) { self.id = id; self.d = d }
    override func exec(_ s: Scope) throws -> Int { s.declare(id, .fn(Closure(d, s))); return NORMAL } }
final class If: Stmt {
    let c: Expr; let t: Stmt; let e: Stmt?
    init(_ c: Expr, _ t: Stmt, _ e: Stmt?) { self.c = c; self.t = t; self.e = e }
    override func exec(_ s: Scope) throws -> Int {
        if truthy(try c.eval(s)) { return try t.exec(s) }
        if let e = e { return try e.exec(s) }
        return NORMAL
    }
}
final class While: Stmt {
    let c: Expr; let b: Block; init(_ c: Expr, _ b: Block) { self.c = c; self.b = b }
    override func exec(_ s: Scope) throws -> Int {
        while truthy(try c.eval(s)) {
            let r = try b.exec(s)
            if r == BRK { break }
            if r == RET { return r }
        }
        return NORMAL
    }
}
final class For: Stmt {
    let id: Int; let e: Expr; let b: Block; let line: Int
    init(_ id: Int, _ e: Expr, _ b: Block, _ l: Int) { self.id = id; self.e = e; self.b = b; line = l }
    override func exec(_ s: Scope) throws -> Int {
        let v = try e.eval(s)
        guard case .list(let l) = v else { throw TErr(line: line, msg: "cannot iterate \(typeName(v))") }
        var i = 0
        while i < l.a.count {
            let sc = Scope(s)
            sc.declare(id, l.a[i])
            let r = try runIn(b.body, sc)
            if r == BRK { break }
            if r == RET { return r }
            i += 1
        }
        return NORMAL
    }
}
final class Return: Stmt { let e: Expr?; init(_ e: Expr?) { self.e = e }
    override func exec(_ s: Scope) throws -> Int { retVal = try e?.eval(s) ?? .nil_; return RET } }
final class Break: Stmt { override func exec(_ s: Scope) throws -> Int { return BRK } }
final class Continue: Stmt { override func exec(_ s: Scope) throws -> Int { return CONT } }
final class Assign: Stmt {
    let id: Int; let line: Int; let e: Expr
    init(_ id: Int, _ line: Int, _ e: Expr) { self.id = id; self.line = line; self.e = e }
    override func exec(_ s: Scope) throws -> Int {
        let v = try e.eval(s)
        var c: Scope? = s
        while let sc = c {
            var i = sc.ids.count - 1
            while i >= 0 { if sc.ids[i] == id { sc.vals[i] = v; return NORMAL }; i -= 1 }
            c = sc.parent
        }
        throw TErr(line: line, msg: "undefined variable " + names[id])
    }
}
final class IndexAssign: Stmt {
    let obj: Expr; let idx: Expr; let val: Expr; let line: Int
    init(_ o: Expr, _ i: Expr, _ v: Expr, _ l: Int) { obj = o; idx = i; val = v; line = l }
    override func exec(_ s: Scope) throws -> Int {
        let o = try obj.eval(s), i = try idx.eval(s), v = try val.eval(s)
        guard case .list(let l) = o else { throw TErr(line: line, msg: "cannot assign into \(typeName(o))") }
        guard case .int(let k) = i else { throw TErr(line: line, msg: "cannot index list with \(typeName(i))") }
        if k < 0 || k >= l.a.count { throw TErr(line: line, msg: "index \(k) out of range for length \(l.a.count)") }
        l.a[k] = v
        return NORMAL
    }
}
final class ExprStmt: Stmt { let e: Expr; init(_ e: Expr) { self.e = e }
    override func exec(_ s: Scope) throws -> Int { _ = try e.eval(s); return NORMAL } }

// ---------- parser ----------
var toks: [Tok] = []
var pos = 0
func peek() -> Tok { return toks[pos] }
@discardableResult func next() -> Tok { pos += 1; return toks[pos - 1] }
func isOp(_ t: String) -> Bool { let k = toks[pos]; return k.kind == .op && k.text == t }
func isKw(_ t: String) -> Bool { let k = toks[pos]; return k.kind == .kw && k.text == t }
func expect(_ t: String) { next() }

func parseBlockBody() -> [Stmt] {
    expect("{")
    var l: [Stmt] = []
    while !isOp("}") { l.append(parseStmt()) }
    expect("}")
    return l
}
func parseFnRest() -> FnDef {
    expect("(")
    var ps: [Int] = []
    if !isOp(")") {
        ps.append(intern(next().text))
        while isOp(",") { next(); ps.append(intern(next().text)) }
    }
    expect(")")
    return FnDef(ps, parseBlockBody())
}
func parseIf() -> Stmt {
    expect("if")
    let c = parseExpr()
    let t = Block(parseBlockBody())
    var e: Stmt? = nil
    if isKw("else") { next(); e = isKw("if") ? parseIf() : Block(parseBlockBody()) }
    return If(c, t, e)
}
func parseStmt() -> Stmt {
    let t = peek()
    if t.kind == .kw {
        switch t.text {
        case "let": next(); let id = next(); expect("="); let e = parseExpr(); expect(";"); return Let(intern(id.text), e)
        case "fn":
            if toks[pos + 1].kind == .id { next(); let id = next(); return FnDecl(intern(id.text), parseFnRest()) }
        case "if": return parseIf()
        case "while": next(); let c = parseExpr(); return While(c, Block(parseBlockBody()))
        case "for": let f = next(); let id = next(); expect("in"); let e = parseExpr(); return For(intern(id.text), e, Block(parseBlockBody()), f.line)
        case "return": next(); var e: Expr? = nil; if !isOp(";") { e = parseExpr() }; expect(";"); return Return(e)
        case "break": next(); expect(";"); return Break()
        case "continue": next(); expect(";"); return Continue()
        default: break
        }
    }
    let e = parseExpr()
    if isOp("=") {
        next()
        let v = parseExpr(); expect(";")
        if let vr = e as? Var { return Assign(vr.id, vr.line, v) }
        let ix = e as! Index
        return IndexAssign(ix.obj, ix.idx, v, ix.line)
    }
    expect(";")
    return ExprStmt(e)
}
func parseExpr() -> Expr { var l = parseAnd(); while isKw("or") { next(); l = Or(l, parseAnd()) }; return l }
func parseAnd() -> Expr { var l = parseNot(); while isKw("and") { next(); l = And(l, parseNot()) }; return l }
func parseNot() -> Expr { if isKw("not") { next(); return Not(parseNot()) }; return parseCmp() }
func opIndex(_ t: Tok) -> Int { if t.kind != .op { return -1 }; return OPS.firstIndex(of: t.text) ?? -1 }
func parseCmp() -> Expr {
    var l = parseAdd()
    while true { let t = peek(); let o = opIndex(t); if o < OP_EQ { break }; next(); l = Bin(o, l, parseAdd(), t.line) }
    return l
}
func parseAdd() -> Expr {
    var l = parseMul()
    while true { let t = peek(); let o = opIndex(t); if o != OP_ADD && o != OP_SUB { break }; next(); l = Bin(o, l, parseMul(), t.line) }
    return l
}
func parseMul() -> Expr {
    var l = parseUnary()
    while true { let t = peek(); let o = opIndex(t); if o != OP_MUL && o != OP_DIV && o != OP_MOD { break }; next(); l = Bin(o, l, parseUnary(), t.line) }
    return l
}
func parseUnary() -> Expr { if isOp("-") { let t = next(); return Neg(parseUnary(), t.line) }; return parsePostfix() }
func parseList(_ close: String) -> [Expr] {
    var a: [Expr] = []
    if !isOp(close) { a.append(parseExpr()); while isOp(",") { next(); a.append(parseExpr()) } }
    expect(close)
    return a
}
func parsePostfix() -> Expr {
    var e = parsePrimary()
    while true {
        if isOp("(") { let t = next(); e = Call(e, parseList(")"), t.line) }
        else if isOp("[") { let t = next(); let i = parseExpr(); expect("]"); e = Index(e, i, t.line) }
        else { break }
    }
    return e
}
func parsePrimary() -> Expr {
    let t = next()
    switch t.kind {
    case .int: return Lit(.int(t.ival))
    case .str: return Lit(.str(t.bytes))
    case .id: return Var(intern(t.text), t.line)
    case .kw:
        if t.text == "true" { return Lit(.bool(true)) }
        if t.text == "false" { return Lit(.bool(false)) }
        if t.text == "nil" { return Lit(.nil_) }
        if t.text == "fn" { return FnExpr(parseFnRest()) }
    case .op:
        if t.text == "(" { let e = parseExpr(); expect(")"); return e }
        if t.text == "[" { return ListLit(parseList("]")) }
    default: break
    }
    fatalError("parse error line \(t.line) at \(t.text)")
}

// ---------- runtime helpers ----------
@inline(__always) func truthy(_ v: Value) -> Bool {
    switch v {
    case .nil_: return false
    case .bool(let b): return b
    case .int(let i): return i != 0
    case .str(let s): return !s.isEmpty
    case .list(let l): return !l.a.isEmpty
    default: return true
    }
}
func typeName(_ v: Value) -> String {
    switch v {
    case .nil_: return "nil"
    case .int: return "int"
    case .bool: return "bool"
    case .str: return "string"
    case .list: return "list"
    default: return "function"
    }
}
func valEq(_ a: Value, _ b: Value) -> Bool {
    switch (a, b) {
    case (.nil_, .nil_): return true
    case (.int(let x), .int(let y)): return x == y
    case (.bool(let x), .bool(let y)): return x == y
    case (.str(let x), .str(let y)): return x == y
    case (.list(let x), .list(let y)):
        if x === y { return true }
        if x.a.count != y.a.count { return false }
        for i in 0..<x.a.count { if !valEq(x.a[i], y.a[i]) { return false } }
        return true
    case (.fn(let x), .fn(let y)): return x === y
    case (.builtin(let x), .builtin(let y)): return x == y
    default: return false
    }
}
func display(_ v: Value, _ out: inout [UInt8], _ inList: Bool, _ stack: inout [ObjectIdentifier]) {
    switch v {
    case .str(let s):
        if !inList { out.append(contentsOf: s); return }
        out.append(34)
        for c in s {
            switch c {
            case 92: out.append(92); out.append(92)
            case 34: out.append(92); out.append(34)
            case 10: out.append(92); out.append(110)
            case 9: out.append(92); out.append(116)
            default: out.append(c)
            }
        }
        out.append(34)
    case .list(let l):
        let oid = ObjectIdentifier(l)
        if stack.contains(oid) { out.append(contentsOf: Array("[...]".utf8)); return }
        stack.append(oid)
        out.append(91)
        for i in 0..<l.a.count {
            if i > 0 { out.append(44); out.append(32) }
            display(l.a[i], &out, true, &stack)
        }
        out.append(93)
        stack.removeLast()
    case .int(let i): out.append(contentsOf: Array(String(i).utf8))
    case .bool(let b): out.append(contentsOf: Array((b ? "true" : "false").utf8))
    case .nil_: out.append(contentsOf: Array("nil".utf8))
    default: out.append(contentsOf: Array("<fn>".utf8))
    }
}

var outBuf: [UInt8] = []
func flushOut() {
    if outBuf.isEmpty { return }
    outBuf.withUnsafeBufferPointer { p in _ = fwrite(p.baseAddress, 1, p.count, stdout) }
    outBuf.removeAll(keepingCapacity: true)
}

let BUILTINS = ["print", "str", "type", "len", "push", "pop", "range", "slice"]
let ARITY = [-1, 1, 1, 1, 2, 1, 2, 3]

func callValue(_ f: Value, _ av: [Value], _ line: Int) throws -> Value {
    switch f {
    case .fn(let c):
        let ps = c.def.params
        if ps.count != av.count { throw TErr(line: line, msg: "expected \(ps.count) arguments, got \(av.count)") }
        let sc = Scope(c.env)
        sc.ids = ps
        sc.vals = av
        for st in c.def.body {
            if try st.exec(sc) == RET { let v = retVal; retVal = .nil_; return v }
        }
        return .nil_
    case .builtin(let id):
        let ar = ARITY[id]
        if ar >= 0 && av.count != ar { throw TErr(line: line, msg: "expected \(ar) arguments, got \(av.count)") }
        switch id {
        case 0:
            var stack: [ObjectIdentifier] = []
            for i in 0..<av.count { if i > 0 { outBuf.append(32) }; display(av[i], &outBuf, false, &stack) }
            outBuf.append(10)
            if outBuf.count > 1 << 16 { flushOut() }
            return .nil_
        case 1:
            var o: [UInt8] = []; var stack: [ObjectIdentifier] = []
            display(av[0], &o, false, &stack)
            return .str(o)
        case 2: return .str(Array(typeName(av[0]).utf8))
        case 3:
            if case .str(let s) = av[0] { return .int(s.count) }
            if case .list(let l) = av[0] { return .int(l.a.count) }
        case 4:
            if case .list(let l) = av[0] { l.a.append(av[1]); return .nil_ }
        case 5:
            if case .list(let l) = av[0], !l.a.isEmpty { return l.a.removeLast() }
        case 6:
            if case .int(let a) = av[0], case .int(let b) = av[1] {
                var r: [Value] = []
                if b > a { r.reserveCapacity(b - a); var k = a; while k < b { r.append(.int(k)); k += 1 } }
                return .list(TList(r))
            }
        default:
            if case .int(let a) = av[1], case .int(let b) = av[2] {
                if case .str(let s) = av[0], 0 <= a && a <= b && b <= s.count { return .str(Array(s[a..<b])) }
                if case .list(let l) = av[0], 0 <= a && a <= b && b <= l.a.count { return .list(TList(Array(l.a[a..<b]))) }
            }
        }
        throw TErr(line: line, msg: "bad argument to \(BUILTINS[id])")
    default:
        throw TErr(line: line, msg: "cannot call \(typeName(f))")
    }
}

func runMain() {
    let dir = CommandLine.arguments[1]
    let url = URL(fileURLWithPath: dir).appendingPathComponent("program.tern")
    let data = (try? Data(contentsOf: url)) ?? Data()
    toks = lex([UInt8](data)); pos = 0
    var prog: [Stmt] = []
    while peek().kind != .eof { prog.append(parseStmt()) }
    let builtins = Scope(nil)
    for (i, b) in BUILTINS.enumerated() { builtins.declare(intern(b), .builtin(i)) }
    let global = Scope(builtins)
    do {
        for st in prog { _ = try st.exec(global) }
    } catch let e as TErr {
        outBuf.append(contentsOf: Array("error at line \(e.line): \(e.msg)\n".utf8))
    } catch {}
    flushOut()
    fflush(stdout)
    exit(0)
}

let th = Thread { runMain() }
th.stackSize = 1 << 29
th.start()
while true { sleep(1000) }
