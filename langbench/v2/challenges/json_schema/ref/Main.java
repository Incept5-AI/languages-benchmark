import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static final Object NULL = new Object();

    // ---------- JSON parser ----------
    static class Parser {
        final String s; int i = 0;
        Parser(String s) { this.s = s; }
        void ws() { while (i < s.length()) { char c = s.charAt(i); if (c == ' ' || c == '\t' || c == '\n' || c == '\r') i++; else break; } }
        Object parse() { ws(); Object v = value(); ws(); return v; }
        Object value() {
            ws();
            char c = s.charAt(i);
            if (c == '{') {
                i++; LinkedHashMap<String, Object> m = new LinkedHashMap<>();
                ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws(); String k = str(); ws(); i++; // ':'
                    Object v = value(); m.put(k, v); ws();
                    char d = s.charAt(i++);
                    if (d == '}') break;
                }
                return m;
            } else if (c == '[') {
                i++; ArrayList<Object> a = new ArrayList<>();
                ws();
                if (s.charAt(i) == ']') { i++; return a; }
                while (true) {
                    a.add(value()); ws();
                    char d = s.charAt(i++);
                    if (d == ']') break;
                }
                return a;
            } else if (c == '"') {
                return str();
            } else if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            else if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            else if (s.startsWith("null", i)) { i += 4; return NULL; }
            else {
                int st = i;
                while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
                return Double.parseDouble(s.substring(st, i));
            }
        }
        String str() {
            i++; StringBuilder b = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') break;
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n': b.append('\n'); break;
                        case 't': b.append('\t'); break;
                        case 'r': b.append('\r'); break;
                        case 'b': b.append('\b'); break;
                        case 'f': b.append('\f'); break;
                        case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                        default: b.append(e);
                    }
                } else b.append(c);
            }
            return b.toString();
        }
    }

    // ---------- helpers ----------
    static String esc(String tok) { return tok.replace("~", "~0").replace("/", "~1"); }

    static int cpCompare(String a, String b) {
        int i = 0, j = 0;
        while (i < a.length() && j < b.length()) {
            int ca = a.codePointAt(i), cb = b.codePointAt(j);
            if (ca != cb) return Integer.compare(ca, cb);
            i += Character.charCount(ca); j += Character.charCount(cb);
        }
        return Integer.compare(a.length() - i, b.length() - j);
    }

    static String typeOf(Object v) {
        if (v == NULL) return "null";
        if (v instanceof Boolean) return "boolean";
        if (v instanceof Double) return "number";
        if (v instanceof String) return "string";
        if (v instanceof List) return "array";
        return "object";
    }

    static boolean isInt(double d) { return d == Math.floor(d) && !Double.isInfinite(d); }

    @SuppressWarnings("unchecked")
    static boolean deepEq(Object a, Object b) {
        String ta = typeOf(a), tb = typeOf(b);
        if (!ta.equals(tb)) return false;
        switch (ta) {
            case "null": return true;
            case "boolean": return a.equals(b);
            case "number": return ((Double) a).doubleValue() == ((Double) b).doubleValue();
            case "string": return a.equals(b);
            case "array": {
                List<Object> x = (List<Object>) a, y = (List<Object>) b;
                if (x.size() != y.size()) return false;
                for (int k = 0; k < x.size(); k++) if (!deepEq(x.get(k), y.get(k))) return false;
                return true;
            }
            default: {
                Map<String, Object> x = (Map<String, Object>) a, y = (Map<String, Object>) b;
                if (x.size() != y.size()) return false;
                for (Map.Entry<String, Object> e : x.entrySet()) {
                    if (!y.containsKey(e.getKey())) return false;
                    if (!deepEq(e.getValue(), y.get(e.getKey()))) return false;
                }
                return true;
            }
        }
    }

    static Object root;

    static Object resolve(String ref) {
        if (ref.equals("#")) return root;
        String name = ref.substring("#/$defs/".length()).replace("~1", "/").replace("~0", "~");
        return ((Map<?, ?>) ((Map<?, ?>) root).get("$defs")).get(name);
    }
    static String refLoc(String ref) {
        if (ref.equals("#")) return "#";
        String name = ref.substring("#/$defs/".length()).replace("~1", "/").replace("~0", "~");
        return "#/$defs/" + esc(name);
    }

    static final class Err {
        final String l, p;
        Err(String l, String p) { this.l = l; this.p = p; }
        public boolean equals(Object o) { Err e = (Err) o; return l.equals(e.l) && p.equals(e.p); }
        public int hashCode() { return l.hashCode() * 31 + p.hashCode(); }
    }

    static boolean accepts(Object inst, String L, Object s, String P) {
        HashSet<Err> e = new HashSet<>();
        validate(inst, L, s, P, e);
        return e.isEmpty();
    }

    static long cpLen(String s) { return s.codePointCount(0, s.length()); }

    @SuppressWarnings("unchecked")
    static void validate(Object inst, String L, Object s, String P, Set<Err> out) {
        if (s instanceof Boolean) {
            if (!(Boolean) s) out.add(new Err(L, P));
            return;
        }
        Map<String, Object> m = (Map<String, Object>) s;
        String it = typeOf(inst);
        if (m.containsKey("type")) {
            Object t = m.get("type");
            List<Object> ts = t instanceof List ? (List<Object>) t : List.of(t);
            boolean ok = false;
            for (Object o : ts) {
                String n = (String) o;
                if (n.equals(it)) ok = true;
                else if (n.equals("integer") && it.equals("number") && isInt((Double) inst)) ok = true;
            }
            if (!ok) out.add(new Err(L, P + "/type"));
        }
        if (m.containsKey("enum")) {
            boolean ok = false;
            for (Object o : (List<Object>) m.get("enum")) if (deepEq(inst, o)) { ok = true; break; }
            if (!ok) out.add(new Err(L, P + "/enum"));
        }
        if (m.containsKey("const")) {
            if (!deepEq(inst, m.get("const"))) out.add(new Err(L, P + "/const"));
        }
        if (it.equals("number")) {
            double d = (Double) inst;
            if (m.containsKey("minimum") && !(d >= (Double) m.get("minimum"))) out.add(new Err(L, P + "/minimum"));
            if (m.containsKey("maximum") && !(d <= (Double) m.get("maximum"))) out.add(new Err(L, P + "/maximum"));
            if (m.containsKey("exclusiveMinimum") && !(d > (Double) m.get("exclusiveMinimum"))) out.add(new Err(L, P + "/exclusiveMinimum"));
            if (m.containsKey("exclusiveMaximum") && !(d < (Double) m.get("exclusiveMaximum"))) out.add(new Err(L, P + "/exclusiveMaximum"));
        }
        if (it.equals("string")) {
            long len = cpLen((String) inst);
            if (m.containsKey("minLength") && !(len >= (Double) m.get("minLength"))) out.add(new Err(L, P + "/minLength"));
            if (m.containsKey("maxLength") && !(len <= (Double) m.get("maxLength"))) out.add(new Err(L, P + "/maxLength"));
        }
        if (it.equals("array")) {
            List<Object> a = (List<Object>) inst;
            int n = a.size();
            if (m.containsKey("minItems") && !(n >= (Double) m.get("minItems"))) out.add(new Err(L, P + "/minItems"));
            if (m.containsKey("maxItems") && !(n <= (Double) m.get("maxItems"))) out.add(new Err(L, P + "/maxItems"));
            if (Boolean.TRUE.equals(m.get("uniqueItems"))) {
                boolean dup = false;
                outer:
                for (int x = 0; x < n; x++) for (int y = x + 1; y < n; y++) if (deepEq(a.get(x), a.get(y))) { dup = true; break outer; }
                if (dup) out.add(new Err(L, P + "/uniqueItems"));
            }
            int pl = 0;
            if (m.containsKey("prefixItems")) {
                List<Object> pi = (List<Object>) m.get("prefixItems");
                pl = pi.size();
                for (int k = 0; k < Math.min(pl, n); k++) validate(a.get(k), L + "/" + k, pi.get(k), P + "/prefixItems/" + k, out);
            }
            if (m.containsKey("items")) {
                for (int k = pl; k < n; k++) validate(a.get(k), L + "/" + k, m.get("items"), P + "/items", out);
            }
        }
        if (it.equals("object")) {
            Map<String, Object> o = (Map<String, Object>) inst;
            Map<String, Object> props = m.containsKey("properties") ? (Map<String, Object>) m.get("properties") : Map.of();
            for (Map.Entry<String, Object> e : o.entrySet()) {
                if (props.containsKey(e.getKey()))
                    validate(e.getValue(), L + "/" + esc(e.getKey()), props.get(e.getKey()), P + "/properties/" + esc(e.getKey()), out);
            }
            if (m.containsKey("required")) {
                List<Object> r = (List<Object>) m.get("required");
                for (int j = 0; j < r.size(); j++) if (!o.containsKey((String) r.get(j))) out.add(new Err(L, P + "/required/" + j));
            }
            if (m.containsKey("additionalProperties")) {
                for (Map.Entry<String, Object> e : o.entrySet()) {
                    if (!props.containsKey(e.getKey()))
                        validate(e.getValue(), L + "/" + esc(e.getKey()), m.get("additionalProperties"), P + "/additionalProperties", out);
                }
            }
            int n = o.size();
            if (m.containsKey("minProperties") && !(n >= (Double) m.get("minProperties"))) out.add(new Err(L, P + "/minProperties"));
            if (m.containsKey("maxProperties") && !(n <= (Double) m.get("maxProperties"))) out.add(new Err(L, P + "/maxProperties"));
        }
        if (m.containsKey("allOf")) {
            List<Object> l = (List<Object>) m.get("allOf");
            for (int k = 0; k < l.size(); k++) validate(inst, L, l.get(k), P + "/allOf/" + k, out);
        }
        if (m.containsKey("anyOf")) {
            List<Object> l = (List<Object>) m.get("anyOf");
            boolean ok = false;
            for (int k = 0; k < l.size(); k++) if (accepts(inst, L, l.get(k), P + "/anyOf/" + k)) { ok = true; break; }
            if (!ok) out.add(new Err(L, P + "/anyOf"));
        }
        if (m.containsKey("oneOf")) {
            List<Object> l = (List<Object>) m.get("oneOf");
            int c = 0;
            for (int k = 0; k < l.size(); k++) if (accepts(inst, L, l.get(k), P + "/oneOf/" + k)) c++;
            if (c != 1) out.add(new Err(L, P + "/oneOf"));
        }
        if (m.containsKey("not")) {
            if (accepts(inst, L, m.get("not"), P + "/not")) out.add(new Err(L, P + "/not"));
        }
        if (m.containsKey("if")) {
            if (accepts(inst, L, m.get("if"), P + "/if")) {
                if (m.containsKey("then")) validate(inst, L, m.get("then"), P + "/then", out);
            } else {
                if (m.containsKey("else")) validate(inst, L, m.get("else"), P + "/else", out);
            }
        }
        if (m.containsKey("$ref")) {
            String ref = (String) m.get("$ref");
            validate(inst, L, resolve(ref), refLoc(ref), out);
        }
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        Path dir = Paths.get(args[0]);
        root = new Parser(new String(Files.readAllBytes(dir.resolve("schema.json")), StandardCharsets.UTF_8)).parse();
        List<Object> insts = (List<Object>) new Parser(new String(Files.readAllBytes(dir.resolve("instances.json")), StandardCharsets.UTF_8)).parse();
        StringBuilder sb = new StringBuilder();
        int valid = 0, invalid = 0;
        for (int n = 0; n < insts.size(); n++) {
            HashSet<Err> errs = new HashSet<>();
            validate(insts.get(n), "#", root, "#", errs);
            if (errs.isEmpty()) { valid++; sb.append(n).append(": valid\n"); continue; }
            invalid++;
            List<Err> l = new ArrayList<>(errs);
            l.sort((a, b) -> { int c = cpCompare(a.l, b.l); return c != 0 ? c : cpCompare(a.p, b.p); });
            sb.append(n).append(": invalid (").append(l.size()).append(")\n");
            for (Err e : l) sb.append("  ").append(e.l).append(' ').append(e.p).append('\n');
        }
        sb.append("valid=").append(valid).append(" invalid=").append(invalid).append('\n');
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(sb);
        ps.flush();
    }
}
