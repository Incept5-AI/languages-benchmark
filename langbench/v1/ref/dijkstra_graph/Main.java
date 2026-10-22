import java.nio.file.*;
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
        s = Files.readString(Path.of("__FIXTURE_DIR__/graph.json")); p = 0;
        Map<String,Object> g = (Map<String,Object>) parse();
        Map<String,List<Object[]>> adj = new TreeMap<>();
        for (Object n : (List<Object>) g.get("nodes")) adj.put((String) n, new ArrayList<>());
        for (Object o : (List<Object>) g.get("edges")) {
            Map<String,Object> e = (Map<String,Object>) o;
            String a = (String) e.get("from"), b = (String) e.get("to");
            double w = ((BigDecimal) e.get("weight")).doubleValue();
            adj.computeIfAbsent(a, k -> new ArrayList<>()).add(new Object[]{b, w});
            adj.computeIfAbsent(b, k -> new ArrayList<>()).add(new Object[]{a, w});
        }
        Map<String,Double> dist = new HashMap<>();
        dist.put("A", 0.0);
        PriorityQueue<Object[]> pq = new PriorityQueue<>(Comparator.comparingDouble(x -> (Double) x[1]));
        pq.add(new Object[]{"A", 0.0});
        while (!pq.isEmpty()) {
            Object[] cur = pq.poll();
            String u = (String) cur[0]; double d = (Double) cur[1];
            if (d > dist.getOrDefault(u, Double.MAX_VALUE)) continue;
            for (Object[] e : adj.getOrDefault(u, List.of())) {
                double nd = d + (Double) e[1];
                if (nd < dist.getOrDefault((String) e[0], Double.MAX_VALUE)) {
                    dist.put((String) e[0], nd);
                    pq.add(new Object[]{e[0], nd});
                }
            }
        }
        StringBuilder sb = new StringBuilder("=== Shortest Paths from A ===\n\n");
        for (String n : adj.keySet()) {
            Double d = dist.get(n);
            sb.append("A -> ").append(n).append(": ");
            if (d == null) sb.append("unreachable");
            else if (d == Math.floor(d)) sb.append((long) (double) d);
            else sb.append(d);
            sb.append("\n");
        }
        System.out.print(sb);
    }
}
