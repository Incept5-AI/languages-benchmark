import java.nio.file.*;
import java.util.*;

public class Main {
    record Num(String text) {}
    static String s; static int p;

    static void ws() { while (p < s.length() && Character.isWhitespace(s.charAt(p))) p++; }
    static Object parse() {
        ws();
        char c = s.charAt(p);
        if (c == '{') {
            p++; Map<String,Object> m = new TreeMap<>(); ws();
            if (s.charAt(p) == '}') { p++; return m; }
            while (true) {
                ws(); String k = str(); ws(); p++; // colon
                m.put(k, parse()); ws();
                if (s.charAt(p++) == '}') break;
            }
            return m;
        }
        if (c == '[') {
            p++; List<Object> l = new ArrayList<>(); ws();
            if (s.charAt(p) == ']') { p++; return l; }
            while (true) {
                l.add(parse()); ws();
                if (s.charAt(p++) == ']') break;
            }
            return l;
        }
        if (c == '"') return str();
        if (s.startsWith("true", p)) { p += 4; return Boolean.TRUE; }
        if (s.startsWith("false", p)) { p += 5; return Boolean.FALSE; }
        if (s.startsWith("null", p)) { p += 4; return null; }
        int st = p;
        while (p < s.length() && "+-0123456789.eE".indexOf(s.charAt(p)) >= 0) p++;
        return new Num(s.substring(st, p));
    }
    static String str() {
        StringBuilder sb = new StringBuilder(); p++;
        while (true) {
            char c = s.charAt(p++);
            if (c == '"') break;
            if (c == '\\') {
                char e = s.charAt(p++);
                switch (e) {
                    case 'n' -> sb.append('\n'); case 't' -> sb.append('\t');
                    case 'r' -> sb.append('\r'); case 'b' -> sb.append('\b');
                    case 'f' -> sb.append('\f');
                    case 'u' -> { sb.append((char) Integer.parseInt(s.substring(p, p + 4), 16)); p += 4; }
                    default -> sb.append(e);
                }
            } else sb.append(c);
        }
        return sb.toString();
    }
    @SuppressWarnings("unchecked")
    static Object merge(Object a, Object b) {
        if (a instanceof Map && b instanceof Map) {
            Map<String,Object> r = new TreeMap<>((Map<String,Object>) a);
            for (var e : ((Map<String,Object>) b).entrySet()) {
                if (r.containsKey(e.getKey())) r.put(e.getKey(), merge(r.get(e.getKey()), e.getValue()));
                else r.put(e.getKey(), e.getValue());
            }
            return r;
        }
        if (a instanceof List && b instanceof List) {
            List<Object> r = new ArrayList<>();
            for (Object o : (List<Object>) a) if (!r.contains(o)) r.add(o);
            for (Object o : (List<Object>) b) if (!r.contains(o)) r.add(o);
            return r;
        }
        return b;
    }
    static String quote(String v) {
        StringBuilder sb = new StringBuilder("\"");
        for (char c : v.toCharArray()) {
            switch (c) {
                case '"' -> sb.append("\\\""); case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n"); case '\t' -> sb.append("\\t");
                case '\r' -> sb.append("\\r");
                default -> { if (c < 0x20) sb.append(String.format("\\u%04x", (int) c)); else sb.append(c); }
            }
        }
        return sb.append('"').toString();
    }
    @SuppressWarnings("unchecked")
    static void out(Object o, int ind, StringBuilder sb) {
        String pad = "  ".repeat(ind + 1), end = "  ".repeat(ind);
        if (o instanceof Map) {
            Map<String,Object> m = (Map<String,Object>) o;
            if (m.isEmpty()) { sb.append("{}"); return; }
            sb.append("{\n"); int i = 0;
            for (var e : m.entrySet()) {
                sb.append(pad).append(quote(e.getKey())).append(": ");
                out(e.getValue(), ind + 1, sb);
                if (++i < m.size()) sb.append(","); sb.append("\n");
            }
            sb.append(end).append("}");
        } else if (o instanceof List) {
            List<Object> l = (List<Object>) o;
            if (l.isEmpty()) { sb.append("[]"); return; }
            sb.append("[\n");
            for (int i = 0; i < l.size(); i++) {
                sb.append(pad); out(l.get(i), ind + 1, sb);
                if (i < l.size() - 1) sb.append(","); sb.append("\n");
            }
            sb.append(end).append("]");
        } else if (o == null) sb.append("null");
        else if (o instanceof String str) sb.append(quote(str));
        else if (o instanceof Num n) sb.append(n.text());
        else sb.append(o);
    }
    static Object load(String f) throws Exception { s = Files.readString(Path.of(f)); p = 0; return parse(); }
    public static void main(String[] args) throws Exception {
        Object a = load("__FIXTURE_DIR__/config_a.json");
        Object b = load("__FIXTURE_DIR__/config_b.json");
        StringBuilder sb = new StringBuilder();
        out(merge(a, b), 0, sb);
        System.out.println(sb);
    }
}
