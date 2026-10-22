import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.zip.CRC32;
import java.util.zip.Deflater;

public class Main {
    // ---- minimal JSON parser ----
    static String src;
    static int pos;

    static void ws() {
        while (pos < src.length() && " \t\r\n".indexOf(src.charAt(pos)) >= 0) pos++;
    }

    static String str() {
        pos++;
        StringBuilder sb = new StringBuilder();
        while (src.charAt(pos) != '"') {
            char c = src.charAt(pos++);
            if (c == '\\') {
                char e = src.charAt(pos++);
                switch (e) {
                    case 'n': sb.append('\n'); break;
                    case 't': sb.append('\t'); break;
                    case 'r': sb.append('\r'); break;
                    case 'b': sb.append('\b'); break;
                    case 'f': sb.append('\f'); break;
                    case 'u': sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16)); pos += 4; break;
                    default: sb.append(e);
                }
            } else sb.append(c);
        }
        pos++;
        return sb.toString();
    }

    static Object val() {
        ws();
        char c = src.charAt(pos);
        if (c == '{') {
            pos++;
            Map<String, Object> m = new HashMap<>();
            ws();
            if (src.charAt(pos) == '}') { pos++; return m; }
            while (true) {
                ws();
                String k = str();
                ws();
                pos++; // ':'
                m.put(k, val());
                ws();
                char d = src.charAt(pos++);
                if (d == '}') break;
            }
            return m;
        }
        if (c == '[') {
            pos++;
            List<Object> a = new ArrayList<>();
            ws();
            if (src.charAt(pos) == ']') { pos++; return a; }
            while (true) {
                a.add(val());
                ws();
                char d = src.charAt(pos++);
                if (d == ']') break;
            }
            return a;
        }
        if (c == '"') return str();
        if (src.startsWith("true", pos)) { pos += 4; return Boolean.TRUE; }
        if (src.startsWith("false", pos)) { pos += 5; return Boolean.FALSE; }
        if (src.startsWith("null", pos)) { pos += 4; return null; }
        int st = pos;
        while (pos < src.length() && "+-.eE0123456789".indexOf(src.charAt(pos)) >= 0) pos++;
        String t = src.substring(st, pos);
        try { return Long.parseLong(t); } catch (NumberFormatException ex) { return (long) Double.parseDouble(t); }
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> obj(Object o) { return (Map<String, Object>) o; }
    @SuppressWarnings("unchecked")
    static List<Object> arr(Object o) { return (List<Object>) o; }
    static long num(Object o) { return o == null ? 0 : (Long) o; }

    static byte[] chunk(String type, byte[] data) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int n = data.length;
        b.write(n >>> 24); b.write(n >>> 16); b.write(n >>> 8); b.write(n);
        byte[] t = type.getBytes(StandardCharsets.US_ASCII);
        b.write(t, 0, 4);
        b.write(data, 0, n);
        CRC32 crc = new CRC32();
        crc.update(t);
        crc.update(data);
        long c = crc.getValue();
        b.write((int) (c >>> 24)); b.write((int) (c >>> 16)); b.write((int) (c >>> 8)); b.write((int) c);
        return b.toByteArray();
    }

    public static void main(String[] args) throws Exception {
        src = new String(Files.readAllBytes(Paths.get(args[0], "scene.json")), StandardCharsets.UTF_8);
        pos = 0;
        Map<String, Object> root = obj(val());
        int W = (int) num(root.get("width")), H = (int) num(root.get("height"));
        List<Object> bg = arr(root.get("background"));
        int[] img = new int[W * H * 3];
        for (int i = 0; i < W * H; i++)
            for (int c = 0; c < 3; c++) img[i * 3 + c] = (int) num(bg.get(c));
        StringBuilder out = new StringBuilder();
        List<Object> shapes = root.get("shapes") == null ? new ArrayList<>() : arr(root.get("shapes"));
        for (int si = 0; si < shapes.size(); si++) {
            Map<String, Object> s = obj(shapes.get(si));
            String type = (String) s.get("type");
            List<Object> col = arr(s.get("color"));
            long[] sc = new long[3];
            for (int c = 0; c < 3; c++) sc[c] = num(col.get(c));
            long A = num(col.get(3));
            int kind;
            long x0, y0, x1, y1;
            long cx = 0, cy = 0, r = 0, lx1 = 0, ly1 = 0, lx2 = 0, ly2 = 0;
            long[] px = new long[3], py = new long[3];
            if (type.equals("rect")) {
                kind = 0;
                long rx = num(s.get("x")), ry = num(s.get("y"));
                x0 = rx; y0 = ry; x1 = rx + num(s.get("w")) - 1; y1 = ry + num(s.get("h")) - 1;
            } else if (type.equals("circle")) {
                kind = 1;
                cx = num(s.get("cx")); cy = num(s.get("cy")); r = num(s.get("r"));
                x0 = cx - r; x1 = cx + r; y0 = cy - r; y1 = cy + r;
            } else if (type.equals("triangle")) {
                kind = 2;
                List<Object> pts = arr(s.get("points"));
                for (int i = 0; i < 3; i++) {
                    List<Object> p = arr(pts.get(i));
                    px[i] = num(p.get(0)); py[i] = num(p.get(1));
                }
                x0 = Math.min(px[0], Math.min(px[1], px[2]));
                x1 = Math.max(px[0], Math.max(px[1], px[2]));
                y0 = Math.min(py[0], Math.min(py[1], py[2]));
                y1 = Math.max(py[0], Math.max(py[1], py[2]));
            } else {
                kind = 3;
                lx1 = num(s.get("x1")); ly1 = num(s.get("y1")); lx2 = num(s.get("x2")); ly2 = num(s.get("y2"));
                r = num(s.get("r"));
                x0 = Math.min(lx1, lx2) - r; x1 = Math.max(lx1, lx2) + r;
                y0 = Math.min(ly1, ly2) - r; y1 = Math.max(ly1, ly2) + r;
            }
            x0 = Math.max(x0, 0); y0 = Math.max(y0, 0);
            x1 = Math.min(x1, W - 1); y1 = Math.min(y1, H - 1);
            long r2 = r * r, dx = lx2 - lx1, dy = ly2 - ly1, L = dx * dx + dy * dy;
            long count = 0;
            for (long y = y0; y <= y1; y++) {
                for (long x = x0; x <= x1; x++) {
                    boolean cov;
                    switch (kind) {
                        case 0: cov = true; break;
                        case 1: cov = (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2; break;
                        case 2: {
                            boolean p = true, n = true;
                            for (int i = 0; i < 3; i++) {
                                int j = (i + 1) % 3;
                                long e = (px[j] - px[i]) * (y - py[i]) - (py[j] - py[i]) * (x - px[i]);
                                if (e < 0) p = false;
                                if (e > 0) n = false;
                            }
                            cov = p || n;
                            break;
                        }
                        default: {
                            long t = (x - lx1) * dx + (y - ly1) * dy;
                            if (t <= 0) cov = (x - lx1) * (x - lx1) + (y - ly1) * (y - ly1) <= r2;
                            else if (t >= L) cov = (x - lx2) * (x - lx2) + (y - ly2) * (y - ly2) <= r2;
                            else {
                                long c = (x - lx1) * dy - (y - ly1) * dx;
                                cov = c * c <= r2 * L;
                            }
                        }
                    }
                    if (cov) {
                        count++;
                        int o = (int) ((y * W + x) * 3);
                        for (int c = 0; c < 3; c++)
                            img[o + c] = (int) ((sc[c] * A + img[o + c] * (255 - A) + 127) / 255);
                    }
                }
            }
            out.append(si + 1).append(' ').append(type).append(' ').append(count).append('\n');
        }
        BitSet seen = new BitSet(1 << 24);
        int k = 0;
        for (int i = 0; i < W * H; i++) {
            int v = (img[i * 3] << 16) | (img[i * 3 + 1] << 8) | img[i * 3 + 2];
            if (!seen.get(v)) { seen.set(v); k++; }
        }
        out.append("colors ").append(k).append('\n');

        int stride = W * 3;
        byte[] raw = new byte[H * (stride + 1)];
        for (int y = 0; y < H; y++) {
            raw[y * (stride + 1)] = 0;
            for (int i = 0; i < stride; i++) raw[y * (stride + 1) + 1 + i] = (byte) img[y * stride + i];
        }
        Deflater def = new Deflater();
        def.setInput(raw);
        def.finish();
        ByteArrayOutputStream zb = new ByteArrayOutputStream();
        byte[] buf = new byte[65536];
        while (!def.finished()) {
            int n = def.deflate(buf);
            zb.write(buf, 0, n);
        }
        def.end();
        byte[] ihdr = new byte[13];
        ihdr[0] = (byte) (W >>> 24); ihdr[1] = (byte) (W >>> 16); ihdr[2] = (byte) (W >>> 8); ihdr[3] = (byte) W;
        ihdr[4] = (byte) (H >>> 24); ihdr[5] = (byte) (H >>> 16); ihdr[6] = (byte) (H >>> 8); ihdr[7] = (byte) H;
        ihdr[8] = 8; ihdr[9] = 2;
        ByteArrayOutputStream png = new ByteArrayOutputStream();
        png.write(new byte[]{(byte) 137, 80, 78, 71, 13, 10, 26, 10});
        png.write(chunk("IHDR", ihdr));
        png.write(chunk("IDAT", zb.toByteArray()));
        png.write(chunk("IEND", new byte[0]));
        Files.write(Paths.get("out.png"), png.toByteArray());
        System.out.print(out);
        System.out.flush();
    }
}
