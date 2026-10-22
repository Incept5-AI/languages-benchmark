import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.SecureRandom;
import java.util.*;

public class Main {
    static int u16(byte[] m, int p) { return ((m[p] & 0xff) << 8) | (m[p + 1] & 0xff); }

    // returns name; end position in endOut[0]
    static String readName(byte[] m, int pos, int[] endOut) {
        List<String> labels = new ArrayList<>();
        int end = -1, jumps = 0;
        while (pos < m.length) {
            int l = m[pos] & 0xff;
            if ((l & 0xC0) == 0xC0) {
                if (pos + 1 >= m.length) break;
                if (end < 0) end = pos + 2;
                if (++jumps > 255) break;
                pos = ((l & 0x3F) << 8) | (m[pos + 1] & 0xff);
                continue;
            }
            if (l == 0) { if (end < 0) end = pos + 1; break; }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < l && pos + 1 + i < m.length; i++) sb.append(Character.toLowerCase((char) (m[pos + 1 + i] & 0xff)));
            labels.add(sb.toString());
            pos += 1 + l;
        }
        if (endOut != null) endOut[0] = end < 0 ? m.length : end;
        return labels.isEmpty() ? "." : String.join(".", labels);
    }

    static String typeName(int t) {
        switch (t) {
            case 1: return "A"; case 5: return "CNAME"; case 15: return "MX"; case 16: return "TXT"; case 28: return "AAAA";
        }
        return "TYPE" + t;
    }

    static String aaaa(byte[] m, int s) {
        int[] g = new int[8];
        for (int i = 0; i < 8; i++) g[i] = u16(m, s + 2 * i);
        int bs = -1, bl = 0;
        for (int i = 0; i < 8; ) {
            if (g[i] == 0) { int j = i; while (j < 8 && g[j] == 0) j++; if (j - i > bl) { bl = j - i; bs = i; } i = j; }
            else i++;
        }
        StringBuilder sb = new StringBuilder();
        if (bl < 2) {
            for (int i = 0; i < 8; i++) { if (i > 0) sb.append(':'); sb.append(Integer.toHexString(g[i])); }
            return sb.toString();
        }
        for (int i = 0; i < bs; i++) { if (i > 0) sb.append(':'); sb.append(Integer.toHexString(g[i])); }
        sb.append("::");
        for (int i = bs + bl; i < 8; i++) { if (i > bs + bl) sb.append(':'); sb.append(Integer.toHexString(g[i])); }
        return sb.toString();
    }

    static void readFully(InputStream in, byte[] b) throws IOException {
        int got = 0;
        while (got < b.length) { int r = in.read(b, got, b.length - got); if (r < 0) throw new EOFException(); got += r; }
    }

    public static void main(String[] args) throws Exception {
        String dir = args[0];
        int port = Integer.parseInt(args[1].trim());
        List<String> lines = Files.readAllLines(Paths.get(dir, "queries.txt"), StandardCharsets.ISO_8859_1);
        SecureRandom rnd = new SecureRandom();
        InetAddress lo = InetAddress.getByName("127.0.0.1");
        DatagramSocket sock = new DatagramSocket();
        StringBuilder out = new StringBuilder();
        for (String raw : lines) {
            String line = raw.trim();
            if (line.isEmpty()) continue;
            String[] parts = line.split("\\s+");
            String name = parts[0];
            String ty = parts.length > 1 ? parts[1] : "A";
            int qtype = 1;
            switch (ty) {
                case "A": qtype = 1; break; case "CNAME": qtype = 5; break; case "MX": qtype = 15; break;
                case "TXT": qtype = 16; break; case "AAAA": qtype = 28; break;
            }
            if (name.endsWith(".")) name = name.substring(0, name.length() - 1);
            int id = rnd.nextInt(65536);
            ByteArrayOutputStream q = new ByteArrayOutputStream();
            q.write(id >> 8); q.write(id & 0xff); q.write(1); q.write(0); q.write(0); q.write(1);
            for (int i = 0; i < 6; i++) q.write(0);
            if (!name.isEmpty()) {
                for (String lab : name.split("\\.", -1)) {
                    byte[] b = lab.getBytes(StandardCharsets.ISO_8859_1);
                    q.write(b.length); q.write(b);
                }
            }
            q.write(0); q.write(qtype >> 8); q.write(qtype & 0xff); q.write(0); q.write(1);
            byte[] qb = q.toByteArray();
            sock.send(new DatagramPacket(qb, qb.length, lo, port));
            byte[] buf = new byte[65536];
            byte[] msg;
            while (true) {
                DatagramPacket p = new DatagramPacket(buf, buf.length);
                sock.receive(p);
                int n = p.getLength();
                if (n < 12) continue;
                if (u16(buf, 0) == id && (buf[2] & 0x80) != 0) { msg = Arrays.copyOf(buf, n); break; }
            }
            if ((msg[2] & 0x02) != 0) {
                try (Socket s = new Socket(lo, port)) {
                    OutputStream os = s.getOutputStream();
                    byte[] fr = new byte[qb.length + 2];
                    fr[0] = (byte) (qb.length >> 8); fr[1] = (byte) qb.length;
                    System.arraycopy(qb, 0, fr, 2, qb.length);
                    os.write(fr); os.flush();
                    InputStream in = s.getInputStream();
                    byte[] lb = new byte[2];
                    readFully(in, lb);
                    msg = new byte[u16(lb, 0)];
                    readFully(in, msg);
                }
            }
            int rc = msg[3] & 0x0f;
            String[] rn = {"NOERROR", "FORMERR", "SERVFAIL", "NXDOMAIN", "NOTIMP", "REFUSED"};
            String lname = name.toLowerCase(Locale.ROOT);
            out.append(lname.isEmpty() ? "." : lname).append(' ').append(ty).append(' ')
               .append(rc <= 5 ? rn[rc] : "RCODE" + rc).append('\n');
            int qd = u16(msg, 4), an = u16(msg, 6);
            int pos = 12;
            int[] e = new int[1];
            for (int i = 0; i < qd; i++) { readName(msg, pos, e); pos = e[0] + 4; }
            for (int i = 0; i < an; i++) {
                String owner = readName(msg, pos, e);
                pos = e[0];
                int t = u16(msg, pos);
                long ttl = ((long) (msg[pos + 4] & 0xff) << 24) | ((msg[pos + 5] & 0xff) << 16) | ((msg[pos + 6] & 0xff) << 8) | (msg[pos + 7] & 0xff);
                int rdl = u16(msg, pos + 8);
                int rs = pos + 10;
                String data;
                if (t == 1 && rdl == 4) data = (msg[rs] & 0xff) + "." + (msg[rs + 1] & 0xff) + "." + (msg[rs + 2] & 0xff) + "." + (msg[rs + 3] & 0xff);
                else if (t == 28 && rdl == 16) data = aaaa(msg, rs);
                else if (t == 5) data = readName(msg, rs, null);
                else if (t == 15) data = u16(msg, rs) + " " + readName(msg, rs + 2, null);
                else if (t == 16) {
                    StringBuilder sb = new StringBuilder();
                    int p = 0; boolean first = true;
                    while (p < rdl) {
                        int l = msg[rs + p] & 0xff; p++;
                        if (!first) sb.append(' ');
                        first = false;
                        sb.append('"');
                        for (int k = 0; k < l && p < rdl; k++, p++) {
                            int b = msg[rs + p] & 0xff;
                            if (b == '"') sb.append("\\\"");
                            else if (b == '\\') sb.append("\\\\");
                            else if (b >= 0x20 && b <= 0x7e) sb.append((char) b);
                            else sb.append(String.format("\\%03d", b));
                        }
                        sb.append('"');
                    }
                    data = sb.toString();
                } else {
                    StringBuilder sb = new StringBuilder("\\# " + rdl);
                    if (rdl > 0) { sb.append(' '); for (int k = 0; k < rdl; k++) sb.append(String.format("%02x", msg[rs + k] & 0xff)); }
                    data = sb.toString();
                }
                out.append("  ").append(owner).append(' ').append(ttl).append(' ').append(typeName(t)).append(' ').append(data).append('\n');
                pos = rs + rdl;
            }
        }
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.ISO_8859_1);
        ps.print(out);
        ps.flush();
    }
}
