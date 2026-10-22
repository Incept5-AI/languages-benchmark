import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static long u32(byte[] d, int o) {
        return (d[o] & 0xFFL) | ((d[o + 1] & 0xFFL) << 8) | ((d[o + 2] & 0xFFL) << 16) | ((d[o + 3] & 0xFFL) << 24);
    }

    static int u16(byte[] d, int o) {
        return (d[o] & 0xFF) | ((d[o + 1] & 0xFF) << 8);
    }

    static boolean isInk(long r, long g, long b) {
        return (299 * r + 587 * g + 114 * b) / 1000 < 128;
    }

    static long chan(long px, long mask) {
        if (mask == 0) return 0;
        int s = Long.numberOfTrailingZeros(mask);
        int n = Long.bitCount(mask);
        long v = (px & mask) >>> s;
        long m = (1L << n) - 1;
        return (v * 255 + m / 2) / m;
    }

    static void process(Path dir, String name, StringBuilder out) throws IOException {
        out.append("== ").append(name).append('\n');
        byte[] d = Files.readAllBytes(dir.resolve(name));
        int off = (int) u32(d, 10);
        int hsz = (int) u32(d, 14);
        int w = (int) u32(d, 18);
        int hraw = (int) u32(d, 22);
        int bpp = u16(d, 28);
        long comp = u32(d, 30);
        long clrUsed = u32(d, 46);
        if (comp != 0 && comp != 3) {
            out.append("error: unsupported compression ").append(comp).append('\n');
            return;
        }
        boolean bottomUp = hraw > 0;
        int h = Math.abs(hraw);
        long rm = 0, gm = 0, bm = 0;
        if (comp == 3) {
            int p = hsz >= 108 ? 54 : 14 + hsz;
            rm = u32(d, p); gm = u32(d, p + 4); bm = u32(d, p + 8);
        }
        boolean[] palInk = new boolean[0];
        if (bpp <= 8) {
            int n = clrUsed != 0 ? (int) clrUsed : 1 << bpp;
            palInk = new boolean[n];
            int pal = 14 + hsz;
            for (int i = 0; i < n; i++) {
                int e = pal + 4 * i;
                palInk[i] = isInk(d[e + 2] & 0xFF, d[e + 1] & 0xFF, d[e] & 0xFF);
            }
        }
        long stride = (((long) w * bpp + 31) / 32) * 4;
        byte[] ink = new byte[w * h];
        long inkCount = 0;
        for (int y = 0; y < h; y++) {
            int srow = bottomUp ? h - 1 - y : y;
            int row = (int) (off + srow * stride);
            for (int x = 0; x < w; x++) {
                boolean is;
                if (bpp <= 8) {
                    int idx;
                    if (bpp == 8) idx = d[row + x] & 0xFF;
                    else if (bpp == 4) idx = ((d[row + (x >> 1)] & 0xFF) >> ((x & 1) == 1 ? 0 : 4)) & 15;
                    else idx = ((d[row + (x >> 3)] & 0xFF) >> (7 - (x & 7))) & 1;
                    is = idx < palInk.length && palInk[idx];
                } else if (bpp == 24) {
                    int p = row + 3 * x;
                    is = isInk(d[p + 2] & 0xFF, d[p + 1] & 0xFF, d[p] & 0xFF);
                } else if (comp == 0) {
                    int p = row + 4 * x;
                    is = isInk(d[p + 2] & 0xFF, d[p + 1] & 0xFF, d[p] & 0xFF);
                } else {
                    long px = u32(d, row + 4 * x);
                    is = isInk(chan(px, rm), chan(px, gm), chan(px, bm));
                }
                if (is) { ink[y * w + x] = 1; inkCount++; }
            }
        }
        out.append("size ").append(w).append('x').append(h).append(" bpp ").append(bpp)
           .append(" header ").append(hsz).append(bottomUp ? " bottom-up" : " top-down")
           .append(" ink ").append(inkCount).append('\n');
        StringBuilder lines = new StringBuilder();
        int ncomp = 0;
        int[] stack = new int[Math.max(1, w * h)];
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int i0 = y * w + x;
                if (ink[i0] != 1) continue;
                ink[i0] = 2;
                int sp = 0;
                stack[sp++] = i0;
                int minx = x, maxx = x, miny = y, maxy = y, cnt = 0;
                while (sp > 0) {
                    int i = stack[--sp];
                    int cy = i / w, cx = i % w;
                    cnt++;
                    if (cx < minx) minx = cx; if (cx > maxx) maxx = cx;
                    if (cy < miny) miny = cy; if (cy > maxy) maxy = cy;
                    for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) {
                        int nx = cx + dx, ny = cy + dy;
                        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
                        int j = ny * w + nx;
                        if (ink[j] == 1) { ink[j] = 2; stack[sp++] = j; }
                    }
                }
                ncomp++;
                lines.append(x).append(',').append(y).append(" bbox ").append(minx).append(',').append(miny)
                     .append('-').append(maxx).append(',').append(maxy).append(" pixels ").append(cnt).append('\n');
            }
        }
        out.append("components ").append(ncomp).append('\n');
        out.append(lines);
    }

    public static void main(String[] args) throws IOException {
        Path dir = Paths.get(args[0]);
        List<String> names = new ArrayList<>();
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir)) {
            for (Path p : ds) {
                String n = p.getFileName().toString();
                if (n.endsWith(".bmp")) names.add(n);
            }
        }
        names.sort((a, b) -> Arrays.compareUnsigned(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8)));
        StringBuilder out = new StringBuilder();
        for (String n : names) process(dir, n, out);
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(out);
        ps.flush();
    }
}
