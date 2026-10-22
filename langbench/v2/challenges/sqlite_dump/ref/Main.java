import java.io.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.util.*;

public class Main {
    static byte[] db;
    static int pageSize, usable;

    static int u8(int off) { return db[off] & 0xFF; }
    static int be16(int off) { return (u8(off) << 8) | u8(off + 1); }
    static long be32(int off) { return ((long) u8(off) << 24) | (u8(off + 1) << 16) | (u8(off + 2) << 8) | u8(off + 3); }

    static int vlen;
    static long varint(byte[] p, int off) {
        long v = 0;
        for (int i = 0; i < 8; i++) {
            int b = p[off + i] & 0xFF;
            v = (v << 7) | (b & 0x7F);
            if ((b & 0x80) == 0) { vlen = i + 1; return v; }
        }
        v = (v << 8) | (p[off + 8] & 0xFF);
        vlen = 9;
        return v;
    }

    static final class Row { long rowid; byte[] payload; Row(long r, byte[] p) { rowid = r; payload = p; } }

    static int pageOff(long n) { return (int) ((n - 1) * pageSize); }

    static void walk(long pg, List<Row> rows, int depth) {
        if (depth > 64) throw new RuntimeException("too deep");
        int base = pageOff(pg);
        int h = base + (pg == 1 ? 100 : 0);
        int type = u8(h);
        int ncell = be16(h + 3);
        if (type == 0x05) {
            for (int i = 0; i < ncell; i++) {
                int c = base + be16(h + 12 + 2 * i);
                walk(be32(c), rows, depth + 1);
            }
            walk(be32(h + 8), rows, depth + 1);
        } else if (type == 0x0D) {
            for (int i = 0; i < ncell; i++) {
                int c = base + be16(h + 8 + 2 * i);
                long plenL = varint(db, c); int l1 = vlen;
                long rowid = varint(db, c + l1); int l2 = vlen;
                int start = c + l1 + l2;
                int plen = (int) plenL;
                int x = usable - 35;
                byte[] out = new byte[plen];
                if (plen <= x) {
                    System.arraycopy(db, start, out, 0, plen);
                } else {
                    int m = ((usable - 12) * 32 / 255) - 23;
                    int k = m + ((plen - m) % (usable - 4));
                    int local = k <= x ? k : m;
                    System.arraycopy(db, start, out, 0, local);
                    long ov = be32(start + local);
                    int got = local;
                    while (got < plen && ov != 0) {
                        int op = pageOff(ov);
                        int take = Math.min(plen - got, usable - 4);
                        System.arraycopy(db, op + 4, out, got, take);
                        got += take;
                        ov = be32(op);
                    }
                }
                rows.add(new Row(rowid, out));
            }
        } else {
            throw new RuntimeException("unexpected page type " + type + " at " + pg);
        }
    }

    // value: kind 0 null,1 int,2 real,3 text,4 blob
    static final class Val { int kind; long i; double d; byte[] s; int off, n; }

    static List<Val> decode(byte[] p) {
        long hlen = varint(p, 0);
        int hp = vlen, dp = (int) hlen;
        List<Val> vals = new ArrayList<>();
        while (hp < hlen) {
            long st = varint(p, hp); hp += vlen;
            Val v = new Val();
            if (st == 0) v.kind = 0;
            else if (st >= 1 && st <= 6) {
                int n = new int[]{0, 1, 2, 3, 4, 6, 8}[(int) st];
                long u = 0;
                for (int i = 0; i < n; i++) u = (u << 8) | (p[dp + i] & 0xFF);
                if (n < 8 && ((u >>> (n * 8 - 1)) & 1) == 1) u |= -1L << (n * 8);
                v.kind = 1; v.i = u; dp += n;
            } else if (st == 7) {
                long u = 0;
                for (int i = 0; i < 8; i++) u = (u << 8) | (p[dp + i] & 0xFF);
                v.kind = 2; v.d = Double.longBitsToDouble(u); dp += 8;
            } else if (st == 8) { v.kind = 1; v.i = 0; }
            else if (st == 9) { v.kind = 1; v.i = 1; }
            else if (st >= 12) {
                int n = (int) ((st - 12) / 2);
                v.kind = (st & 1) == 1 ? 3 : 4; v.s = p; v.off = dp; v.n = n; dp += n;
            }
            vals.add(v);
        }
        return vals;
    }

