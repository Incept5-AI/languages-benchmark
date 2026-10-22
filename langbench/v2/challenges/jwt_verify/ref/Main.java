import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public class Main {
    static final class JsonErr extends Exception {}

    static final class Parser {
        final String s; int p = 0; int depth = 0;
        Parser(String s) { this.s = s; }
        void ws() { while (p < s.length()) { char c = s.charAt(p); if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p++; else break; } }
        Object parseAll() throws JsonErr { Object v = value(); ws(); if (p != s.length()) throw new JsonErr(); return v; }
        Object value() throws JsonErr {
            ws();
            if (p >= s.length()) throw new JsonErr();
            if (++depth > 1000) throw new JsonErr();
            try {
                char c = s.charAt(p);
                if (c == '{') {
                    p++;
                    LinkedHashMap<String, Object> m = new LinkedHashMap<>();
                    ws();
                    if (p < s.length() && s.charAt(p) == '}') { p++; return m; }
                    while (true) {
                        ws();
                        String k = string();
                        ws();
                        if (p >= s.length() || s.charAt(p) != ':') throw new JsonErr();
                        p++;
                        Object v = value();
                        m.put(k, v);
                        ws();
                        if (p >= s.length()) throw new JsonErr();
                        char d = s.charAt(p++);
                        if (d == ',') continue;
                        if (d == '}') return m;
                        throw new JsonErr();
                    }
                } else if (c == '[') {
                    p++;
                    ArrayList<Object> a = new ArrayList<>();
                    ws();
                    if (p < s.length() && s.charAt(p) == ']') { p++; return a; }
                    while (true) {
                        a.add(value());
                        ws();
                        if (p >= s.length()) throw new JsonErr();
                        char d = s.charAt(p++);
                        if (d == ',') continue;
                        if (d == ']') return a;
                        throw new JsonErr();
                    }
                } else if (c == '"') {
                    return string();
                } else if (s.startsWith("true", p)) { p += 4; return Boolean.TRUE; }
                else if (s.startsWith("false", p)) { p += 5; return Boolean.FALSE; }
                else if (s.startsWith("null", p)) { p += 4; return JSONNULL; }
                else return number();
            } finally { depth--; }
        }
        boolean digit() { return p < s.length() && s.charAt(p) >= '0' && s.charAt(p) <= '9'; }
        Double number() throws JsonErr {
            int st = p;
            if (p < s.length() && s.charAt(p) == '-') p++;
            if (p >= s.length()) throw new JsonErr();
            if (s.charAt(p) == '0') p++;
            else if (digit()) { while (digit()) p++; }
            else throw new JsonErr();
            if (p < s.length() && s.charAt(p) == '.') { p++; if (!digit()) throw new JsonErr(); while (digit()) p++; }
            if (p < s.length() && (s.charAt(p) == 'e' || s.charAt(p) == 'E')) {
                p++;
                if (p < s.length() && (s.charAt(p) == '+' || s.charAt(p) == '-')) p++;
                if (!digit()) throw new JsonErr();
                while (digit()) p++;
            }
            return Double.parseDouble(s.substring(st, p));
        }
        String string() throws JsonErr {
            if (p >= s.length() || s.charAt(p) != '"') throw new JsonErr();
            p++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (p >= s.length()) throw new JsonErr();
                char c = s.charAt(p++);
                if (c == '"') return sb.toString();
                if (c < 0x20) throw new JsonErr();
                if (c == '\\') {
                    if (p >= s.length()) throw new JsonErr();
                    char e = s.charAt(p++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u': {
                            if (p + 4 > s.length()) throw new JsonErr();
                            int v = 0;
                            for (int i = 0; i < 4; i++) {
                                int h = Character.digit(s.charAt(p++), 16);
                                if (h < 0) throw new JsonErr();
                                v = v * 16 + h;
                            }
                            sb.append((char) v);
                            break;
                        }
                        default: throw new JsonErr();
                    }
                } else sb.append(c);
            }
        }
    }
    static final Object JSONNULL = new Object();

    static int b64v(char c) {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '-') return 62;
        if (c == '_') return 63;
        return -1;
    }
    static byte[] b64dec(String s) {
        if (s.length() % 4 == 1) return null;
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int acc = 0, bits = 0;
        for (int i = 0; i < s.length(); i++) {
            int v = b64v(s.charAt(i));
            if (v < 0) return null;
            acc = (acc << 6) | v; bits += 6;
            if (bits >= 8) { bits -= 8; out.write((acc >> bits) & 0xFF); }
        }
        return out.toByteArray();
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> parseObj(byte[] b) {
        try {
            Object v = new Parser(new String(b, StandardCharsets.UTF_8)).parseAll();
            return v instanceof Map ? (Map<String, Object>) v : null;
        } catch (JsonErr e) { return null; }
    }

    static Map<String, Object> cfg;
    static List<Object> keys;

    static int cmpCp(String a, String b) {
        int i = 0, j = 0;
        while (i < a.length() && j < b.length()) {
            int x = a.codePointAt(i), y = b.codePointAt(j);
            if (x != y) return Integer.compare(x, y);
            i += Character.charCount(x); j += Character.charCount(y);
        }
        return Integer.compare(a.length() - i, b.length() - j);
    }

    static double num(Object o) { return o instanceof Double ? (Double) o : 0; }

    @SuppressWarnings("unchecked")
    static String check(String tok) throws Exception {
        String[] segs = tok.split("\\.", -1);
        if (segs.length != 3) return "MALFORMED";
        byte[][] dec = new byte[3][];
        for (int i = 0; i < 3; i++) {
            if (segs[i].isEmpty()) return "MALFORMED";
            dec[i] = b64dec(segs[i]);
            if (dec[i] == null) return "MALFORMED";
        }
        Map<String, Object> hdr = parseObj(dec[0]);
        if (hdr == null) return "MALFORMED";
        Map<String, Object> pl = parseObj(dec[1]);
        if (pl == null) return "MALFORMED";
        Object alg = hdr.get("alg");
        String macName; int hl;
        if ("HS256".equals(alg)) { macName = "HmacSHA256"; hl = 32; }
        else if ("HS384".equals(alg)) { macName = "HmacSHA384"; hl = 48; }
        else if ("HS512".equals(alg)) { macName = "HmacSHA512"; hl = 64; }
        else return "UNSUPPORTED_ALG";
        Object kid = hdr.get("kid");
        if (!(kid instanceof String)) return "UNKNOWN_KEY";
        Map<String, Object> key = null;
        for (Object k : keys) {
            if (k instanceof Map && kid.equals(((Map<String, Object>) k).get("kid"))) { key = (Map<String, Object>) k; break; }
        }
        if (key == null) return "UNKNOWN_KEY";
        if (!alg.equals(key.get("alg"))) return "ALG_MISMATCH";
        byte[] secret = key.get("k") instanceof String ? b64dec((String) key.get("k")) : null;
        if (secret == null || secret.length == 0) secret = new byte[] { 0 }; // HMAC pads with zeros: equivalent to empty key
        Mac mac = Mac.getInstance(macName);
        mac.init(new SecretKeySpec(secret, macName));
        byte[] sig = mac.doFinal((segs[0] + "." + segs[1]).getBytes(StandardCharsets.US_ASCII));
        if (dec[2].length != hl || !Arrays.equals(sig, dec[2])) return "BAD_SIGNATURE";
        double now = num(cfg.get("now")), leeway = num(cfg.get("leeway"));
        Object exp = pl.get("exp");
        if (exp instanceof Double && now >= (Double) exp + leeway) return "EXPIRED";
        Object nbf = pl.get("nbf");
        if (nbf instanceof Double && now + leeway < (Double) nbf) return "NOT_YET_VALID";
        Object iss = pl.get("iss");
        if (!(iss instanceof String) || !iss.equals(cfg.get("issuer"))) return "BAD_ISSUER";
        Object aud = pl.get("aud");
        Object caud = cfg.get("audience");
        boolean ok = false;
        if (aud instanceof String) ok = aud.equals(caud);
        else if (aud instanceof List) for (Object a : (List<Object>) aud) if (a instanceof String && a.equals(caud)) { ok = true; break; }
        if (!ok) return "BAD_AUDIENCE";
        Object sub = pl.get("sub");
        String subS = sub instanceof String ? (String) sub : "";
        List<String> scopes = new ArrayList<>();
        Object sc = pl.get("scope");
        if (sc instanceof String) {
            TreeSet<String> set = new TreeSet<>(Main::cmpCp);
            for (String piece : ((String) sc).split(" ")) if (!piece.isEmpty()) set.add(piece);
            scopes.addAll(set);
        }
        return "VALID sub=" + subS + " scope=" + String.join(",", scopes);
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        Path dir = Paths.get(args[0]);
        cfg = (Map<String, Object>) new Parser(new String(Files.readAllBytes(dir.resolve("config.json")), StandardCharsets.UTF_8)).parseAll();
        Object ks = cfg.get("keys");
        keys = ks instanceof List ? (List<Object>) ks : new ArrayList<>();
        String raw = new String(Files.readAllBytes(dir.resolve("tokens.txt")), StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder();
        int n = 0, valid = 0, invalid = 0;
        for (String line : raw.split("\n", -1)) {
            if (line.endsWith("\r")) line = line.substring(0, line.length() - 1);
            int a = 0, b = line.length();
            while (a < b && (line.charAt(a) == ' ' || line.charAt(a) == '\t')) a++;
            while (b > a && (line.charAt(b - 1) == ' ' || line.charAt(b - 1) == '\t')) b--;
            line = line.substring(a, b);
            if (line.isEmpty()) continue;
            n++;
            String r = check(line);
            if (r.startsWith("VALID")) valid++; else invalid++;
            out.append(n).append(": ").append(r).append('\n');
        }
        out.append('\n').append("valid=").append(valid).append(" invalid=").append(invalid).append('\n');
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(out);
        ps.flush();
    }
}
