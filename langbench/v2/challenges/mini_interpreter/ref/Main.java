import java.util.*;
import java.io.*;
import java.nio.file.*;

public class Main {
    // ---------- values ----------
    static final class Nil { }
    static final Object NIL = new Nil();
    static final class TList { ArrayList<Object> a; TList(ArrayList<Object> a) { this.a = a; } }
    static abstract class Fn { }
    static final class Closure extends Fn { FnDef def; Scope env; Closure(FnDef d, Scope e) { def = d; env = e; } }
    static final class Builtin extends Fn { String name; int id; Builtin(String n, int i) { name = n; id = i; } }

    static final class TErr extends RuntimeException {
        int line; String msg;
        TErr(int l, String m) { super(null, null, false, false); line = l; msg = m; }
    }

    // ---------- scopes ----------
    static final class Scope {
        Scope parent; int[] ids; Object[] vals; int n;
        Scope(Scope p, int cap) { parent = p; ids = new int[cap < 2 ? 2 : cap]; vals = new Object[ids.length]; }
        void declare(int id, Object v) {
            if (n == ids.length) { ids = Arrays.copyOf(ids, n * 2); vals = Arrays.copyOf(vals, n * 2); }
            ids[n] = id; vals[n] = v; n++;
        }
    }

    // ---------- names ----------
    static HashMap<String, Integer> nameIds = new HashMap<>();
    static int intern(String s) { Integer i = nameIds.get(s); if (i == null) { i = nameIds.size(); nameIds.put(s, i); } return i; }

    // ---------- lexer ----------
    static final int T_INT = 0, T_STR = 1, T_ID = 2, T_KW = 3, T_OP = 4, T_EOF = 5;
    static final class Tok { int kind; String text; long ival; int line; Tok(int k, String t, int l) { kind = k; text = t; line = l; } }
    static final Set<String> KW = new HashSet<>(Arrays.asList("let", "fn", "if", "else", "while", "for", "in", "return", "break", "continue", "and", "or", "not", "true", "false", "nil"));

    static ArrayList<Tok> lex(String s) {
        ArrayList<Tok> out = new ArrayList<>();
        int i = 0, line = 1, n = s.length();
        while (i < n) {
            char c = s.charAt(i);
            if (c == '\n') { line++; i++; continue; }
            if (c == ' ' || c == '\t' || c == '\r') { i++; continue; }
            if (c == '#') { while (i < n && s.charAt(i) != '\n') i++; continue; }
            if (c >= '0' && c <= '9') {
                int j = i; while (j < n && Character.isDigit(s.charAt(j))) j++;
                Tok t = new Tok(T_INT, s.substring(i, j), line); t.ival = Long.parseLong(t.text); out.add(t); i = j; continue;
            }
            if (Character.isLetter(c) || c == '_') {
                int j = i; while (j < n && (Character.isLetterOrDigit(s.charAt(j)) || s.charAt(j) == '_')) j++;
                String w = s.substring(i, j);
                out.add(new Tok(KW.contains(w) ? T_KW : T_ID, w, line)); i = j; continue;
            }
            if (c == '"') {
                StringBuilder sb = new StringBuilder(); i++;
                while (i < n && s.charAt(i) != '"') {
                    char d = s.charAt(i);
                    if (d == '\\' && i + 1 < n) {
                        char e = s.charAt(i + 1);
                        if (e == 'n') sb.append('\n'); else if (e == 't') sb.append('\t'); else sb.append(e);
                        i += 2;
                    } else { sb.append(d); i++; }
                }
                i++;
                out.add(new Tok(T_STR, sb.toString(), line)); continue;
            }
            if (i + 1 < n) {
                String two = s.substring(i, i + 2);
                if (two.equals("==") || two.equals("!=") || two.equals("<=") || two.equals(">=")) { out.add(new Tok(T_OP, two, line)); i += 2; continue; }
            }
            out.add(new Tok(T_OP, String.valueOf(c), line)); i++;
        }
        out.add(new Tok(T_EOF, "", line));
        return out;
    }