    static int[] crcTable = new int[256];
    static {
        for (int i = 0; i < 256; i++) {
            int c = i;
            for (int k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
            crcTable[i] = c;
        }
    }
    static int crc32(byte[] s, int off, int n) {
        int c = 0xFFFFFFFF;
        for (int i = 0; i < n; i++) c = crcTable[(c ^ s[off + i]) & 0xFF] ^ (c >>> 8);
        return ~c;
    }

    static void ascii(ByteArrayOutputStream o, String s) {
        byte[] b = s.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        o.write(b, 0, b.length);
    }

    static String fmtReal(double d) {
        String s = new BigDecimal(d).toPlainString();
        if (d == 0 && 1 / d < 0) s = "-0";
        if (s.indexOf('.') < 0) return s + ".0";
        int n = s.length();
        while (s.charAt(n - 1) == '0') n--;
        if (s.charAt(n - 1) == '.') n++;
        return s.substring(0, n);
    }

    static void fmtVal(ByteArrayOutputStream o, Val v) {
        switch (v.kind) {
            case 0 -> ascii(o, "NULL");
            case 1 -> ascii(o, Long.toString(v.i));
            case 2 -> ascii(o, fmtReal(v.d));
            case 3 -> {
                if (v.n > 100) ascii(o, String.format("<TEXT len=%d crc32=%08x>", v.n, crc32(v.s, v.off, v.n)));
                else {
                    o.write('"');
                    for (int i = 0; i < v.n; i++) {
                        int c = v.s[v.off + i] & 0xFF;
                        if (c == '\\') ascii(o, "\\\\");
                        else if (c == '"') ascii(o, "\\\"");
                        else if (c == '\n') ascii(o, "\\n");
                        else if (c == '\r') ascii(o, "\\r");
                        else if (c == '\t') ascii(o, "\\t");
                        else if (c < 0x20 || c == 0x7F) ascii(o, String.format("\\x%02X", c));
                        else o.write(c);
                    }
                    o.write('"');
                }
            }
            case 4 -> {
                if (v.n > 100) ascii(o, String.format("<BLOB len=%d crc32=%08x>", v.n, crc32(v.s, v.off, v.n)));
                else {
                    StringBuilder sb = new StringBuilder("X'");
                    for (int i = 0; i < v.n; i++) sb.append(String.format("%02X", v.s[v.off + i] & 0xFF));
                    sb.append('\'');
                    ascii(o, sb.toString());
                }
            }
            default -> ascii(o, "NULL");
        }
    }

    static boolean isId(char c) { return Character.isLetterOrDigit(c) || c == '_' || c == '$' || c >= 0x80; }

    static final class Col { String name; boolean alias; }

    static List<Col> parseCols(String sql) {
        int n = sql.length(), i = 0;
        while (i < n && sql.charAt(i) != '(') {
            if (sql.charAt(i) == '"') { i++; while (i < n && sql.charAt(i) != '"') i++; }
            i++;
        }
        int start = i + 1, depth = 1, end = n;
        for (int j = start; j < n; j++) {
            char c = sql.charAt(j);
            if (c == '"') { j++; while (j < n && sql.charAt(j) != '"') j++; continue; }
            if (c == '(') depth++;
            else if (c == ')') { depth--; if (depth == 0) { end = j; break; } }
        }
        List<String> defs = new ArrayList<>();
        int ds = start; depth = 0;
        for (int j = start; j <= end; j++) {
            char c = j == end ? ',' : sql.charAt(j);
            if (c == '"' && j < end) { j++; while (j < end && sql.charAt(j) != '"') j++; continue; }
            if (c == '(') depth++;
            else if (c == ')') depth--;
            else if (c == ',' && depth == 0) { defs.add(sql.substring(ds, j)); ds = j + 1; }
        }
        Set<String> kws = Set.of("CONSTRAINT", "PRIMARY", "NOT", "NULL", "UNIQUE", "CHECK", "DEFAULT", "COLLATE", "REFERENCES", "GENERATED", "AS");
        List<Col> cols = new ArrayList<>();
        for (String d : defs) {
            List<String> toks = new ArrayList<>();
            int k = 0;
            while (k < d.length()) {
                char c = d.charAt(k);
                if (Character.isWhitespace(c)) k++;
                else if (c == '"') {
                    int s = k; k++;
                    while (k < d.length() && d.charAt(k) != '"') k++;
                    k++;
                    toks.add(d.substring(s, Math.min(k, d.length())));
                } else if (isId(c)) {
                    int s = k;
                    while (k < d.length() && isId(d.charAt(k))) k++;
                    toks.add(d.substring(s, k));
                } else { toks.add(String.valueOf(c)); k++; }
            }
            if (toks.isEmpty()) continue;
            Col col = new Col();
            String t0 = toks.get(0);
            col.name = t0.startsWith("\"") ? t0.substring(1, t0.length() - 1) : t0;
            int t = 1, words = 0; boolean isInt = false;
            while (t < toks.size()) {
                String tk = toks.get(t);
                char c = tk.charAt(0);
                if (!(isId(c) || c == '"')) break;
                if (kws.contains(tk.toUpperCase(Locale.ROOT))) break;
                words++;
                if (words == 1 && tk.equalsIgnoreCase("INTEGER")) isInt = true;
                t++;
            }
            boolean paren = t < toks.size() && toks.get(t).equals("(") && words > 0;
            boolean pk = false;
            for (int q = 0; q + 1 < toks.size(); q++)
                if (toks.get(q).equalsIgnoreCase("PRIMARY") && toks.get(q + 1).equalsIgnoreCase("KEY")) pk = true;
            col.alias = words == 1 && isInt && !paren && pk;
            cols.add(col);
        }
        return cols;
    }

    static int cmpBytes(byte[] a, byte[] b) { return Arrays.compareUnsigned(a, b); }

    public static void main(String[] args) throws IOException {
        db = Files.readAllBytes(Paths.get(args[0], "db.sqlite"));
        pageSize = be16(16);
        if (pageSize == 1) pageSize = 65536;
        usable = pageSize - u8(20);

        List<Row> schema = new ArrayList<>();
        walk(1, schema, 0);
        List<Object[]> tabs = new ArrayList<>();
        for (Row r : schema) {
            List<Val> v = decode(r.payload);
            if (v.size() < 5) continue;
            Val t = v.get(0), nm = v.get(1);
            if (t.kind != 3 || nm.kind != 3) continue;
            if (!new String(t.s, t.off, t.n, java.nio.charset.StandardCharsets.UTF_8).equals("table")) continue;
            byte[] name = Arrays.copyOfRange(nm.s, nm.off, nm.off + nm.n);
            if (name.length >= 7 && new String(name, 0, 7, java.nio.charset.StandardCharsets.ISO_8859_1).equals("sqlite_")) continue;
            long root = v.get(3).kind == 1 ? v.get(3).i : 0;
            Val sq = v.get(4);
            String sql = sq.kind == 3 ? new String(sq.s, sq.off, sq.n, java.nio.charset.StandardCharsets.UTF_8) : "";
            tabs.add(new Object[]{name, root, sql});
        }
        tabs.sort((a, b) -> cmpBytes((byte[]) a[0], (byte[]) b[0]));

        ByteArrayOutputStream o = new ByteArrayOutputStream(1 << 16);
        for (int ti = 0; ti < tabs.size(); ti++) {
            byte[] name = (byte[]) tabs.get(ti)[0];
            long root = (Long) tabs.get(ti)[1];
            List<Col> cols = parseCols((String) tabs.get(ti)[2]);
            List<Row> rows = new ArrayList<>();
            if (root != 0) walk(root, rows, 0);
            if (ti > 0) o.write('\n');
            ascii(o, "TABLE ");
            o.write(name, 0, name.length);
            ascii(o, " rows=" + rows.size() + "\nCOLUMNS ");
            for (int c = 0; c < cols.size(); c++) { if (c > 0) ascii(o, ", "); ascii(o, cols.get(c).name); }
            o.write('\n');
            for (Row r : rows) {
                List<Val> v = decode(r.payload);
                ascii(o, r.rowid + ": ");
                for (int c = 0; c < cols.size(); c++) {
                    if (c > 0) ascii(o, ", ");
                    if (cols.get(c).alias) ascii(o, Long.toString(r.rowid));
                    else if (c < v.size()) fmtVal(o, v.get(c));
                    else ascii(o, "NULL");
                }
                o.write('\n');
            }
        }
        System.out.write(o.toByteArray());
        System.out.flush();
    }
}
