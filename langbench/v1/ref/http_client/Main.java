import java.net.URI;
import java.net.http.*;
import java.math.BigDecimal;
import java.util.*;

public class Main {
    static String s; static int p;
    static void ws() { while (p < s.length() && Character.isWhitespace(s.charAt(p))) p++; }
    static Object parse() {
        ws();
        char c = s.charAt(p);
        if (c == '{') {
            p++; Map<String,Object> m = new LinkedHashMap<>(); ws();
            if (s.charAt(p) == '}') { p++; return m; }
            while (true) { ws(); String k = str(); ws(); p++; m.put(k, parse()); ws(); if (s.charAt(p++) == '}') break; }
            return m;
        }
        if (c == '[') {
            p++; List<Object> l = new ArrayList<>(); ws();
            if (s.charAt(p) == ']') { p++; return l; }
            while (true) { l.add(parse()); ws(); if (s.charAt(p++) == ']') break; }
            return l;
        }
        if (c == '"') return str();
        if (s.startsWith("true", p)) { p += 4; return Boolean.TRUE; }
        if (s.startsWith("false", p)) { p += 5; return Boolean.FALSE; }
        if (s.startsWith("null", p)) { p += 4; return null; }
        int st = p;
        while (p < s.length() && "+-0123456789.eE".indexOf(s.charAt(p)) >= 0) p++;
        return new java.math.BigDecimal(s.substring(st, p));
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
                    case 'u' -> { sb.append((char) Integer.parseInt(s.substring(p, p + 4), 16)); p += 4; }
                    default -> sb.append(e);
                }
            } else sb.append(c);
        }
        return sb.toString();
    }
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest req = HttpRequest.newBuilder(URI.create("http://127.0.0.1:__PORT__/api/products")).GET().build();
        s = client.send(req, HttpResponse.BodyHandlers.ofString()).body(); p = 0;
        List<Object> arr = (List<Object>) parse();
        List<Map<String,Object>> ps = new ArrayList<>();
        for (Object o : arr) {
            Map<String,Object> m = (Map<String,Object>) o;
            if (((BigDecimal) m.get("price")).compareTo(new BigDecimal("50.00")) > 0) ps.add(m);
        }
        ps.sort(Comparator.comparing(m -> (String) m.get("name")));
        BigDecimal total = BigDecimal.ZERO;
        StringBuilder sb = new StringBuilder("=== Filtered Products (price > $50.00) ===\n\n");
        for (var m : ps) {
            BigDecimal pr = (BigDecimal) m.get("price");
            total = total.add(pr);
            sb.append(m.get("name")).append(": $").append(String.format("%.2f", pr)).append("\n");
        }
        sb.append("\nCount: ").append(ps.size()).append("\nTotal: $").append(String.format("%.2f", total)).append("\n");
        System.out.print(sb);
    }
}
