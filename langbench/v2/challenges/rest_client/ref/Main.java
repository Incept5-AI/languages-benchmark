import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static String host;
    static int port;
    static String basePath;

    static final Object NULL = new Object();

    static class Resp {
        int status;
        Map<String, String> headers = new HashMap<>();
        byte[] body;
        String bodyStr() { return new String(body, StandardCharsets.UTF_8); }
    }

    static class Fatal extends Exception {
        Fatal(String m) { super(m); }
    }

    static int indexOf(byte[] a, int len, byte[] pat, int from) {
        outer:
        for (int i = from; i + pat.length <= len; i++) {
            for (int j = 0; j < pat.length; j++) if (a[i + j] != pat[j]) continue outer;
            return i;
        }
        return -1;
    }

    static Resp request(String method, String path, List<String[]> hdrs, byte[] body) throws IOException {
        try (Socket s = new Socket(host, port)) {
            StringBuilder sb = new StringBuilder();
            sb.append(method).append(' ').append(path).append(" HTTP/1.1\r\n");
            sb.append("Host: ").append(host).append(':').append(port).append("\r\n");
            for (String[] h : hdrs) sb.append(h[0]).append(": ").append(h[1]).append("\r\n");
            if (body != null) sb.append("Content-Length: ").append(body.length).append("\r\n");
            sb.append("Connection: close\r\n\r\n");
            OutputStream os = s.getOutputStream();
            os.write(sb.toString().getBytes(StandardCharsets.UTF_8));
            if (body != null) os.write(body);
            os.flush();
            InputStream in = s.getInputStream();
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) {
                bo.write(buf, 0, n);
                byte[] all = bo.toByteArray();
                Resp r = tryParse(all, all.length, false);
                if (r != null) return r;
            }
            byte[] all = bo.toByteArray();
            Resp r = tryParse(all, all.length, true);
            if (r == null) throw new IOException("bad response");
            return r;
        }
    }

    static Resp tryParse(byte[] a, int len, boolean eof) {
        int he = indexOf(a, len, "\r\n\r\n".getBytes(), 0);
        if (he < 0) return null;
        String head = new String(a, 0, he, StandardCharsets.ISO_8859_1);
        String[] lines = head.split("\r\n");
        Resp r = new Resp();
        String[] sl = lines[0].split(" ", 3);
        r.status = Integer.parseInt(sl[1].trim());
        for (int i = 1; i < lines.length; i++) {
            int c = lines[i].indexOf(':');
            if (c < 0) continue;
            r.headers.put(lines[i].substring(0, c).trim().toLowerCase(Locale.ROOT), lines[i].substring(c + 1).trim());
        }
        int p = he + 4;
        String te = r.headers.get("transfer-encoding");
        if (te != null && te.toLowerCase(Locale.ROOT).contains("chunked")) {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            while (true) {
                int le = indexOf(a, len, "\r\n".getBytes(), p);
                if (le < 0) return eof ? finish(r, bo) : null;
                String szs = new String(a, p, le - p, StandardCharsets.ISO_8859_1);
                int semi = szs.indexOf(';');
                if (semi >= 0) szs = szs.substring(0, semi);
                szs = szs.trim();
                int sz = Integer.parseInt(szs, 16);
                p = le + 2;
                if (sz == 0) {
                    // trailers
                    while (true) {
                        int te2 = indexOf(a, len, "\r\n".getBytes(), p);
                        if (te2 < 0) return eof ? finish(r, bo) : null;
                        if (te2 == p) return finish(r, bo);
                        p = te2 + 2;
                    }
                }
                if (p + sz > len) {
                    if (!eof) return null;
                    bo.write(a, p, len - p);
                    return finish(r, bo);
                }
                bo.write(a, p, sz);
                p += sz;
                if (p + 2 > len) return eof ? finish(r, bo) : null;
                p += 2;
            }
        }
        String cl = r.headers.get("content-length");
        if (cl != null) {
            int n = Integer.parseInt(cl.trim());
            if (len - p < n) {
                if (!eof) return null;
                n = len - p;
            }
            r.body = Arrays.copyOfRange(a, p, p + n);
            return r;
        }
        if (!eof) return null;
        r.body = Arrays.copyOfRange(a, p, len);
        return r;
    }

    static Resp finish(Resp r, ByteArrayOutputStream bo) {
        r.body = bo.toByteArray();
        return r;
    }

    // ---- JSON ----
    static class JP {
        String s;
        int i;
        JP(String s) { this.s = s; }
        void ws() { while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) i++; }
        Object val() {
            ws();
            char c = s.charAt(i);
            if (c == '{') {
                i++;
                Map<String, Object> m = new LinkedHashMap<>();
                ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws();
                    String k = str();
                    ws();
                    i++; // :
                    Object v = val();
                    m.put(k, v);
                    ws();
                    if (s.charAt(i) == ',') { i++; continue; }
                    i++; // }
                    return m;
                }
            } else if (c == '[') {
                i++;
                List<Object> l = new ArrayList<>();
                ws();
                if (s.charAt(i) == ']') { i++; return l; }
                while (true) {
                    l.add(val());
                    ws();
                    if (s.charAt(i) == ',') { i++; continue; }
                    i++;
                    return l;
                }
            } else if (c == '"') {
                return str();
            } else if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            else if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            else if (s.startsWith("null", i)) { i += 4; return NULL; }
            else {
                int st = i;
                while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
                return s.substring(st, i);
            }
        }
        String str() {
            i++; // opening quote
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') break;
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n': sb.append('\n'); break;
                        case 't': sb.append('\t'); break;
                        case 'r': sb.append('\r'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'u':
                            sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                            i += 4;
                            break;
                        default: sb.append(e);
                    }
                } else sb.append(c);
            }
            return sb.toString();
        }
    }

    static String formEnc(String v) {
        StringBuilder sb = new StringBuilder();
        for (byte b : v.getBytes(StandardCharsets.UTF_8)) {
            int c = b & 0xff;
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '*')
                sb.append((char) c);
            else if (c == ' ') sb.append('+');
            else sb.append(String.format("%%%02X", c));
        }
        return sb.toString();
    }

    static String pctEnc(String v) {
        StringBuilder sb = new StringBuilder();
        for (byte b : v.getBytes(StandardCharsets.UTF_8)) {
            int c = b & 0xff;
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '~')
                sb.append((char) c);
            else sb.append(String.format("%%%02X", c));
        }
        return sb.toString();
    }

    static String clientId, clientSecret, scope;
    static int pageSize;

    static String getToken() throws IOException, Fatal {
        String basic = Base64.getEncoder().encodeToString((formEnc(clientId) + ":" + formEnc(clientSecret)).getBytes(StandardCharsets.UTF_8));
        List<String[]> h = new ArrayList<>();
        h.add(new String[]{"Authorization", "Basic " + basic});
        h.add(new String[]{"Content-Type", "application/x-www-form-urlencoded"});
        h.add(new String[]{"Accept", "application/json"});
        byte[] body = ("grant_type=client_credentials&scope=" + formEnc(scope)).getBytes(StandardCharsets.UTF_8);
        Resp r = request("POST", basePath + "/oauth/token", h, body);
        if (r.status != 200) throw new Fatal("ERROR " + r.status + " " + r.bodyStr());
        @SuppressWarnings("unchecked")
        Map<String, Object> m = (Map<String, Object>) new JP(r.bodyStr()).val();
        return (String) m.get("access_token");
    }

    static long parseCents(String a) {
        boolean neg = a.startsWith("-");
        if (neg) a = a.substring(1);
        String ip = a, fp = "";
        int d = a.indexOf('.');
        if (d >= 0) { ip = a.substring(0, d); fp = a.substring(d + 1); }
        while (fp.length() < 2) fp += "0";
        long v = Long.parseLong(ip) * 100 + Long.parseLong(fp);
        return neg ? -v : v;
    }

    static String fmt(long c) {
        boolean neg = c < 0;
        long a = Math.abs(c);
        return (neg ? "-" : "") + (a / 100) + "." + String.format("%02d", a % 100);
    }

    static int cmpCp(String a, String b) {
        int i = 0, j = 0;
        while (i < a.length() && j < b.length()) {
            int ca = a.codePointAt(i), cb = b.codePointAt(j);
            if (ca != cb) return Integer.compare(ca, cb);
            i += Character.charCount(ca);
            j += Character.charCount(cb);
        }
        return Integer.compare(a.length() - i, b.length() - j);
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        String cfg = new String(Files.readAllBytes(Paths.get(args[0], "client.json")), StandardCharsets.UTF_8);
        Map<String, Object> cm = (Map<String, Object>) new JP(cfg).val();
        clientId = (String) cm.get("client_id");
        clientSecret = (String) cm.get("client_secret");
        scope = (String) cm.get("scope");
        pageSize = (int) Double.parseDouble((String) cm.get("page_size"));
        URI u = new URI(args[1]);
        host = u.getHost();
        port = u.getPort() < 0 ? 80 : u.getPort();
        basePath = u.getRawPath() == null ? "" : u.getRawPath();
        if (basePath.endsWith("/")) basePath = basePath.substring(0, basePath.length() - 1);

        Map<String, Long> totals = new HashMap<>();
        Map<String, String[]> keys = new HashMap<>();
        TreeMap<String, Integer> statuses = new TreeMap<>();
        long nOrders = 0, pages = 0, retries = 0, reauth = 0;
        try {
            String token = getToken();
            boolean fresh = true;
            String cursor = null;
            while (true) {
                String path = basePath + "/orders?limit=" + pageSize + (cursor != null ? "&cursor=" + pctEnc(cursor) : "");
                List<String[]> h = new ArrayList<>();
                h.add(new String[]{"Authorization", "Bearer " + token});
                h.add(new String[]{"Accept", "application/json"});
                Resp r = request("GET", path, h, null);
                boolean wasFresh = fresh;
                fresh = false;
                if (r.status == 200) {
                    pages++;
                    Map<String, Object> m = (Map<String, Object>) new JP(r.bodyStr()).val();
                    Object data = m.get("data");
                    if (data instanceof List) {
                        for (Object o : (List<Object>) data) {
                            Map<String, Object> ord = (Map<String, Object>) o;
                            nOrders++;
                            String st = (String) ord.get("status");
                            statuses.merge(st, 1, Integer::sum);
                            if (!"cancelled".equals(st)) {
                                String name = (String) ((Map<String, Object>) ord.get("customer")).get("name");
                                String cur = (String) ord.get("currency");
                                String k = name + "\u0000" + cur;
                                keys.put(k, new String[]{name, cur});
                                totals.merge(k, parseCents((String) ord.get("amount")), Long::sum);
                            }
                        }
                    }
                    Object nc = m.get("next_cursor");
                    if (nc == null || nc == NULL) break;
                    cursor = (String) nc;
                } else if (r.status == 429) {
                    retries++;
                    String ra = r.headers.get("retry-after");
                    long secs = 1;
                    try { secs = Long.parseLong(ra.trim()); } catch (Exception e) { }
                    Thread.sleep(secs * 1000 + 50);
                } else if (r.status == 503) {
                    retries++;
                    Thread.sleep(100);
                } else if (r.status == 401 && !wasFresh) {
                    token = getToken();
                    reauth++;
                    fresh = true;
                } else {
                    throw new Fatal("ERROR " + r.status + " " + r.bodyStr());
                }
            }
        } catch (Fatal f) {
            out.println(f.getMessage());
            out.flush();
            System.exit(0);
        }
        List<String[]> ks = new ArrayList<>(keys.values());
        ks.sort((x, y) -> {
            int c = cmpCp(x[0], y[0]);
            if (c != 0) return c;
            return cmpCp(x[1], y[1]);
        });
        StringBuilder sb = new StringBuilder();
        for (String[] k : ks) {
            sb.append("total ").append(k[1]).append(' ').append(fmt(totals.get(k[0] + "\u0000" + k[1]))).append(' ').append(k[0]).append('\n');
        }
        for (Map.Entry<String, Integer> e : statuses.entrySet())
            sb.append("status ").append(e.getKey()).append(' ').append(e.getValue()).append('\n');
        sb.append("orders ").append(nOrders).append(" pages ").append(pages).append(" retries ").append(retries).append(" reauth ").append(reauth).append('\n');
        out.print(sb);
        out.flush();
    }
}
