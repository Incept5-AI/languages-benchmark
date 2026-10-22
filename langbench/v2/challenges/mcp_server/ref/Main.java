import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class Main {
    static final Object NULL = new Object();
    static final class Num { final String raw; final boolean isInt; Num(String r, boolean i) { raw = r; isInt = i; } }
    static final class ParseError extends RuntimeException { ParseError() { super(null, null, false, false); } }

    static final class Parser {
        final String s; int p = 0;
        Parser(String s) { this.s = s; }
        void ws() { while (p < s.length()) { char c = s.charAt(p); if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p++; else break; } }
        Object parseAll() { ws(); Object v = value(); ws(); if (p != s.length()) throw new ParseError(); return v; }
        Object value() {
            if (p >= s.length()) throw new ParseError();
            char c = s.charAt(p);
            switch (c) {
                case '{': return obj();
                case '[': return arr();
                case '"': return str();
                case 't': lit("true"); return Boolean.TRUE;
                case 'f': lit("false"); return Boolean.FALSE;
                case 'n': lit("null"); return NULL;
                default: if (c == '-' || (c >= '0' && c <= '9')) return num(); throw new ParseError();
            }
        }
        void lit(String w) { if (!s.startsWith(w, p)) throw new ParseError(); p += w.length(); }
        Object obj() {
            p++; LinkedHashMap<String, Object> m = new LinkedHashMap<>(); ws();
            if (p < s.length() && s.charAt(p) == '}') { p++; return m; }
            while (true) {
                ws(); if (p >= s.length() || s.charAt(p) != '"') throw new ParseError();
                String k = str(); ws();
                if (p >= s.length() || s.charAt(p) != ':') throw new ParseError();
                p++; ws(); Object v = value(); m.remove(k); m.put(k, v); ws();
                if (p >= s.length()) throw new ParseError();
                char c = s.charAt(p++);
                if (c == '}') return m;
                if (c != ',') throw new ParseError();
            }
        }
        Object arr() {
            p++; ArrayList<Object> a = new ArrayList<>(); ws();
            if (p < s.length() && s.charAt(p) == ']') { p++; return a; }
            while (true) {
                ws(); a.add(value()); ws();
                if (p >= s.length()) throw new ParseError();
                char c = s.charAt(p++);
                if (c == ']') return a;
                if (c != ',') throw new ParseError();
            }
        }
        int hex(char c) { if (c >= '0' && c <= '9') return c - '0'; if (c >= 'a' && c <= 'f') return c - 'a' + 10; if (c >= 'A' && c <= 'F') return c - 'A' + 10; throw new ParseError(); }
        String str() {
            p++; StringBuilder b = new StringBuilder();
            while (true) {
                if (p >= s.length()) throw new ParseError();
                char c = s.charAt(p++);
                if (c == '"') return b.toString();
                if (c < 0x20) throw new ParseError();
                if (c == '\\') {
                    if (p >= s.length()) throw new ParseError();
                    char e = s.charAt(p++);
                    switch (e) {
                        case '"': b.append('"'); break; case '\\': b.append('\\'); break; case '/': b.append('/'); break;
                        case 'b': b.append('\b'); break; case 'f': b.append('\f'); break; case 'n': b.append('\n'); break;
                        case 'r': b.append('\r'); break; case 't': b.append('\t'); break;
                        case 'u': {
                            if (p + 4 > s.length()) throw new ParseError();
                            int v = 0; for (int i = 0; i < 4; i++) v = v * 16 + hex(s.charAt(p++));
                            b.append((char) v); break;
                        }
                        default: throw new ParseError();
                    }
                } else b.append(c);
            }
        }
        Object num() {
            int st = p; boolean isInt = true;
            if (s.charAt(p) == '-') p++;
            if (p >= s.length()) throw new ParseError();
            if (s.charAt(p) == '0') p++;
            else if (s.charAt(p) >= '1' && s.charAt(p) <= '9') { while (p < s.length() && Character.isDigit(s.charAt(p)) && s.charAt(p) < 128) p++; }
            else throw new ParseError();
            if (p < s.length() && s.charAt(p) == '.') {
                isInt = false; p++; int d = p;
                while (p < s.length() && s.charAt(p) >= '0' && s.charAt(p) <= '9') p++;
                if (p == d) throw new ParseError();
            }
            if (p < s.length() && (s.charAt(p) == 'e' || s.charAt(p) == 'E')) {
                isInt = false; p++;
                if (p < s.length() && (s.charAt(p) == '+' || s.charAt(p) == '-')) p++;
                int d = p;
                while (p < s.length() && s.charAt(p) >= '0' && s.charAt(p) <= '9') p++;
                if (p == d) throw new ParseError();
            }
            return new Num(s.substring(st, p), isInt);
        }
    }

    static void ser(StringBuilder o, Object v) {
        if (v == NULL || v == null) o.append("null");
        else if (v instanceof Boolean) o.append(((Boolean) v) ? "true" : "false");
        else if (v instanceof Num) o.append(((Num) v).raw);
        else if (v instanceof Long || v instanceof Integer) o.append(v.toString());
        else if (v instanceof String) serStr(o, (String) v);
        else if (v instanceof List) {
            o.append('['); boolean f = true;
            for (Object x : (List<?>) v) { if (!f) o.append(','); f = false; ser(o, x); }
            o.append(']');
        } else if (v instanceof Map) {
            o.append('{'); boolean f = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) { if (!f) o.append(','); f = false; serStr(o, (String) e.getKey()); o.append(':'); ser(o, e.getValue()); }
            o.append('}');
        } else if (v instanceof RawJson) o.append(((RawJson) v).s);
    }
    static final class RawJson { final String s; RawJson(String s) { this.s = s; } }
    static void serStr(StringBuilder o, String s) {
        o.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"') o.append("\\\"");
            else if (c == '\\') o.append("\\\\");
            else if (c < 0x20 || c > 0x7e) o.append(String.format("\\u%04x", (int) c));
            else o.append(c);
        }
        o.append('"');
    }

    static boolean validId(Object id) { return id instanceof String || (id instanceof Num && ((Num) id).isInt); }

    static final String TOOLS = """
[{"name":"kv_set","title":"Set a value","description":"Store a string value under a key, replacing any previous value.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1},"value":{"type":"string"}},"required":["key","value"],"additionalProperties":false}},{"name":"kv_get","title":"Get a value","description":"Return the value stored under a key.","inputSchema":{"type":"object","properties":{"key":{"type":"string","minLength":1}},"required":["key"],"additionalProperties":false}},{"name":"kv_list","title":"List keys","description":"List the stored keys that start with a prefix, sorted by Unicode code point.","inputSchema":{"type":"object","properties":{"prefix":{"type":"string"}},"additionalProperties":false},"outputSchema":{"type":"object","properties":{"keys":{"type":"array","items":{"type":"string"}},"count":{"type":"integer"}},"required":["keys","count"]}},{"name":"sum","title":"Sum integers","description":"Add up a non-empty list of integers.","inputSchema":{"type":"object","properties":{"numbers":{"type":"array","items":{"type":"integer"},"minItems":1}},"required":["numbers"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"sum":{"type":"integer"},"count":{"type":"integer"}},"required":["sum","count"]},"annotations":{"readOnlyHint":true,"idempotentHint":true}},{"name":"text_stats","title":"Text statistics","description":"Count the words, Unicode code points and UTF-8 bytes of a text.","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false},"outputSchema":{"type":"object","properties":{"words":{"type":"integer"},"codepoints":{"type":"integer"},"utf8_bytes":{"type":"integer"}},"required":["words","codepoints","utf8_bytes"]},"annotations":{"readOnlyHint":true}}]""";

    static boolean initialized = false;
    static final TreeMap<String, String> kv = new TreeMap<>((a, b) -> {
        int i = 0, j = 0;
        while (i < a.length() && j < b.length()) {
            int ca = a.codePointAt(i), cb = b.codePointAt(j);
            if (ca != cb) return Integer.compare(ca, cb);
            i += Character.charCount(ca); j += Character.charCount(cb);
        }
        return Integer.compare(a.length() - i, b.length() - j);
    });

    static String errorResp(Object id, int code, String msg) {
        StringBuilder o = new StringBuilder("{\"jsonrpc\":\"2.0\",\"id\":");
        ser(o, id); o.append(",\"error\":{\"code\":").append(code).append(",\"message\":"); serStr(o, msg); o.append("}}");
        return o.toString();
    }
    static String resultResp(Object id, Object result) {
        StringBuilder o = new StringBuilder("{\"jsonrpc\":\"2.0\",\"id\":");
        ser(o, id); o.append(",\"result\":"); ser(o, result); o.append('}');
        return o.toString();
    }
    static Map<String, Object> textResult(String text, boolean isError) {
        LinkedHashMap<String, Object> m = new LinkedHashMap<>();
        LinkedHashMap<String, Object> c = new LinkedHashMap<>();
        c.put("type", "text"); c.put("text", text);
        m.put("content", List.of(c)); m.put("isError", isError);
        return m;
    }
    static Map<String, Object> structResult(Map<String, Object> S) {
        StringBuilder t = new StringBuilder(); ser(t, S);
        LinkedHashMap<String, Object> m = new LinkedHashMap<>();
        LinkedHashMap<String, Object> c = new LinkedHashMap<>();
        c.put("type", "text"); c.put("text", t.toString());
        m.put("content", List.of(c)); m.put("structuredContent", S); m.put("isError", false);
        return m;
    }

    // tool specs: properties in order: name, type ("string" / "intarray"), required, nonEmpty
    static final Map<String, String[][]> SPECS = new HashMap<>();
    static {
        SPECS.put("kv_set", new String[][]{{"key", "string", "1", "1"}, {"value", "string", "1", "0"}});
        SPECS.put("kv_get", new String[][]{{"key", "string", "1", "1"}});
        SPECS.put("kv_list", new String[][]{{"prefix", "string", "0", "0"}});
        SPECS.put("sum", new String[][]{{"numbers", "intarray", "1", "1"}});
        SPECS.put("text_stats", new String[][]{{"text", "string", "1", "0"}});
    }
    static int cmpCp(String a, String b) { return kv.comparator().compare(a, b); }

    @SuppressWarnings("unchecked")
    static Object callTool(String name, Object argsObj) {
        String[][] spec = SPECS.get(name);
        Map<String, Object> args;
        if (argsObj == null) args = new LinkedHashMap<>();
        else if (argsObj instanceof Map) args = (Map<String, Object>) argsObj;
        else return textResult("arguments must be an object", true);
        String unknown = null;
        for (String k : args.keySet()) {
            boolean known = false;
            for (String[] pr : spec) if (pr[0].equals(k)) known = true;
            if (!known && (unknown == null || cmpCp(k, unknown) < 0)) unknown = k;
        }
        if (unknown != null) return textResult("unknown argument: " + unknown, true);
        for (String[] pr : spec) {
            String pn = pr[0];
            if (!args.containsKey(pn)) {
                if (pr[2].equals("1")) return textResult("missing required argument: " + pn, true);
                continue;
            }
            Object v = args.get(pn);
            if (pr[1].equals("string")) {
                if (!(v instanceof String)) return textResult("argument " + pn + " must be a string", true);
                if (pr[3].equals("1") && ((String) v).isEmpty()) return textResult("argument " + pn + " must not be empty", true);
            } else {
                boolean ok = v instanceof List;
                if (ok) for (Object x : (List<Object>) v) if (!(x instanceof Num && ((Num) x).isInt)) ok = false;
                if (!ok) return textResult("argument " + pn + " must be an array of integers", true);
                if (pr[3].equals("1") && ((List<Object>) v).isEmpty()) return textResult("argument " + pn + " must not be empty", true);
            }
        }
        switch (name) {
            case "kv_set": {
                String k = (String) args.get("key"), v = (String) args.get("value");
                boolean existed = kv.containsKey(k);
                kv.put(k, v);
                return textResult((existed ? "updated " : "created ") + k, false);
            }
            case "kv_get": {
                String k = (String) args.get("key");
                if (!kv.containsKey(k)) return textResult("key not found: " + k, true);
                return textResult(kv.get(k), false);
            }
            case "kv_list": {
                String pre = args.containsKey("prefix") ? (String) args.get("prefix") : "";
                ArrayList<Object> keys = new ArrayList<>();
                for (String k : kv.keySet()) if (k.startsWith(pre)) keys.add(k);
                LinkedHashMap<String, Object> S = new LinkedHashMap<>();
                S.put("keys", keys); S.put("count", (long) keys.size());
                return structResult(S);
            }
            case "sum": {
                List<Object> a = (List<Object>) args.get("numbers");
                long sum = 0;
                for (Object x : a) sum += new java.math.BigDecimal(((Num) x).raw).longValue();
                LinkedHashMap<String, Object> S = new LinkedHashMap<>();
                S.put("sum", sum); S.put("count", (long) a.size());
                return structResult(S);
            }
            default: {
                String t = (String) args.get("text");
                long words = 0, cps = 0, bytes = 0; boolean in = false;
                for (int i = 0; i < t.length(); ) {
                    int cp = t.codePointAt(i); i += Character.charCount(cp);
                    cps++;
                    bytes += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
                    boolean sep = cp == ' ' || cp == '\t' || cp == '\n' || cp == '\r';
                    if (!sep && !in) words++;
                    in = !sep;
                }
                LinkedHashMap<String, Object> S = new LinkedHashMap<>();
                S.put("words", words); S.put("codepoints", cps); S.put("utf8_bytes", bytes);
                return structResult(S);
            }
        }
    }

    @SuppressWarnings("unchecked")
    static String handle(String line) {
        Object msg;
        try { msg = new Parser(line).parseAll(); } catch (ParseError | StackOverflowError e) {
            return errorResp(NULL, -32700, "Parse error");
        }
        if (!(msg instanceof Map)) return errorResp(NULL, -32600, "Invalid Request");
        Map<String, Object> m = (Map<String, Object>) msg;
        if (!m.containsKey("method") && (m.containsKey("result") || m.containsKey("error"))) return null;
        boolean hasId = m.containsKey("id");
        Object id = m.get("id");
        boolean idOk = hasId && validId(id);
        if (!"2.0".equals(m.get("jsonrpc")) || !(m.get("method") instanceof String) || (hasId && !idOk))
            return errorResp(idOk ? id : NULL, -32600, "Invalid Request");
        if (!hasId) return null;
        String method = (String) m.get("method");
        if (!method.equals("initialize") && !method.equals("ping") && !method.equals("tools/list") && !method.equals("tools/call"))
            return errorResp(id, -32601, "Method not found");
        boolean hasParams = m.containsKey("params");
        Object params = m.get("params");
        if (hasParams && !(params instanceof Map)) return errorResp(id, -32602, "Invalid params");
        Map<String, Object> pm = hasParams ? (Map<String, Object>) params : null;
        if (!initialized && !method.equals("initialize") && !method.equals("ping"))
            return errorResp(id, -32002, "Server not initialized");
        switch (method) {
            case "initialize": {
                if (initialized) return errorResp(id, -32600, "Already initialized");
                if (pm == null || !(pm.get("protocolVersion") instanceof String)) return errorResp(id, -32602, "Invalid params");
                String v = (String) pm.get("protocolVersion");
                if (!v.equals("2025-06-18") && !v.equals("2025-03-26") && !v.equals("2024-11-05")) v = "2025-06-18";
                initialized = true;
                StringBuilder r = new StringBuilder("{\"protocolVersion\":"); serStr(r, v);
                r.append(",\"capabilities\":{\"tools\":{\"listChanged\":false}},\"serverInfo\":{\"name\":\"cb2-kv\",\"title\":\"CB2 key-value server\",\"version\":\"1.4.2\"}}");
                return resultResp(id, new RawJson(r.toString()));
            }
            case "ping": return resultResp(id, new RawJson("{}"));
            case "tools/list": return resultResp(id, new RawJson("{\"tools\":" + TOOLS.trim() + "}"));
            default: {
                if (pm == null || !(pm.get("name") instanceof String)) return errorResp(id, -32602, "Invalid params");
                String name = (String) pm.get("name");
                if (!SPECS.containsKey(name)) return errorResp(id, -32602, "Unknown tool: " + name);
                Object a = pm.containsKey("arguments") ? pm.get("arguments") : null;
                if (pm.containsKey("arguments") && a == NULL) return resultResp(id, textResult("arguments must be an object", true));
                return resultResp(id, callTool(name, a));
            }
        }
    }

    public static void main(String[] args) throws Exception {
        Thread t = new Thread(null, () -> {
            try { run(); } catch (IOException e) { throw new UncheckedIOException(e); }
        }, "main", 1L << 30);
        t.start(); t.join();
        System.exit(0);
    }
    static void run() throws IOException {
        byte[] all = System.in.readAllBytes();
        StringBuilder out = new StringBuilder();
        int st = 0;
        for (int i = 0; i <= all.length; i++) {
            if (i == all.length || all[i] == '\n') {
                if (i == all.length && st == i) break;
                String line = new String(all, st, i - st, StandardCharsets.UTF_8);
                st = i + 1;
                boolean blank = true;
                for (int k = 0; k < line.length(); k++) { char c = line.charAt(k); if (c != ' ' && c != '\t' && c != '\r') { blank = false; break; } }
                if (blank) continue;
                String r = handle(line);
                if (r != null) out.append(r).append('\n');
            }
        }
        System.out.write(out.toString().getBytes(StandardCharsets.UTF_8));
        System.out.flush();
    }
}