    // ---------- AST ----------
    static abstract class Expr { abstract Object eval(Scope s); }
    static abstract class Stmt { abstract int exec(Scope s); }
    static final int NORMAL = 0, BRK = 1, CONT = 2, RET = 3;
    static Object retVal = NIL;

    static final class FnDef { int[] params; Stmt[] body; FnDef(int[] p, Stmt[] b) { params = p; body = b; } }

    static final class Lit extends Expr { Object v; Lit(Object v) { this.v = v; } Object eval(Scope s) { return v; } }
    static final class Var extends Expr {
        int id; String name; int line;
        Var(int id, String name, int line) { this.id = id; this.name = name; this.line = line; }
        Object eval(Scope s) {
            for (Scope c = s; c != null; c = c.parent) {
                int[] ids = c.ids;
                for (int i = c.n - 1; i >= 0; i--) if (ids[i] == id) return c.vals[i];
            }
            throw new TErr(line, "undefined variable " + name);
        }
    }
    static final class ListLit extends Expr {
        Expr[] items; ListLit(Expr[] it) { items = it; }
        Object eval(Scope s) { ArrayList<Object> a = new ArrayList<>(items.length); for (Expr e : items) a.add(e.eval(s)); return new TList(a); }
    }
    static final class FnExpr extends Expr { FnDef d; FnExpr(FnDef d) { this.d = d; } Object eval(Scope s) { return new Closure(d, s); } }
    static final class Call extends Expr {
        Expr callee; Expr[] args; int line;
        Call(Expr c, Expr[] a, int l) { callee = c; args = a; line = l; }
        Object eval(Scope s) {
            Object f = callee.eval(s);
            Object[] av = new Object[args.length];
            for (int i = 0; i < args.length; i++) av[i] = args[i].eval(s);
            return call(f, av, line);
        }
    }
    static final class Index extends Expr {
        Expr obj, idx; int line;
        Index(Expr o, Expr i, int l) { obj = o; idx = i; line = l; }
        Object eval(Scope s) {
            Object o = obj.eval(s), i = idx.eval(s);
            if (i instanceof Long) {
                long k = (Long) i;
                if (o instanceof TList) {
                    ArrayList<Object> a = ((TList) o).a;
                    if (k < 0 || k >= a.size()) throw new TErr(line, "index " + k + " out of range for length " + a.size());
                    return a.get((int) k);
                }
                if (o instanceof String) {
                    String str = (String) o;
                    if (k < 0 || k >= str.length()) throw new TErr(line, "index " + k + " out of range for length " + str.length());
                    return String.valueOf(str.charAt((int) k));
                }
            }
            throw new TErr(line, "cannot index " + typeName(o) + " with " + typeName(i));
        }
    }
    static final class And extends Expr { Expr l, r; And(Expr a, Expr b) { l = a; r = b; } Object eval(Scope s) { Object a = l.eval(s); if (!truthy(a)) return a; return r.eval(s); } }
    static final class Or extends Expr { Expr l, r; Or(Expr a, Expr b) { l = a; r = b; } Object eval(Scope s) { Object a = l.eval(s); if (truthy(a)) return a; return r.eval(s); } }
    static final class Not extends Expr { Expr e; Not(Expr e) { this.e = e; } Object eval(Scope s) { return !truthy(e.eval(s)); } }
    static final class Neg extends Expr {
        Expr e; int line; Neg(Expr e, int l) { this.e = e; line = l; }
        Object eval(Scope s) {
            Object v = e.eval(s);
            if (!(v instanceof Long)) throw new TErr(line, "cannot apply - to " + typeName(v));
            long x = (Long) v;
            if (x == Long.MIN_VALUE) throw new TErr(line, "integer overflow");
            return -x;
        }
    }
    static final int OP_ADD = 0, OP_SUB = 1, OP_MUL = 2, OP_DIV = 3, OP_MOD = 4, OP_EQ = 5, OP_NE = 6, OP_LT = 7, OP_LE = 8, OP_GT = 9, OP_GE = 10;
    static final String[] OPS = {"+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">="};
    static final class Bin extends Expr {
        int op; Expr l, r; int line;
        Bin(int op, Expr l, Expr r, int line) { this.op = op; this.l = l; this.r = r; this.line = line; }
        TErr bad(Object a, Object b) { return new TErr(line, "cannot apply " + OPS[op] + " to " + typeName(a) + " and " + typeName(b)); }
        Object eval(Scope s) {
            Object a = l.eval(s), b = r.eval(s);
            switch (op) {
                case OP_EQ: return valEq(a, b);
                case OP_NE: return !valEq(a, b);
                case OP_ADD:
                    if (a instanceof Long && b instanceof Long) {
                        long x = (Long) a, y = (Long) b, z = x + y;
                        if (((x ^ z) & (y ^ z)) < 0) throw new TErr(line, "integer overflow");
                        return z;
                    }
                    if (a instanceof String && b instanceof String) return (String) a + (String) b;
                    if (a instanceof TList && b instanceof TList) {
                        ArrayList<Object> n = new ArrayList<>(((TList) a).a); n.addAll(((TList) b).a); return new TList(n);
                    }
                    throw bad(a, b);
                case OP_LT: case OP_LE: case OP_GT: case OP_GE: {
                    int c;
                    if (a instanceof Long && b instanceof Long) c = Long.compare((Long) a, (Long) b);
                    else if (a instanceof String && b instanceof String) c = ((String) a).compareTo((String) b);
                    else throw bad(a, b);
                    switch (op) { case OP_LT: return c < 0; case OP_LE: return c <= 0; case OP_GT: return c > 0; default: return c >= 0; }
                }
                default: {
                    if (!(a instanceof Long && b instanceof Long)) throw bad(a, b);
                    long x = (Long) a, y = (Long) b;
                    switch (op) {
                        case OP_SUB: { long z = x - y; if (((x ^ y) & (x ^ z)) < 0) throw new TErr(line, "integer overflow"); return z; }
                        case OP_MUL: try { return Math.multiplyExact(x, y); } catch (ArithmeticException e) { throw new TErr(line, "integer overflow"); }
                        case OP_DIV: if (y == 0) throw new TErr(line, "division by zero");
                            if (x == Long.MIN_VALUE && y == -1) throw new TErr(line, "integer overflow");
                            return x / y;
                        default: if (y == 0) throw new TErr(line, "division by zero");
                            if (y == -1) return 0L;
                            return x % y;
                    }
                }
            }
        }
    }

    static final class Block extends Stmt {
        Stmt[] body; Block(Stmt[] b) { body = b; }
        int exec(Scope s) { return runIn(body, new Scope(s, 4)); }
    }
    static int runIn(Stmt[] body, Scope sc) {
        for (Stmt st : body) { int r = st.exec(sc); if (r != NORMAL) return r; }
        return NORMAL;
    }
    static final class Let extends Stmt { int id; Expr e; Let(int id, Expr e) { this.id = id; this.e = e; } int exec(Scope s) { Object v = e.eval(s); s.declare(id, v); return NORMAL; } }
    static final class FnDecl extends Stmt { int id; FnDef d; FnDecl(int id, FnDef d) { this.id = id; this.d = d; } int exec(Scope s) { s.declare(id, new Closure(d, s)); return NORMAL; } }
    static final class If extends Stmt {
        Expr c; Stmt t, e; If(Expr c, Stmt t, Stmt e) { this.c = c; this.t = t; this.e = e; }
        int exec(Scope s) { if (truthy(c.eval(s))) return t.exec(s); if (e != null) return e.exec(s); return NORMAL; }
    }
    static final class While extends Stmt {
        Expr c; Block b; While(Expr c, Block b) { this.c = c; this.b = b; }
        int exec(Scope s) {
            while (truthy(c.eval(s))) {
                int r = b.exec(s);
                if (r == BRK) break;
                if (r == RET) return r;
            }
            return NORMAL;
        }
    }
    static final class For extends Stmt {
        int id; Expr e; Block b; int line; For(int id, Expr e, Block b, int l) { this.id = id; this.e = e; this.b = b; line = l; }
        int exec(Scope s) {
            Object v = e.eval(s);
            if (!(v instanceof TList)) throw new TErr(line, "cannot iterate " + typeName(v));
            ArrayList<Object> a = ((TList) v).a;
            for (int i = 0; i < a.size(); i++) {
                Scope sc = new Scope(s, 4);
                sc.declare(id, a.get(i));
                int r = runIn(b.body, sc);
                if (r == BRK) break;
                if (r == RET) return r;
            }
            return NORMAL;
        }
    }
    static final class Return extends Stmt { Expr e; Return(Expr e) { this.e = e; } int exec(Scope s) { retVal = e == null ? NIL : e.eval(s); return RET; } }
    static final class Break extends Stmt { int exec(Scope s) { return BRK; } }
    static final class Continue extends Stmt { int exec(Scope s) { return CONT; } }
    static final class Assign extends Stmt {
        int id; String name; int line; Expr e;
        Assign(int id, String name, int line, Expr e) { this.id = id; this.name = name; this.line = line; this.e = e; }
        int exec(Scope s) {
            Object v = e.eval(s);
            for (Scope c = s; c != null; c = c.parent) {
                int[] ids = c.ids;
                for (int i = c.n - 1; i >= 0; i--) if (ids[i] == id) { c.vals[i] = v; return NORMAL; }
            }
            throw new TErr(line, "undefined variable " + name);
        }
    }
    static final class IndexAssign extends Stmt {
        Expr obj, idx, val; int line;
        IndexAssign(Expr o, Expr i, Expr v, int l) { obj = o; idx = i; val = v; line = l; }
        int exec(Scope s) {
            Object o = obj.eval(s), i = idx.eval(s), v = val.eval(s);
            if (!(o instanceof TList)) throw new TErr(line, "cannot assign into " + typeName(o));
            if (!(i instanceof Long)) throw new TErr(line, "cannot index list with " + typeName(i));
            ArrayList<Object> a = ((TList) o).a; long k = (Long) i;
            if (k < 0 || k >= a.size()) throw new TErr(line, "index " + k + " out of range for length " + a.size());
            a.set((int) k, v);
            return NORMAL;
        }
    }
    static final class ExprStmt extends Stmt { Expr e; ExprStmt(Expr e) { this.e = e; } int exec(Scope s) { e.eval(s); return NORMAL; } }

    // ---------- parser ----------
    static ArrayList<Tok> toks; static int pos;
    static Tok peek() { return toks.get(pos); }
    static Tok next() { return toks.get(pos++); }
    static boolean isOp(String t) { Tok k = peek(); return k.kind == T_OP && k.text.equals(t); }
    static boolean isKw(String t) { Tok k = peek(); return k.kind == T_KW && k.text.equals(t); }
    static Tok expect(String t) { Tok k = next(); if (!k.text.equals(t)) throw new RuntimeException("parse error line " + k.line + ": expected " + t + " got " + k.text); return k; }

    static Stmt[] parseBlockBody() {
        expect("{");
        ArrayList<Stmt> l = new ArrayList<>();
        while (!isOp("}")) l.add(parseStmt());
        expect("}");
        return l.toArray(new Stmt[0]);
    }
    static FnDef parseFnRest() {
        expect("(");
        ArrayList<Integer> ps = new ArrayList<>();
        if (!isOp(")")) {
            ps.add(intern(next().text));
            while (isOp(",")) { next(); ps.add(intern(next().text)); }
        }
        expect(")");
        int[] p = new int[ps.size()]; for (int i = 0; i < p.length; i++) p[i] = ps.get(i);
        return new FnDef(p, parseBlockBody());
    }
    static Stmt parseIf() {
        expect("if");
        Expr c = parseExpr();
        Block t = new Block(parseBlockBody());
        Stmt e = null;
        if (isKw("else")) { next(); if (isKw("if")) e = parseIf(); else e = new Block(parseBlockBody()); }
        return new If(c, t, e);
    }
    static Stmt parseStmt() {
        Tok t = peek();
        if (t.kind == T_KW) {
            switch (t.text) {
                case "let": { next(); Tok id = next(); expect("="); Expr e = parseExpr(); expect(";"); return new Let(intern(id.text), e); }
                case "fn":
                    if (toks.get(pos + 1).kind == T_ID) { next(); Tok id = next(); return new FnDecl(intern(id.text), parseFnRest()); }
                    break;
                case "if": return parseIf();
                case "while": { next(); Expr c = parseExpr(); return new While(c, new Block(parseBlockBody())); }
                case "for": { Tok f = next(); Tok id = next(); expect("in"); Expr e = parseExpr(); return new For(intern(id.text), e, new Block(parseBlockBody()), f.line); }
                case "return": { next(); Expr e = null; if (!isOp(";")) e = parseExpr(); expect(";"); return new Return(e); }
                case "break": next(); expect(";"); return new Break();
                case "continue": next(); expect(";"); return new Continue();
            }
        }
        Expr e = parseExpr();
        if (isOp("=")) {
            next();
            Expr v = parseExpr(); expect(";");
            if (e instanceof Var) { Var vr = (Var) e; return new Assign(vr.id, vr.name, vr.line, v); }
            Index ix = (Index) e;
            return new IndexAssign(ix.obj, ix.idx, v, ix.line);
        }
        expect(";");
        return new ExprStmt(e);
    }
    static Expr parseExpr() { return parseOr(); }
    static Expr parseOr() { Expr l = parseAnd(); while (isKw("or")) { next(); l = new Or(l, parseAnd()); } return l; }
    static Expr parseAnd() { Expr l = parseNot(); while (isKw("and")) { next(); l = new And(l, parseNot()); } return l; }
    static Expr parseNot() { if (isKw("not")) { next(); return new Not(parseNot()); } return parseCmp(); }
    static int opIndex(Tok t) { if (t.kind != T_OP) return -1; for (int i = 0; i < OPS.length; i++) if (OPS[i].equals(t.text)) return i; return -1; }
    static Expr parseCmp() {
        Expr l = parseAdd();
        while (true) { Tok t = peek(); int o = opIndex(t); if (o < OP_EQ) break; next(); l = new Bin(o, l, parseAdd(), t.line); }
        return l;
    }
    static Expr parseAdd() {
        Expr l = parseMul();
        while (true) { Tok t = peek(); int o = opIndex(t); if (o != OP_ADD && o != OP_SUB) break; next(); l = new Bin(o, l, parseMul(), t.line); }
        return l;
    }
    static Expr parseMul() {
        Expr l = parseUnary();
        while (true) { Tok t = peek(); int o = opIndex(t); if (o != OP_MUL && o != OP_DIV && o != OP_MOD) break; next(); l = new Bin(o, l, parseUnary(), t.line); }
        return l;
    }
    static Expr parseUnary() { if (isOp("-")) { Tok t = next(); return new Neg(parseUnary(), t.line); } return parsePostfix(); }
    static Expr parsePostfix() {
        Expr e = parsePrimary();
        while (true) {
            if (isOp("(")) {
                Tok t = next();
                ArrayList<Expr> a = new ArrayList<>();
                if (!isOp(")")) { a.add(parseExpr()); while (isOp(",")) { next(); a.add(parseExpr()); } }
                expect(")");
                e = new Call(e, a.toArray(new Expr[0]), t.line);
            } else if (isOp("[")) {
                Tok t = next(); Expr i = parseExpr(); expect("]");
                e = new Index(e, i, t.line);
            } else break;
        }
        return e;
    }
    static Expr parsePrimary() {
        Tok t = next();
        switch (t.kind) {
            case T_INT: return new Lit(t.ival);
            case T_STR: return new Lit(t.text);
            case T_ID: return new Var(intern(t.text), t.text, t.line);
            case T_KW:
                switch (t.text) {
                    case "true": return new Lit(Boolean.TRUE);
                    case "false": return new Lit(Boolean.FALSE);
                    case "nil": return new Lit(NIL);
                    case "fn": return new FnExpr(parseFnRest());
                }
                break;
            case T_OP:
                if (t.text.equals("(")) { Expr e = parseExpr(); expect(")"); return e; }
                if (t.text.equals("[")) {
                    ArrayList<Expr> a = new ArrayList<>();
                    if (!isOp("]")) { a.add(parseExpr()); while (isOp(",")) { next(); a.add(parseExpr()); } }
                    expect("]");
                    return new ListLit(a.toArray(new Expr[0]));
                }
        }
        throw new RuntimeException("parse error line " + t.line + " at " + t.text);
    }

    // ---------- runtime helpers ----------
    static boolean truthy(Object v) {
        if (v == NIL) return false;
        if (v instanceof Boolean) return (Boolean) v;
        if (v instanceof Long) return (Long) v != 0;
        if (v instanceof String) return !((String) v).isEmpty();
        if (v instanceof TList) return !((TList) v).a.isEmpty();
        return true;
    }
    static String typeName(Object v) {
        if (v == NIL) return "nil";
        if (v instanceof Long) return "int";
        if (v instanceof Boolean) return "bool";
        if (v instanceof String) return "string";
        if (v instanceof TList) return "list";
        return "function";
    }
    static boolean valEq(Object a, Object b) {
        if (a == b) return true;
        if (a instanceof Long && b instanceof Long) return ((Long) a).longValue() == ((Long) b).longValue();
        if (a instanceof Boolean && b instanceof Boolean) return a.equals(b);
        if (a instanceof String && b instanceof String) return a.equals(b);
        if (a instanceof TList && b instanceof TList) {
            ArrayList<Object> x = ((TList) a).a, y = ((TList) b).a;
            if (x.size() != y.size()) return false;
            for (int i = 0; i < x.size(); i++) if (!valEq(x.get(i), y.get(i))) return false;
            return true;
        }
        return false;
    }
    static void display(Object v, StringBuilder sb, boolean inList, ArrayList<TList> stack) {
        if (v instanceof String) {
            String s = (String) v;
            if (!inList) { sb.append(s); return; }
            sb.append('"');
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '\\': sb.append("\\\\"); break;
                    case '"': sb.append("\\\""); break;
                    case '\n': sb.append("\\n"); break;
                    case '\t': sb.append("\\t"); break;
                    default: sb.append(c);
                }
            }
            sb.append('"');
        } else if (v instanceof TList) {
            TList l = (TList) v;
            for (TList x : stack) if (x == l) { sb.append("[...]"); return; }
            stack.add(l);
            sb.append('[');
            for (int i = 0; i < l.a.size(); i++) { if (i > 0) sb.append(", "); display(l.a.get(i), sb, true, stack); }
            sb.append(']');
            stack.remove(stack.size() - 1);
        } else if (v instanceof Long) sb.append((long) (Long) v);
        else if (v instanceof Boolean) sb.append((Boolean) v ? "true" : "false");
        else if (v == NIL) sb.append("nil");
        else sb.append("<fn>");
    }
    static String disp(Object v) { StringBuilder sb = new StringBuilder(); display(v, sb, false, new ArrayList<>()); return sb.toString(); }

    static StringBuilder out = new StringBuilder();
    static PrintStream stdout;
    static void flushOut() { if (out.length() > 0) { stdout.print(out); out.setLength(0); } }

    static final String[] BUILTINS = {"print", "str", "type", "len", "push", "pop", "range", "slice"};
    static final int[] ARITY = {-1, 1, 1, 1, 2, 1, 2, 3};

    static Object call(Object f, Object[] av, int line) {
        if (f instanceof Closure) {
            Closure c = (Closure) f;
            int[] ps = c.def.params;
            if (ps.length != av.length) throw new TErr(line, "expected " + ps.length + " arguments, got " + av.length);
            Scope sc = new Scope(c.env, ps.length + 2);
            for (int i = 0; i < ps.length; i++) sc.declare(ps[i], av[i]);
            for (Stmt st : c.def.body) {
                int r = st.exec(sc);
                if (r == RET) { Object v = retVal; retVal = NIL; return v; }
            }
            return NIL;
        }
        if (f instanceof Builtin) {
            Builtin b = (Builtin) f;
            int ar = ARITY[b.id];
            if (ar >= 0 && av.length != ar) throw new TErr(line, "expected " + ar + " arguments, got " + av.length);
            TErr bad = null;
            switch (b.id) {
                case 0: {
                    for (int i = 0; i < av.length; i++) { if (i > 0) out.append(' '); display(av[i], out, false, new ArrayList<>()); }
                    out.append('\n');
                    if (out.length() > 1 << 16) flushOut();
                    return NIL;
                }
                case 1: return disp(av[0]);
                case 2: return typeName(av[0]);
                case 3:
                    if (av[0] instanceof String) return (long) ((String) av[0]).length();
                    if (av[0] instanceof TList) return (long) ((TList) av[0]).a.size();
                    break;
                case 4:
                    if (av[0] instanceof TList) { ((TList) av[0]).a.add(av[1]); return NIL; }
                    break;
                case 5:
                    if (av[0] instanceof TList && !((TList) av[0]).a.isEmpty()) { ArrayList<Object> a = ((TList) av[0]).a; return a.remove(a.size() - 1); }
                    break;
                case 6:
                    if (av[0] instanceof Long && av[1] instanceof Long) {
                        long a = (Long) av[0], z = (Long) av[1];
                        ArrayList<Object> l = new ArrayList<>();
                        for (long k = a; k < z; k++) l.add(k);
                        return new TList(l);
                    }
                    break;
                case 7:
                    if (av[1] instanceof Long && av[2] instanceof Long) {
                        long a = (Long) av[1], z = (Long) av[2];
                        if (av[0] instanceof String) {
                            String s = (String) av[0];
                            if (0 <= a && a <= z && z <= s.length()) return s.substring((int) a, (int) z);
                        } else if (av[0] instanceof TList) {
                            ArrayList<Object> l = ((TList) av[0]).a;
                            if (0 <= a && a <= z && z <= l.size()) return new TList(new ArrayList<>(l.subList((int) a, (int) z)));
                        }
                    }
                    break;
            }
            throw new TErr(line, "bad argument to " + b.name);
        }
        throw new TErr(line, "cannot call " + typeName(f));
    }

    static void run(String path) throws Exception {
        String src = new String(Files.readAllBytes(Paths.get(path, "program.tern")), "ISO-8859-1");
        toks = lex(src); pos = 0;
        ArrayList<Stmt> prog = new ArrayList<>();
        while (peek().kind != T_EOF) prog.add(parseStmt());
        Scope builtins = new Scope(null, 16);
        for (int i = 0; i < BUILTINS.length; i++) builtins.declare(intern(BUILTINS[i]), new Builtin(BUILTINS[i], i));
        Scope global = new Scope(builtins, 32);
        try {
            for (Stmt st : prog) st.exec(global);
        } catch (TErr e) {
            out.append("error at line ").append(e.line).append(": ").append(e.msg).append('\n');
        }
        flushOut();
        stdout.flush();
    }

    public static void main(String[] args) throws Exception {
        stdout = new PrintStream(new FileOutputStream(FileDescriptor.out), false, "ISO-8859-1");
        Thread t = new Thread(null, () -> {
            try { run(args[0]); } catch (Exception e) { throw new RuntimeException(e); }
        }, "main", 1L << 29);
        t.start();
        t.join();
    }
}
