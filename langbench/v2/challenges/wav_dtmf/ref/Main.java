import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class Main {
    static final double[] LOWF = {697, 770, 852, 941};
    static final double[] HIGHF = {1209, 1336, 1477, 1633};
    static final String[] KEYS = {"123A", "456B", "789C", "*0#D"};

    static int u16(byte[] b, int p) { return (b[p] & 0xFF) | ((b[p + 1] & 0xFF) << 8); }
    static long u32(byte[] b, int p) { return (u16(b, p) | ((long) u16(b, p + 2) << 16)); }

    static boolean idEq(byte[] b, int p, String s) {
        for (int i = 0; i < 4; i++) if (b[p + i] != (byte) s.charAt(i)) return false;
        return true;
    }

    static void process(String name, byte[] buf, StringBuilder out) {
        long len = buf.length;
        int fmtOff = -1, fmtLen = 0, dataOff = -1, dataLen = 0;
        long pos = 12;
        while (pos + 8 <= len) {
            int p = (int) pos;
            long n = u32(buf, p + 4);
            long start = pos + 8;
            long end = Math.min(start + n, len);
            if (idEq(buf, p, "fmt ")) { fmtOff = (int) start; fmtLen = (int) (end - start); }
            else if (idEq(buf, p, "data")) { dataOff = (int) start; dataLen = (int) (end - start); }
            pos = start + n + (n & 1);
        }
        if (fmtOff < 0 || fmtLen < 16) { out.append(name).append(": unsupported\n"); return; }
        int tag = u16(buf, fmtOff), ch = u16(buf, fmtOff + 2), bits = u16(buf, fmtOff + 14);
        long rate = u32(buf, fmtOff + 4);
        int eff = tag;
        if (tag == 0xFFFE) eff = fmtLen >= 26 ? u16(buf, fmtOff + 24) : 0;
        if (eff != 1 || bits != 16) { out.append(name).append(": unsupported\n"); return; }
        if (dataOff < 0) dataLen = 0;
        int frames = dataLen / (2 * ch);
        int N = (int) (rate / 50);
        int nb = frames / N;
        out.append(name).append(": ").append(rate).append(" Hz, ").append(ch).append(" ch, ").append(nb).append(" blocks\n");
        char[] lab = new char[nb];
        double[] x = new double[N];
        for (int b = 0; b < nb; b++) {
            double E = 0;
            for (int i = 0; i < N; i++) {
                int off = dataOff + (b * N + i) * 2 * ch;
                double s = (short) u16(buf, off);
                x[i] = s;
                E += s * s;
            }
            if (E < 1000000.0 * N) continue;
            double[] r = new double[8];
            for (int k = 0; k < 8; k++) {
                double fq = k < 4 ? LOWF[k] : HIGHF[k - 4];
                double c = 2 * Math.cos(2 * Math.PI * fq / rate), s1 = 0, s2 = 0;
                for (int i = 0; i < N; i++) { double s = x[i] + c * s1 - s2; s2 = s1; s1 = s; }
                double P = s1 * s1 + s2 * s2 - c * s1 * s2;
                r[k] = 2 * P / (N * E);
            }
            int bl = 0, bh = 0;
            for (int k = 1; k < 4; k++) { if (r[k] > r[bl]) bl = k; if (r[4 + k] > r[4 + bh]) bh = k; }
            if (r[bl] >= 0.1 && r[4 + bh] >= 0.1 && r[bl] + r[4 + bh] >= 0.6) lab[b] = KEYS[bl].charAt(bh);
        }
        char[] br = lab.clone();
        for (int b = 1; b + 1 < nb; b++)
            if (lab[b] == 0 && lab[b - 1] != 0 && lab[b - 1] == lab[b + 1]) br[b] = lab[b - 1];
        StringBuilder digits = new StringBuilder();
        int i = 0;
        while (i < nb) {
            int j = i;
            while (j + 1 < nb && br[j + 1] == br[i]) j++;
            if (br[i] != 0 && j - i + 1 >= 2) {
                out.append(br[i]).append(' ').append(i * 20L).append(' ').append((j + 1) * 20L).append('\n');
                digits.append(br[i]);
            }
            i = j + 1;
        }
        out.append("digits: ").append(digits).append('\n');
    }

    static int cmpBytes(byte[] a, byte[] b) {
        int n = Math.min(a.length, b.length);
        for (int i = 0; i < n; i++) {
            int c = (a[i] & 0xFF) - (b[i] & 0xFF);
            if (c != 0) return c;
        }
        return a.length - b.length;
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) return;
        File dir = new File(args[0]);
        String[] all = dir.list();
        List<String> names = new ArrayList<>();
        if (all != null) for (String s : all) if (s.endsWith(".wav")) names.add(s);
        names.sort((a, b) -> cmpBytes(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8)));
        StringBuilder out = new StringBuilder();
        for (int k = 0; k < names.size(); k++) {
            if (k > 0) out.append('\n');
            byte[] buf = Files.readAllBytes(new File(dir, names.get(k)).toPath());
            process(names.get(k), buf, out);
        }
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(out);
        ps.flush();
    }
}
