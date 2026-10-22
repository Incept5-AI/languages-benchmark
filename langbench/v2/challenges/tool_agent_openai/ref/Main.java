import java.io.*;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static final Object JNULL = new Object();
    static final class Num { final String t; Num(String t) { this.t = t; } }

    // ---------- JSON parsing ----------
    static final class P {
        final String s; int i = 0;
        P(String s) { this.s = s; }
        void ws() { while (i < s.length()) { char c = s.charAt(i); if (c == ' ' || c == '\t' || c == '\n' || c == '\r') i++; else break; } }
        Object val() {
            ws();
            char c = s.charAt(i);
            if (c == '{') {
                i++; LinkedHashMap<String, Object> m = new LinkedHashMap<>(); ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws(); String k = str(); ws(); i++; // ':'
                    Object v = val(); m.put(k, v); ws();
                    char d = s.charAt(i++);
                    if (d == '}') return m;
                }
            } else if (c == '[') {
                i++; ArrayList<Object> a = new ArrayList<>(); ws();
                if (s.charAt(i) == ']') { i++; return a; }
                while (true) {
                    a.add(val()); ws();
                    char d = s.charAt(i++);
                    if (d == ']') return a;
                }
            } else if (c == '"') {
                return str();
            } else if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            else if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            else if (s.startsWith("null", i)) { i += 4; return JNULL; }
            else {
                int st = i;
                while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
                return new Num(s.substring(st, i));
            }
        }
        String str() {
            i++; StringBuilder b = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') return b.toString();
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'b': b.append('\b'); break;
                        case 'f': b.append('\f'); break;
                        case 'n': b.append('\n'); break;
                        case 'r': b.append('\r'); break;
                        case 't': b.append('\t'); break;
                        case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                        default: b.append(e);
                    }
                } else b.append(c);
            }
        }
    }

    static Object parse(String s) { return new P(s).val(); }

    // ---------- JSON writing ----------
    static void esc(StringBuilder b, String s) {
        b.append('"');
        for (int k = 0; k < s.length(); k++) {
            char c = s.charAt(k);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\b': b.append("\\b"); break;
                case '\f': b.append("\\f"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        b.append('"');
    }

    @SuppressWarnings("unchecked")
    static void write(StringBuilder b, Object v) {
        if (v == JNULL || v == null) b.append("null");
        else if (v instanceof String) esc(b, (String) v);
        else if (v instanceof Num) b.append(((Num) v).t);
        else if (v instanceof Boolean) b.append(v.toString());
        else if (v instanceof Map) {
            b.append('{'); boolean first = true;
            for (Map.Entry<String, Object> e : ((Map<String, Object>) v).entrySet()) {
                if (!first) b.append(','); first = false;
                esc(b, e.getKey()); b.append(':'); write(b, e.getValue());
            }
            b.append('}');
        } else if (v instanceof List) {
            b.append('['); boolean first = true;
            for (Object o : (List<Object>) v) { if (!first) b.append(','); first = false; write(b, o); }
            b.append(']');
        }
    }

    static String toJson(Object v) { StringBuilder b = new StringBuilder(); write(b, v); return b.toString(); }

    // ---------- CSV ----------
    static List<List<String>> csv(String s) {
        if (s.startsWith("﻿")) s = s.substring(1);
        List<List<String>> rows = new ArrayList<>();
        int i = 0, n = s.length();
        while (i < n) {
            List<String> row = new ArrayList<>();
            while (true) {
                StringBuilder f = new StringBuilder();
                if (i < n && s.charAt(i) == '"') {
                    i++;
                    while (i < n) {
                        char c = s.charAt(i);
                        if (c == '"') {
                            if (i + 1 < n && s.charAt(i + 1) == '"') { f.append('"'); i += 2; }
                            else { i++; break; }
                        } else { f.append(c); i++; }
                    }
                }
                while (i < n && s.charAt(i) != ',' && s.charAt(i) != '\r' && s.charAt(i) != '\n') { f.append(s.charAt(i)); i++; }
                row.add(f.toString());
                if (i < n && s.charAt(i) == ',') { i++; continue; }
                if (i < n && s.charAt(i) == '\r') i++;
                if (i < n && s.charAt(i) == '\n') i++;
                break;
            }
            rows.add(row);
        }
        return rows;
    }

    static String normInt(String s) {
        return new BigInteger(s.trim()).toString();
    }

    static BigInteger toBig(Object o) {
        if (o instanceof Num) return new BigDecimal(((Num) o).t).toBigInteger();
        if (o instanceof String) return new BigDecimal((String) o).toBigInteger();
        return BigInteger.ZERO;
    }

    // ---------- HTTP ----------
    static String host; static int port; static String pathPrefix;

    static int lastStatus;
    static String post(String path, String apiKey, String body) throws IOException {
        byte[] bb = body.getBytes(StandardCharsets.UTF_8);
        try (Socket sock = new Socket(host, port)) {
            OutputStream os = sock.getOutputStream();
            String hdr = "POST " + path + " HTTP/1.1\r\nHost: " + host + ":" + port + "\r\n"
                    + "Authorization: Bearer " + apiKey + "\r\n"
                    + "Content-Type: application/json\r\n"
                    + "Content-Length: " + bb.length + "\r\n"
                    + "Connection: close\r\n\r\n";
            os.write(hdr.getBytes(StandardCharsets.UTF_8));
            os.write(bb);
            os.flush();
            byte[] all = sock.getInputStream().readAllBytes();
            int sep = -1;
            for (int k = 0; k + 3 < all.length; k++) {
                if (all[k] == '\r' && all[k + 1] == '\n' && all[k + 2] == '\r' && all[k + 3] == '\n') { sep = k; break; }
            }
            String head = new String(all, 0, sep, StandardCharsets.ISO_8859_1);
            String[] lines = head.split("\r\n");
            String[] sl = lines[0].split(" ");
            lastStatus = Integer.parseInt(sl[1]);
            int cl = -1;
            for (int k = 1; k < lines.length; k++) {
                int c = lines[k].indexOf(':');
                if (c > 0 && lines[k].substring(0, c).trim().equalsIgnoreCase("content-length"))
                    cl = Integer.parseInt(lines[k].substring(c + 1).trim());
            }
            int start = sep + 4;
            int len = cl >= 0 ? Math.min(cl, all.length - start) : all.length - start;
            return new String(all, start, len, StandardCharsets.UTF_8);
        }
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        Path dir = Paths.get(args[0]);
        String base = args[1];
        String rest = base.substring(base.indexOf("://") + 3);
        int slash = rest.indexOf('/');
        String hp = slash >= 0 ? rest.substring(0, slash) : rest;
        pathPrefix = slash >= 0 ? rest.substring(slash) : "";
        int colon = hp.lastIndexOf(':');
        if (colon >= 0) { host = hp.substring(0, colon); port = Integer.parseInt(hp.substring(colon + 1)); }
        else { host = hp; port = 80; }

        Map<String, Object> task = (Map<String, Object>) parse(Files.readString(dir.resolve("task.json"), StandardCharsets.UTF_8));
        Map<String, Object> ratesDoc = (Map<String, Object>) parse(Files.readString(dir.resolve("rates.json"), StandardCharsets.UTF_8));
        Map<String, Object> rates = (Map<String, Object>) ratesDoc.get("rates");
        List<List<String>> rows = csv(new String(Files.readAllBytes(dir.resolve("inventory.csv")), StandardCharsets.UTF_8));
        Map<String, List<String>> inv = new HashMap<>();
        for (int k = 1; k < rows.size(); k++) {
            List<String> r = rows.get(k);
            if (r.size() >= 5) inv.put(r.get(0), r);
        }

        String apiKey = (String) task.get("api_key");
        List<Object> messages = new ArrayList<>();
        LinkedHashMap<String, Object> m0 = new LinkedHashMap<>(); m0.put("role", "system"); m0.put("content", task.get("system")); messages.add(m0);
        LinkedHashMap<String, Object> m1 = new LinkedHashMap<>(); m1.put("role", "user"); m1.put("content", task.get("user")); messages.add(m1);

        BigInteger pt = BigInteger.ZERO, ct = BigInteger.ZERO, tt = BigInteger.ZERO;
        int requests = 0;
        while (true) {
            LinkedHashMap<String, Object> req = new LinkedHashMap<>();
            req.put("model", task.get("model"));
            req.put("messages", messages);
            req.put("tools", task.get("tools"));
            req.put("tool_choice", "auto");
            String body = post(pathPrefix + "/chat/completions", apiKey, toJson(req));
            requests++;
            if (lastStatus != 200) {
                String msg = "";
                try {
                    Map<String, Object> e = (Map<String, Object>) parse(body);
                    Object err = e.get("error");
                    if (err instanceof Map) {
                        Object mm = ((Map<String, Object>) err).get("message");
                        if (mm instanceof String) msg = (String) mm;
                    }
                } catch (Exception ex) { }
                out.println("ERROR " + lastStatus + " " + msg);
                out.flush();
                System.exit(0);
            }
            Map<String, Object> resp = (Map<String, Object>) parse(body);
            Object u = resp.get("usage");
            if (u instanceof Map) {
                Map<String, Object> um = (Map<String, Object>) u;
                if (um.get("prompt_tokens") instanceof Num) pt = pt.add(toBig(um.get("prompt_tokens")));
                if (um.get("completion_tokens") instanceof Num) ct = ct.add(toBig(um.get("completion_tokens")));
                if (um.get("total_tokens") instanceof Num) tt = tt.add(toBig(um.get("total_tokens")));
            }
            Map<String, Object> choice = (Map<String, Object>) ((List<Object>) resp.get("choices")).get(0);
            Map<String, Object> msg = (Map<String, Object>) choice.get("message");
            Object fr = choice.get("finish_reason");
            if ("tool_calls".equals(fr)) {
                messages.add(msg);
                Object content = msg.get("content");
                if (content instanceof String) out.println("SAY " + content);
                List<Object> calls = (List<Object>) msg.get("tool_calls");
                List<String> results = new ArrayList<>();
                for (Object co : calls) {
                    Map<String, Object> call = (Map<String, Object>) co;
                    Map<String, Object> fn = (Map<String, Object>) call.get("function");
                    String id = (String) call.get("id");
                    String name = (String) fn.get("name");
                    out.println("CALL " + id + " " + name);
                    String res;
                    if (name.equals("inventory_lookup")) {
                        Map<String, Object> a = (Map<String, Object>) parse((String) fn.get("arguments"));
                        String sku = (String) a.get("sku");
                        List<String> r = inv.get(sku);
                        StringBuilder b = new StringBuilder();
                        if (r == null) {
                            b.append("{\"error\":"); esc(b, "unknown sku: " + sku); b.append('}');
                        } else {
                            b.append("{\"sku\":"); esc(b, r.get(0));
                            b.append(",\"name\":"); esc(b, r.get(1));
                            b.append(",\"price_cents\":").append(normInt(r.get(2)));
                            b.append(",\"currency\":"); esc(b, r.get(3));
                            b.append(",\"stock\":").append(normInt(r.get(4)));
                            b.append('}');
                        }
                        res = b.toString();
                    } else if (name.equals("convert_currency")) {
                        Map<String, Object> a = (Map<String, Object>) parse((String) fn.get("arguments"));
                        BigInteger amt = toBig(a.get("amount_cents"));
                        String from = (String) a.get("from"), to = (String) a.get("to");
                        BigInteger rf = toBig(rates.get(from)), rt = toBig(rates.get(to));
                        BigInteger two = BigInteger.TWO;
                        BigInteger conv = two.multiply(amt).multiply(rt).add(rf).divide(two.multiply(rf));
                        StringBuilder b = new StringBuilder();
                        b.append("{\"amount_cents\":").append(conv.toString()).append(",\"currency\":"); esc(b, to); b.append('}');
                        res = b.toString();
                    } else {
                        StringBuilder b = new StringBuilder();
                        b.append("{\"error\":"); esc(b, "unknown tool: " + name); b.append('}');
                        res = b.toString();
                    }
                    results.add(res);
                    LinkedHashMap<String, Object> tm = new LinkedHashMap<>();
                    tm.put("role", "tool"); tm.put("tool_call_id", id); tm.put("content", res);
                    messages.add(tm);
                }
                for (int k = 0; k < calls.size(); k++) {
                    String id = (String) ((Map<String, Object>) calls.get(k)).get("id");
                    out.println("RESULT " + id + " " + results.get(k));
                }
            } else {
                Object content = msg.get("content");
                out.println("FINAL " + (content instanceof String ? (String) content : ""));
                break;
            }
        }
        out.println("USAGE prompt=" + pt + " completion=" + ct + " total=" + tt + " requests=" + requests);
        out.flush();
    }
}
