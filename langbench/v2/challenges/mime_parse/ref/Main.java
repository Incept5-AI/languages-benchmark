import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static boolean isWs(int c) { return c == ' ' || c == '\t'; }

    static String trimWs(String s) {
        int a = 0, b = s.length();
        while (a < b && isWs(s.charAt(a))) a++;
        while (b > a && isWs(s.charAt(b - 1))) b--;
        return s.substring(a, b);
    }

    static int find(byte[] h, int hs, int he, byte[] n, int from) {
        outer:
        for (int i = from; i + n.length <= he; i++) {
            for (int k = 0; k < n.length; k++) if (h[i + k] != n[k]) continue outer;
            return i;
        }
        return -1;
    }

    static int hexv(int c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }

    static int b64val(int c) {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    }

    static byte[] b64decode(byte[] d, int s, int e) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int acc = 0, n = 0;
        for (int i = s; i < e; i++) {
            int c = d[i] & 0xff;
            if (c == '=') break;
            int v = b64val(c);
            if (v < 0) continue;
            acc = (acc << 6) | v;
            n++;
            if (n == 4) {
                out.write((acc >> 16) & 255); out.write((acc >> 8) & 255); out.write(acc & 255);
                acc = 0; n = 0;
            }
        }
        if (n == 2) out.write((acc >> 4) & 255);
        else if (n == 3) { out.write((acc >> 10) & 255); out.write((acc >> 2) & 255); }
        return out.toByteArray();
    }

    static byte[] qpdecode(byte[] d, int s, int end, byte[] nl) {
        ByteArrayOutputStream j = new ByteArrayOutputStream();
        int pos = s;
        while (true) {
            int i = find(d, s, end, nl, pos);
            boolean has = i >= 0;
            int le = has ? i : end;
            int e = le;
            while (e > pos && isWs(d[e - 1])) e--;
            if (e > pos && d[e - 1] == '=') {
                j.write(d, pos, e - 1 - pos);
            } else {
                j.write(d, pos, e - pos);
                if (has) j.write(nl, 0, nl.length);
            }
            if (!has) break;
            pos = i + nl.length;
        }
        byte[] jb = j.toByteArray();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int i = 0;
        while (i < jb.length) {
            int c = jb[i] & 0xff;
            if (c == '=' && i + 2 < jb.length) {
                int a = hexv(jb[i + 1]), b = hexv(jb[i + 2]);
                if (a >= 0 && b >= 0) { out.write(a * 16 + b); i += 3; continue; }
            }
            out.write(c);
            i++;
        }
        return out.toByteArray();
    }

    static final int[] CP1252 = {
        0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
        0x0152, 0x008D, 0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
        0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178};

    static String decodeCharset(byte[] b, String cs0) {
        String cs = cs0.toLowerCase(Locale.ROOT);
        if (cs.equals("iso-8859-1") || cs.equals("latin1") || cs.equals("iso8859-1")) {
            StringBuilder sb = new StringBuilder();
            for (byte x : b) sb.append((char) (x & 0xff));
            return sb.toString();
        }
        if (cs.equals("windows-1252") || cs.equals("cp1252")) {
            StringBuilder sb = new StringBuilder();
            for (byte x : b) {
                int v = x & 0xff;
                sb.append((char) (v >= 0x80 && v < 0xa0 ? CP1252[v - 0x80] : v));
            }
            return sb.toString();
        }
        return new String(b, StandardCharsets.UTF_8);
    }

    static String stripLang(String cs) {
        int i = cs.indexOf('*');
        return i >= 0 ? cs.substring(0, i) : cs;
    }

    static class Seg {
        boolean word; String text; String cs; byte[] bytes;
        Seg(boolean w, String t, String c, byte[] b) { word = w; text = t; cs = c; bytes = b; }
    }

    static String decode2047(String s) {
        byte[] b = s.getBytes(StandardCharsets.ISO_8859_1);
        List<Seg> segs = new ArrayList<>();
        int textStart = 0, i = 0;
        while (i + 1 < b.length) {
            if (b[i] == '=' && b[i + 1] == '?') {
                int j = i + 2;
                while (j < b.length && b[j] != '?' && !isWs(b[j])) j++;
                if (j > i + 2 && j < b.length && b[j] == '?') {
                    String cs = s.substring(i + 2, j);
                    if (j + 2 < b.length && "BbQq".indexOf(b[j + 1]) >= 0 && b[j + 2] == '?') {
                        int enc = b[j + 1];
                        int ts = j + 3, k = ts;
                        while (k < b.length && b[k] != '?' && !isWs(b[k])) k++;
                        if (k + 1 < b.length && b[k] == '?' && b[k + 1] == '=') {
                            byte[] bytes;
                            if (enc == 'B' || enc == 'b') bytes = b64decode(b, ts, k);
                            else {
                                ByteArrayOutputStream o = new ByteArrayOutputStream();
                                int p = ts;
                                while (p < k) {
                                    int c = b[p] & 0xff;
                                    if (c == '_') o.write(0x20);
                                    else if (c == '=' && p + 2 < k && hexv(b[p + 1]) >= 0 && hexv(b[p + 2]) >= 0) {
                                        o.write(hexv(b[p + 1]) * 16 + hexv(b[p + 2]));
                                        p += 3;
                                        continue;
                                    } else o.write(c);
                                    p++;
                                }
                                bytes = o.toByteArray();
                            }
                            if (textStart < i) segs.add(new Seg(false, s.substring(textStart, i), "", null));
                            segs.add(new Seg(true, "", stripLang(cs).toLowerCase(Locale.ROOT), bytes));
                            i = k + 2;
                            textStart = i;
                            continue;
                        }
                    }
                }
            }
            i++;
        }
        if (textStart < b.length) segs.add(new Seg(false, s.substring(textStart), "", null));
        List<Seg> cleaned = new ArrayList<>();
        boolean prevWord = false;
        for (int idx = 0; idx < segs.size(); idx++) {
            Seg sg = segs.get(idx);
            boolean drop = !sg.word && prevWord && idx + 1 < segs.size() && segs.get(idx + 1).word
                && sg.text.chars().allMatch(c -> c == ' ' || c == '\t');
            if (!drop) { prevWord = sg.word; cleaned.add(sg); }
        }
        StringBuilder out = new StringBuilder();
        int k = 0;
        while (k < cleaned.size()) {
            Seg sg = cleaned.get(k);
            if (!sg.word) { out.append(sg.text); k++; continue; }
            String cs = sg.cs;
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            while (k < cleaned.size() && cleaned.get(k).word && cleaned.get(k).cs.equals(cs)) {
                bytes.write(cleaned.get(k).bytes, 0, cleaned.get(k).bytes.length);
                k++;
            }
            out.append(decodeCharset(bytes.toByteArray(), cs));
        }
        return out.toString();
    }

    static class Ctype {
        String main;
        List<String[]> params = new ArrayList<>();
    }

    static Ctype parseCtype(String v, boolean hasSub) {
        Ctype ct = new Ctype();
        int i = 0, n = v.length();
        while (i < n && isWs(v.charAt(i))) i++;
        int st = i;
        while (i < n && v.charAt(i) != '/' && v.charAt(i) != ';' && !isWs(v.charAt(i))) i++;
        String main = v.substring(st, i).toLowerCase(Locale.ROOT);
        if (hasSub) {
            while (i < n && isWs(v.charAt(i))) i++;
            if (i < n && v.charAt(i) == '/') {
                i++;
                while (i < n && isWs(v.charAt(i))) i++;
                int s2 = i;
                while (i < n && v.charAt(i) != ';' && !isWs(v.charAt(i))) i++;
                main = main + "/" + v.substring(s2, i).toLowerCase(Locale.ROOT);
            }
        }
        ct.main = main;
        while (true) {
            while (i < n && isWs(v.charAt(i))) i++;
            if (i >= n) break;
            if (v.charAt(i) != ';') { i++; continue; }
            i++;
            while (i < n && isWs(v.charAt(i))) i++;
            int a = i;
            while (i < n && v.charAt(i) != '=' && v.charAt(i) != ';' && !isWs(v.charAt(i))) i++;
            String attr = v.substring(a, i).toLowerCase(Locale.ROOT);
            while (i < n && isWs(v.charAt(i))) i++;
            if (i < n && v.charAt(i) == '=') {
                i++;
                while (i < n && isWs(v.charAt(i))) i++;
                StringBuilder val = new StringBuilder();
                if (i < n && v.charAt(i) == '"') {
                    i++;
                    while (i < n && v.charAt(i) != '"') {
                        if (v.charAt(i) == '\\' && i + 1 < n) i++;
                        val.append(v.charAt(i));
                        i++;
                    }
                    if (i < n) i++;
                } else {
                    while (i < n && v.charAt(i) != ';' && !isWs(v.charAt(i))) { val.append(v.charAt(i)); i++; }
                }
                if (!attr.isEmpty()) ct.params.add(new String[]{attr, val.toString()});
            }
        }
        return ct;
    }

    static String getParam(Ctype c, String n) {
        for (String[] p : c.params) if (p[0].equals(n)) return p[1];
        return null;
    }

    static void pctDecode(String s, ByteArrayOutputStream o) {
        byte[] b = s.getBytes(StandardCharsets.ISO_8859_1);
        int i = 0;
        while (i < b.length) {
            if (b[i] == '%' && i + 2 < b.length && hexv(b[i + 1]) >= 0 && hexv(b[i + 2]) >= 0) {
                o.write(hexv(b[i + 1]) * 16 + hexv(b[i + 2]));
                i += 3;
            } else { o.write(b[i]); i++; }
        }
    }

    static String[] splitCs(String v) {
        int p1 = v.indexOf('\'');
        if (p1 >= 0) {
            int p2 = v.indexOf('\'', p1 + 1);
            if (p2 >= 0) return new String[]{v.substring(0, p1), v.substring(p2 + 1)};
        }
        return new String[]{"", v};
    }

    static String rfc2231(Ctype c, String a) {
        String single = getParam(c, a + "*");
        if (single != null) {
            String[] sp = splitCs(single);
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            pctDecode(sp[1], o);
            return decodeCharset(o.toByteArray(), sp[0].isEmpty() ? "us-ascii" : stripLang(sp[0]));
        }
        String pre = a + "*";
        List<Object[]> segs = new ArrayList<>();
        for (String[] p : c.params) {
            if (!p[0].startsWith(pre)) continue;
            String rest = p[0].substring(pre.length());
            boolean enc = false;
            if (rest.endsWith("*")) { enc = true; rest = rest.substring(0, rest.length() - 1); }
            if (rest.matches("[0-9]+")) segs.add(new Object[]{new java.math.BigInteger(rest), enc, p[1]});
        }
        if (segs.isEmpty()) return null;
        segs.sort((x, y) -> ((java.math.BigInteger) x[0]).compareTo((java.math.BigInteger) y[0]));
        String cs = "";
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        boolean firstEnc = true;
        for (Object[] sg : segs) {
            boolean enc = (Boolean) sg[1];
            String v = (String) sg[2];
            if (enc) {
                if (firstEnc) {
                    firstEnc = false;
                    String[] sp = splitCs(v);
                    cs = sp[0];
                    pctDecode(sp[1], o);
                } else pctDecode(v, o);
            } else {
                byte[] vb = v.getBytes(StandardCharsets.ISO_8859_1);
                o.write(vb, 0, vb.length);
            }
        }
        return decodeCharset(o.toByteArray(), cs.isEmpty() ? "us-ascii" : stripLang(cs));
    }

    static long crc32(byte[] d) {
        java.util.zip.CRC32 c = new java.util.zip.CRC32();
        c.update(d);
        return c.getValue();
    }

    static String quote(String s) {
        StringBuilder o = new StringBuilder("\"");
        s.codePoints().forEach(cp -> {
            if (cp == '\\') o.append("\\\\");
            else if (cp == '"') o.append("\\\"");
            else if (cp == '\n') o.append("\\n");
            else if (cp == '\t') o.append("\\t");
            else if (cp < 0x20 || (cp >= 0x7f && cp <= 0x9f)) o.append(String.format("\\u%04x", cp));
            else o.appendCodePoint(cp);
        });
        return o.append('"').toString();
    }

    static class Entity {
        Map<String, String> headers = new HashMap<>();
        int bs, be;
    }

    static Entity parseEntity(byte[] d, int s, int end, byte[] nl) {
        Entity e = new Entity();
        List<String> lines = new ArrayList<>();
        int pos = s;
        e.bs = end; e.be = end;
        while (pos < end) {
            int i = find(d, s, end, nl, pos);
            int le = i >= 0 ? i : end;
            int next = i >= 0 ? i + nl.length : end;
            if (le == pos) { e.bs = Math.min(next, end); break; }
            String line = new String(d, pos, le - pos, StandardCharsets.ISO_8859_1);
            if ((line.charAt(0) == ' ' || line.charAt(0) == '\t') && !lines.isEmpty())
                lines.set(lines.size() - 1, lines.get(lines.size() - 1) + line);
            else lines.add(line);
            pos = next;
        }
        for (String l : lines) {
            int c = l.indexOf(':');
            if (c < 0) continue;
            String name = trimWs(l.substring(0, c)).toLowerCase(Locale.ROOT);
            e.headers.putIfAbsent(name, trimWs(l.substring(c + 1)));
        }
        return e;
    }

    static boolean allWs(byte[] d, int s, int e) {
        for (int i = s; i < e; i++) if (!isWs(d[i])) return false;
        return true;
    }

    static List<int[]> splitMultipart(byte[] d, int bs, int be, String bnd, byte[] nl) {
        byte[] db = ("--" + bnd).getBytes(StandardCharsets.ISO_8859_1);
        List<int[]> parts = new ArrayList<>();
        int pos = bs, cur = -1;
        boolean closed = false;
        while (true) {
            int i = find(d, bs, be, nl, pos);
            boolean has = i >= 0;
            int le = has ? i : be;
            int next = has ? i + nl.length : be;
            int kind = 0;
            if (le - pos >= db.length && Arrays.equals(d, pos, pos + db.length, db, 0, db.length)) {
                int r = pos + db.length;
                if (allWs(d, r, le)) kind = 1;
                else if (le - r >= 2 && d[r] == '-' && d[r + 1] == '-' && allWs(d, r + 2, le)) kind = 2;
            }
            if (kind != 0) {
                if (cur >= 0) parts.add(new int[]{cur, Math.max(cur, pos - nl.length)});
                if (kind == 2) { closed = true; break; }
                cur = next;
            }
            if (!has) break;
            pos = next;
        }
        if (!closed && cur >= 0 && cur <= be) parts.add(new int[]{cur, be});
        return parts;
    }

    static void render(byte[] d, int s, int end, byte[] nl, int depth, StringBuilder out) {
        Entity e = parseEntity(d, s, end, nl);
        String ind = "  ".repeat(depth);
        String ctv = e.headers.get("content-type");
        Ctype ct;
        if (ctv != null) ct = parseCtype(ctv, true);
        else { ct = new Ctype(); ct.main = "text/plain"; }
        String cte = e.headers.getOrDefault("content-transfer-encoding", "7bit").toLowerCase(Locale.ROOT);
        String cdv = e.headers.get("content-disposition");
        Ctype cd = cdv != null ? parseCtype(cdv, false) : null;
        int sl = ct.main.indexOf('/');
        String ty = sl >= 0 ? ct.main.substring(0, sl) : ct.main;
        if (ty.equals("multipart")) {
            String b = getParam(ct, "boundary");
            List<int[]> parts = splitMultipart(d, e.bs, e.be, b == null ? "" : b, nl);
            out.append(ind).append(ct.main).append(" parts=").append(parts.size()).append('\n');
            for (int[] p : parts) render(d, p[0], p[1], nl, depth + 1, out);
            return;
        }
        byte[] raw;
        if (cte.equals("base64")) raw = b64decode(d, e.bs, e.be);
        else if (cte.equals("quoted-printable")) raw = qpdecode(d, e.bs, e.be, nl);
        else raw = Arrays.copyOfRange(d, e.bs, e.be);
        String disp = cd != null ? cd.main : null;
        if (ty.equals("text") && !"attachment".equals(disp)) {
            String cs = getParam(ct, "charset");
            cs = (cs == null ? "us-ascii" : cs).toLowerCase(Locale.ROOT);
            String txt = decodeCharset(raw, cs).replace("\r\n", "\n").replace('\r', '\n');
            out.append(ind).append(ct.main).append(" charset=").append(cs).append(" cte=").append(cte)
                .append(" chars=").append(txt.codePointCount(0, txt.length())).append('\n');
            out.append(ind).append("  ").append(quote(txt)).append('\n');
        } else {
            String name = null;
            if (cd != null) {
                name = rfc2231(cd, "filename");
                if (name == null) { String f = getParam(cd, "filename"); if (f != null) name = decode2047(f); }
            }
            if (name == null) name = rfc2231(ct, "name");
            if (name == null) { String f = getParam(ct, "name"); if (f != null) name = decode2047(f); }
            out.append(ind).append(ct.main).append(" cte=").append(cte)
                .append(" disposition=").append(disp == null ? "-" : disp)
                .append(" name=").append(name == null ? "-" : quote(name))
                .append(" size=").append(raw.length)
                .append(" crc32=").append(String.format("%08x", crc32(raw))).append('\n');
        }
    }

    public static void main(String[] args) throws IOException {
        File dir = new File(args[0]);
        List<String> names = new ArrayList<>();
        for (String n : dir.list()) if (n.endsWith(".eml")) names.add(n);
        names.sort((a, b) -> Arrays.compareUnsigned(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8)));
        StringBuilder out = new StringBuilder();
        for (int idx = 0; idx < names.size(); idx++) {
            if (idx > 0) out.append('\n');
            String n = names.get(idx);
            byte[] d = Files.readAllBytes(new File(dir, n).toPath());
            int p = -1;
            for (int i = 0; i < d.length; i++) if (d[i] == '\n') { p = i; break; }
            byte[] nl = (p > 0 && d[p - 1] == '\r') ? new byte[]{13, 10} : new byte[]{10};
            Entity e = parseEntity(d, 0, d.length, nl);
            out.append("== ").append(n).append('\n');
            String[][] hs = {{"from", "From"}, {"subject", "Subject"}};
            for (String[] h : hs) {
                String v = e.headers.get(h[0]);
                out.append(h[1]).append(": ").append(v == null ? "-" : quote(decode2047(v))).append('\n');
            }
            render(d, 0, d.length, nl, 0, out);
        }
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(out);
        ps.flush();
    }
}
