import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    // ---------- JSON ----------
    static final class Num { final String raw; Num(String r) { raw = r; } }
    static final Object NULL = new Object();

    static final class JP {
        final String s; int p = 0;
        JP(String s) { this.s = s; }
        void ws() { while (p < s.length() && " \t\r\n".indexOf(s.charAt(p)) >= 0) p++; }
        Object parse() { ws(); Object v = val(); ws(); if (p != s.length()) throw new RuntimeException("trailing"); return v; }
        Object val() {
            ws();
            char c = s.charAt(p);
            if (c == '{') {
                p++; LinkedHashMap<String, Object> m = new LinkedHashMap<>(); ws();
                if (s.charAt(p) == '}') { p++; return m; }
                while (true) {
                    ws(); String k = str(); ws();
                    if (s.charAt(p++) != ':') throw new RuntimeException("colon");
                    m.put(k, val()); ws();
                    char d = s.charAt(p++);
                    if (d == '}') return m;
                    if (d != ',') throw new RuntimeException("comma");
                }
            }
            if (c == '[') {
                p++; ArrayList<Object> a = new ArrayList<>(); ws();
                if (s.charAt(p) == ']') { p++; return a; }
                while (true) {
                    a.add(val()); ws();
                    char d = s.charAt(p++);
                    if (d == ']') return a;
                    if (d != ',') throw new RuntimeException("comma");
                }
            }
            if (c == '"') return str();
            if (s.startsWith("true", p)) { p += 4; return Boolean.TRUE; }
            if (s.startsWith("false", p)) { p += 5; return Boolean.FALSE; }
            if (s.startsWith("null", p)) { p += 4; return NULL; }
            int st = p;
            while (p < s.length() && "+-0123456789.eE".indexOf(s.charAt(p)) >= 0) p++;
            if (st == p) throw new RuntimeException("bad json");
            return new Num(s.substring(st, p));
        }
        String str() {
            if (s.charAt(p) != '"') throw new RuntimeException("expected string");
            p++;
            StringBuilder b = new StringBuilder();
            while (true) {
                char c = s.charAt(p++);
                if (c == '"') return b.toString();
                if (c == '\\') {
                    char e = s.charAt(p++);
                    switch (e) {
                        case 'n': b.append('\n'); break;
                        case 'r': b.append('\r'); break;
                        case 't': b.append('\t'); break;
                        case 'b': b.append('\b'); break;
                        case 'f': b.append('\f'); break;
                        case 'u': b.append((char) Integer.parseInt(s.substring(p, p + 4), 16)); p += 4; break;
                        default: b.append(e);
                    }
                } else b.append(c);
            }
        }
    }

    static void ser(Object v, StringBuilder o) {
        if (v == null || v == NULL) o.append("null");
        else if (v instanceof String) serStr((String) v, o);
        else if (v instanceof Num) o.append(((Num) v).raw);
        else if (v instanceof Boolean) o.append(v.toString());
        else if (v instanceof Integer || v instanceof Long) o.append(v.toString());
        else if (v instanceof Map) {
            o.append('{'); boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) o.append(','); first = false;
                serStr((String) e.getKey(), o); o.append(':'); ser(e.getValue(), o);
            }
            o.append('}');
        } else if (v instanceof List) {
            o.append('['); boolean first = true;
            for (Object x : (List<?>) v) { if (!first) o.append(','); first = false; ser(x, o); }
            o.append(']');
        } else throw new RuntimeException("ser");
    }
    static void serStr(String s, StringBuilder o) {
        o.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': o.append("\\\""); break;
                case '\\': o.append("\\\\"); break;
                case '\n': o.append("\\n"); break;
                case '\r': o.append("\\r"); break;
                case '\t': o.append("\\t"); break;
                default:
                    if (c < 0x20 || (Character.isSurrogate(c) && !validSurrogateAt(s, i))) o.append(String.format("\\u%04x", (int) c));
                    else o.append(c);
            }
        }
        o.append('"');
    }
    static boolean validSurrogateAt(String s, int i) {
        char c = s.charAt(i);
        if (Character.isHighSurrogate(c)) return i + 1 < s.length() && Character.isLowSurrogate(s.charAt(i + 1));
        return i > 0 && Character.isHighSurrogate(s.charAt(i - 1));
    }
    static String toJson(Object v) { StringBuilder b = new StringBuilder(); ser(v, b); return b.toString(); }

    @SuppressWarnings("unchecked")
    static Map<String, Object> obj(Object o) { return o instanceof Map ? (Map<String, Object>) o : null; }
    static long num(Object o) {
        if (o instanceof Num) { try { return Long.parseLong(((Num) o).raw); } catch (Exception e) { return (long) Double.parseDouble(((Num) o).raw); } }
        return 0;
    }
    static String sget(Map<String, Object> m, String k) {
        if (m == null) return "";
        Object v = m.get(k);
        return v instanceof String ? (String) v : "";
    }

    // ---------- output ----------
    static final StringBuilder OUT = new StringBuilder();
    static void emit(String s) { OUT.append(s).append('\n'); }
    static void flush() {
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8);
        ps.print(OUT); ps.flush(); OUT.setLength(0);
    }
    static String esc(String s) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\') b.append("\\\\");
            else if (c == '\n') b.append("\\n");
            else if (c == '\r') b.append("\\r");
            else if (c == '\t') b.append("\\t");
            else b.append(c);
        }
        return b.toString();
    }

    // ---------- HTTP ----------
    static String host, prefix; static int port;

    static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            b.write(c);
        }
        if (c == -1 && b.size() == 0) return null;
        String s = b.toString(StandardCharsets.ISO_8859_1);
        if (s.endsWith("\r")) s = s.substring(0, s.length() - 1);
        return s;
    }
    static byte[] readN(InputStream in, int n) throws IOException {
        byte[] b = new byte[n]; int off = 0;
        while (off < n) { int r = in.read(b, off, n - off); if (r < 0) break; off += r; }
        return off == n ? b : Arrays.copyOf(b, off);
    }

    static int status; static byte[] respBody;

    static void post(String apiKey, byte[] body) throws IOException {
        try (Socket sock = new Socket(host, port)) {
            OutputStream os = sock.getOutputStream();
            String head = "POST " + prefix + "/v1/messages HTTP/1.1\r\n"
                + "Host: " + host + ":" + port + "\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "x-api-key: " + apiKey + "\r\n"
                + "anthropic-version: 2023-06-01\r\n"
                + "content-type: application/json\r\n\r\n";
            os.write(head.getBytes(StandardCharsets.ISO_8859_1));
            os.write(body); os.flush();
            InputStream in = new BufferedInputStream(sock.getInputStream());
            String sl = readLine(in);
            status = Integer.parseInt(sl.split(" ")[1]);
            int cl = -1; boolean chunked = false;
            String h;
            while ((h = readLine(in)) != null && !h.isEmpty()) {
                int i = h.indexOf(':'); if (i < 0) continue;
                String k = h.substring(0, i).trim().toLowerCase(), v = h.substring(i + 1).trim();
                if (k.equals("content-length")) cl = Integer.parseInt(v);
                if (k.equals("transfer-encoding") && v.toLowerCase().contains("chunked")) chunked = true;
            }
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            if (chunked) {
                while (true) {
                    String line = readLine(in);
                    if (line == null) break;
                    int semi = line.indexOf(';'); if (semi >= 0) line = line.substring(0, semi);
                    line = line.trim(); if (line.isEmpty()) continue;
                    int sz = Integer.parseInt(line, 16);
                    if (sz == 0) break;
                    bo.write(readN(in, sz));
                    readLine(in);
                }
            } else if (cl >= 0) bo.write(readN(in, cl));
            else in.transferTo(bo);
            respBody = bo.toByteArray();
        }
    }

    // ---------- SSE ----------
    static final class Block { String type, text = "", sig = "", id = "", name = "", json = ""; }
    static long uIn, uOut, uCache;

    static String stop;
    static TreeMap<Long, Block> blocks;

    static void dispatch(String ev, List<String> data) {
        if (data.isEmpty()) return;
        Map<String, Object> o;
        try { o = obj(new JP(String.join("\n", data)).parse()); } catch (Exception e) { return; }
        if (o == null) return;
        String t = ev != null ? ev : sget(o, "type");
        switch (t) {
            case "message_start": {
                Map<String, Object> u = obj(obj(o.get("message")) == null ? null : obj(o.get("message")).get("usage"));
                if (u != null) { uIn += num(u.get("input_tokens")); uCache += num(u.get("cache_read_input_tokens")); }
                break;
            }
            case "content_block_start": {
                Map<String, Object> cb = obj(o.get("content_block"));
                Block b = new Block(); b.type = sget(cb, "type"); b.id = sget(cb, "id"); b.name = sget(cb, "name");
                blocks.put(num(o.get("index")), b);
                break;
            }
            case "content_block_delta": {
                Block b = blocks.get(num(o.get("index"))); if (b == null) return;
                Map<String, Object> d = obj(o.get("delta"));
                String dt = sget(d, "type");
                if (dt.equals("thinking_delta")) b.text += sget(d, "thinking");
                else if (dt.equals("signature_delta")) b.sig += sget(d, "signature");
                else if (dt.equals("text_delta")) b.text += sget(d, "text");
                else if (dt.equals("input_json_delta")) b.json += sget(d, "partial_json");
                break;
            }
            case "message_delta": {
                Map<String, Object> d = obj(o.get("delta"));
                if (d != null && d.get("stop_reason") instanceof String) stop = (String) d.get("stop_reason");
                Map<String, Object> u = obj(o.get("usage"));
                if (u != null) uOut += num(u.get("output_tokens"));
                break;
            }
            default: break;
        }
    }

    static List<Block> parseSSE(byte[] body) {
        stop = ""; blocks = new TreeMap<>();
        String text = new String(body, StandardCharsets.UTF_8);
        String[] lines = text.split("\n", -1);
        String ev = null; List<String> data = new ArrayList<>();
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (i == lines.length - 1 && line.isEmpty()) break;
            if (line.endsWith("\r")) line = line.substring(0, line.length() - 1);
            if (line.isEmpty()) { dispatch(ev, data); ev = null; data = new ArrayList<>(); continue; }
            if (line.startsWith(":")) continue;
            int c = line.indexOf(':');
            String f, v;
            if (c < 0) { f = line; v = ""; } else { f = line.substring(0, c); v = line.substring(c + 1); if (v.startsWith(" ")) v = v.substring(1); }
            if (f.equals("event")) ev = v;
            else if (f.equals("data")) data.add(v);
        }
        dispatch(ev, data);
        return new ArrayList<>(blocks.values());
    }

    static void printReply(List<Block> bl, String sr) {
        for (Block b : bl) {
            if (b.type.equals("thinking")) { emit("thinking " + esc(b.text)); emit("signature " + esc(b.sig)); }
            else if (b.type.equals("text")) emit("text " + esc(b.text));
            else if (b.type.equals("tool_use")) emit("tool_use " + esc(b.id) + " " + esc(b.name) + " " + esc(b.json));
        }
        emit("stop_reason " + esc(sr));
    }

    static String[] runTool(String name, Map<String, Object> in) {
        if (name.equals("measure")) {
            String text = sget(in, "text"); Object u = in.get("unit");
            String unit = u instanceof String ? (String) u : (u == null ? "" : toJson(u));
            switch (unit) {
                case "code_points": return new String[]{"1", String.valueOf(text.codePointCount(0, text.length()))};
                case "utf8_bytes": return new String[]{"1", String.valueOf(text.getBytes(StandardCharsets.UTF_8).length)};
                case "utf16_units": return new String[]{"1", String.valueOf(text.length())};
                default: return new String[]{"0", "unsupported unit: " + unit};
            }
        }
        if (name.equals("reverse")) {
            String text = sget(in, "text");
            int[] cps = text.codePoints().toArray();
            StringBuilder b = new StringBuilder();
            for (int i = cps.length - 1; i >= 0; i--) b.appendCodePoint(cps[i]);
            return new String[]{"1", b.toString()};
        }
        return new String[]{"0", "unknown tool: " + name};
    }

    static boolean handleError() {
        if (status == 200) return false;
        String et = "", em = "";
        try {
            Map<String, Object> o = obj(new JP(new String(respBody, StandardCharsets.UTF_8)).parse());
            Map<String, Object> e = obj(o.get("error"));
            et = sget(e, "type"); em = sget(e, "message");
        } catch (Exception ex) {}
        emit("ERROR " + status + " " + esc(et) + ": " + esc(em));
        return true;
    }

    public static void main(String[] args) {
        try { run(args); } catch (Exception e) { e.printStackTrace(); }
        flush();
        System.exit(0);
    }

    static void run(String[] args) throws Exception {
        String dataDir = args[0], base = args[1];
        URI u = URI.create(base);
        host = u.getHost(); port = u.getPort() < 0 ? 80 : u.getPort();
        prefix = u.getRawPath() == null ? "" : u.getRawPath();
        if (prefix.endsWith("/")) prefix = prefix.substring(0, prefix.length() - 1);
        Map<String, Object> req = obj(new JP(new String(Files.readAllBytes(Paths.get(dataDir, "request.json")), StandardCharsets.UTF_8)).parse());
        String apiKey = sget(req, "api_key");

        LinkedHashMap<String, Object> userMsg = new LinkedHashMap<>();
        userMsg.put("role", "user"); userMsg.put("content", req.get("prompt"));

        emit("request 1");
        List<Object> msgs = new ArrayList<>(); msgs.add(userMsg);
        post(apiKey, body(req, msgs));
        if (handleError()) return;
        List<Block> r1 = parseSSE(respBody);
        printReply(r1, stop);

        List<Object> asst = new ArrayList<>(), results = new ArrayList<>();
        for (Block b : r1) {
            LinkedHashMap<String, Object> m = new LinkedHashMap<>();
            if (b.type.equals("thinking")) { m.put("type", "thinking"); m.put("thinking", b.text); m.put("signature", b.sig); asst.add(m); }
            else if (b.type.equals("text")) { m.put("type", "text"); m.put("text", b.text); asst.add(m); }
            else if (b.type.equals("tool_use")) {
                Object input = b.json.trim().isEmpty() ? new LinkedHashMap<String, Object>() : new JP(b.json).parse();
                m.put("type", "tool_use"); m.put("id", b.id); m.put("name", b.name); m.put("input", input); asst.add(m);
                Map<String, Object> in = obj(input); if (in == null) in = new LinkedHashMap<>();
                String[] r = runTool(b.name, in);
                boolean ok = r[0].equals("1");
                emit((ok ? "result " : "error ") + esc(b.id) + " " + esc(r[1]));
                LinkedHashMap<String, Object> tr = new LinkedHashMap<>();
                tr.put("type", "tool_result"); tr.put("tool_use_id", b.id); tr.put("content", r[1]);
                if (!ok) tr.put("is_error", Boolean.TRUE);
                results.add(tr);
            }
        }
        emit("request 2");
        LinkedHashMap<String, Object> am = new LinkedHashMap<>(); am.put("role", "assistant"); am.put("content", asst);
        LinkedHashMap<String, Object> um = new LinkedHashMap<>(); um.put("role", "user"); um.put("content", results);
        msgs.add(am); msgs.add(um);
        post(apiKey, body(req, msgs));
        if (handleError()) return;
        List<Block> r2 = parseSSE(respBody);
        printReply(r2, stop);
        emit("usage input=" + uIn + " output=" + uOut + " cache_read=" + uCache);
    }

    static byte[] body(Map<String, Object> req, List<Object> msgs) {
        LinkedHashMap<String, Object> b = new LinkedHashMap<>();
        b.put("model", req.get("model"));
        b.put("max_tokens", req.get("max_tokens"));
        b.put("stream", Boolean.TRUE);
        LinkedHashMap<String, Object> th = new LinkedHashMap<>();
        th.put("type", "enabled"); th.put("budget_tokens", req.get("thinking_budget"));
        b.put("thinking", th);
        b.put("system", req.get("system"));
        b.put("tools", req.get("tools"));
        b.put("messages", msgs);
        return toJson(b).getBytes(StandardCharsets.UTF_8);
    }
}
