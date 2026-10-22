import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static int[] CRCT = new int[256];
    static {
        for (int n = 0; n < 256; n++) {
            int c = n;
            for (int k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
            CRCT[n] = c;
        }
    }
    static int crcUpdate(int c, byte[] b, int off, int len) {
        for (int i = 0; i < len; i++) c = CRCT[(c ^ b[off + i]) & 0xff] ^ (c >>> 8);
        return c;
    }
    static long be32(byte[] b, int p) {
        return ((long) (b[p] & 0xff) << 24) | ((b[p + 1] & 0xff) << 16) | ((b[p + 2] & 0xff) << 8) | (b[p + 3] & 0xff);
    }

    static final int[] LBASE = {3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
    static final int[] LEXT = {0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
    static final int[] DBASE = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577};
    static final int[] DEXT = {0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13};

    static class Huff {
        int[] count = new int[16];
        int[] sym;
        Huff(int[] len, int off, int n) {
            for (int i = 0; i < n; i++) count[len[off + i]]++;
            count[0] = 0;
            int[] offs = new int[16];
            for (int i = 1; i < 15; i++) offs[i + 1] = offs[i] + count[i];
            sym = new int[Math.max(n, 1)];
            for (int i = 0; i < n; i++) if (len[off + i] != 0) sym[offs[len[off + i]]++] = i;
        }
    }

    static class Inf {
        byte[] in; int inlen; int pos; int bitbuf, bitcnt;
        byte[] out = new byte[65536]; int outlen;
        int nstored, nfixed, ndynamic;
        int bit() {
            if (bitcnt == 0) {
                bitbuf = pos < inlen ? in[pos] & 0xff : 0;
                pos++; bitcnt = 8;
            }
            int b = bitbuf & 1; bitbuf >>= 1; bitcnt--;
            return b;
        }
        int bits(int n) { int v = 0; for (int i = 0; i < n; i++) v |= bit() << i; return v; }
        void put(int b) {
            if (outlen == out.length) out = Arrays.copyOf(out, out.length * 2);
            out[outlen++] = (byte) b;
        }
        int decode(Huff h) {
            int code = 0, first = 0, index = 0;
            for (int len = 1; len < 16; len++) {
                code |= bit();
                int count = h.count[len];
                if (code - count < first) return h.sym[index + (code - first)];
                index += count; first += count; first <<= 1; code <<= 1;
            }
            return -1;
        }
        boolean codes(Huff lc, Huff dc) {
            for (;;) {
                int sym = decode(lc);
                if (sym < 0) return false;
                if (sym < 256) put(sym);
                else if (sym == 256) return true;
                else {
                    sym -= 257;
                    if (sym >= 29) return false;
                    int len = LBASE[sym] + bits(LEXT[sym]);
                    int d = decode(dc);
                    if (d < 0 || d >= 30) return false;
                    int dist = DBASE[d] + bits(DEXT[d]);
                    if (dist > outlen) return false;
                    for (int i = 0; i < len; i++) put(out[outlen - dist]);
                }
            }
        }
        boolean inflate() {
            int last;
            do {
                last = bit();
                int t = bits(2);
                if (t == 0) {
                    nstored++;
                    bitcnt = 0;
                    if (pos + 4 > inlen) return false;
                    int len = (in[pos] & 0xff) | ((in[pos + 1] & 0xff) << 8);
                    pos += 4;
                    if (pos + len > inlen) return false;
                    for (int i = 0; i < len; i++) put(in[pos + i]);
                    pos += len;
                } else if (t == 1) {
                    nfixed++;
                    int[] l = new int[288];
                    for (int i = 0; i < 288; i++) l[i] = i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8;
                    int[] dl = new int[30]; Arrays.fill(dl, 5);
                    if (!codes(new Huff(l, 0, 288), new Huff(dl, 0, 30))) return false;
                } else if (t == 2) {
                    ndynamic++;
                    int nlen = bits(5) + 257, ndist = bits(5) + 1, ncode = bits(4) + 4;
                    int[] ord = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};
                    int[] cl = new int[19];
                    for (int i = 0; i < ncode; i++) cl[ord[i]] = bits(3);
                    Huff ch = new Huff(cl, 0, 19);
                    int[] lens = new int[nlen + ndist];
                    int idx = 0;
                    while (idx < nlen + ndist) {
                        int sym = decode(ch);
                        if (sym < 0) return false;
                        if (sym < 16) lens[idx++] = sym;
                        else {
                            int rep, val = 0;
                            if (sym == 16) { if (idx == 0) return false; val = lens[idx - 1]; rep = 3 + bits(2); }
                            else if (sym == 17) rep = 3 + bits(3);
                            else rep = 11 + bits(7);
                            if (idx + rep > nlen + ndist) return false;
                            while (rep-- > 0) lens[idx++] = val;
                        }
                    }
                    if (!codes(new Huff(lens, 0, nlen), new Huff(lens, nlen, ndist))) return false;
                } else return false;
            } while (last == 0);
            return true;
        }
    }

    static int paeth(int a, int b, int c) {
        int p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        if (pa <= pb && pa <= pc) return a;
        if (pb <= pc) return b;
        return c;
    }

    static void process(Path dir, String name, StringBuilder o) throws IOException {
        o.append("== ").append(name).append('\n');
        byte[] buf = Files.readAllBytes(dir.resolve(name));
        int sz = buf.length;
        int p = 8, n = 0;
        while (p + 12 <= sz) {
            int len = (int) be32(buf, p);
            n++;
            int c = ~crcUpdate(0xffffffff, buf, p + 4, len + 4);
            if ((c & 0xffffffffL) != be32(buf, p + 8 + len)) {
                o.append("error: CRC mismatch in chunk ").append(n).append(' ')
                 .append(new String(buf, p + 4, 4, StandardCharsets.ISO_8859_1)).append('\n');
                return;
            }
            p += 12 + len;
        }
        int w = 0, h = 0, depth = 0, ctype = 0;
        byte[] pal = new byte[0], trns = new byte[0];
        boolean hastrns = false;
        ByteArrayOutputStream idat = new ByteArrayOutputStream();
        List<String> types = new ArrayList<>(); List<Integer> runs = new ArrayList<>();
        p = 8;
        while (p + 12 <= sz) {
            int len = (int) be32(buf, p);
            String ty = new String(buf, p + 4, 4, StandardCharsets.ISO_8859_1);
            int d = p + 8;
            if (!types.isEmpty() && types.get(types.size() - 1).equals(ty)) runs.set(runs.size() - 1, runs.get(runs.size() - 1) + 1);
            else { types.add(ty); runs.add(1); }
            switch (ty) {
                case "IHDR":
                    w = (int) be32(buf, d); h = (int) be32(buf, d + 4); depth = buf[d + 8] & 0xff; ctype = buf[d + 9] & 0xff; break;
                case "PLTE": pal = Arrays.copyOfRange(buf, d, d + len); break;
                case "tRNS": hastrns = true; trns = Arrays.copyOfRange(buf, d, d + len); break;
                case "IDAT": idat.write(buf, d, len); break;
                default: break;
            }
            p += 12 + len;
        }
        StringBuilder cl = new StringBuilder();
        for (int i = 0; i < types.size(); i++) {
            if (i > 0) cl.append(' ');
            cl.append(types.get(i));
            if (runs.get(i) > 1) cl.append('*').append(runs.get(i));
        }
        byte[] id = idat.toByteArray();
        Inf s = new Inf();
        s.in = id; s.inlen = id.length; s.pos = 2;
        s.inflate();
        long a1 = 1, a2 = 0;
        byte[] out = s.out; int outlen = s.outlen;
        for (int i = 0; i < outlen; i++) { a1 = (a1 + (out[i] & 0xff)) % 65521; a2 = (a2 + a1) % 65521; }
        long adl = (a2 << 16) | a1;
        long want = s.pos + 4 <= id.length ? be32(id, s.pos) : -1;
        if (adl != want) { o.append("error: Adler-32 mismatch\n"); return; }

        int chans = ctype == 0 || ctype == 3 ? 1 : ctype == 2 ? 3 : ctype == 4 ? 2 : 4;
        int bppBits = chans * depth;
        int stride = (int) (((long) w * bppBits + 7) / 8);
        int bpp = Math.max(1, (bppBits + 7) / 8);
        byte[] prow = new byte[stride], cur = new byte[stride];
        long[] fc = new long[5], sum = new long[4];
        int crc = 0xffffffff;
        byte[] rgba = new byte[w * 4];
        int ip = 0;
        int maxv = depth >= 16 ? 0xffff : (1 << depth) - 1;
        int[] tv = new int[3];
        for (int i = 0; i < 3 && 2 * i + 1 < trns.length; i++) tv[i] = ((trns[2 * i] & 0xff) << 8) | (trns[2 * i + 1] & 0xff);
        int[] v = new int[4];
        for (int y = 0; y < h; y++) {
            int ft = ip < outlen ? out[ip] & 0xff : 0;
            ip++;
            if (ft < 5) fc[ft]++;
            for (int i = 0; i < stride; i++) cur[i] = ip + i < outlen ? out[ip + i] : 0;
            ip += stride;
            for (int i = 0; i < stride; i++) {
                int a = i >= bpp ? cur[i - bpp] & 0xff : 0;
                int b = prow[i] & 0xff;
                int c = i >= bpp ? prow[i - bpp] & 0xff : 0;
                int x = cur[i] & 0xff;
                switch (ft) {
                    case 1: x += a; break;
                    case 2: x += b; break;
                    case 3: x += (a + b) / 2; break;
                    case 4: x += paeth(a, b, c); break;
                    default: break;
                }
                cur[i] = (byte) x;
            }
            for (int x = 0; x < w; x++) {
                for (int ch = 0; ch < chans; ch++) {
                    if (depth == 16) {
                        int o2 = (x * chans + ch) * 2;
                        v[ch] = ((cur[o2] & 0xff) << 8) | (cur[o2 + 1] & 0xff);
                    } else if (depth == 8) {
                        v[ch] = cur[x * chans + ch] & 0xff;
                    } else {
                        int bit = (x * chans + ch) * depth;
                        v[ch] = ((cur[bit / 8] & 0xff) >> (8 - depth - bit % 8)) & maxv;
                    }
                }
                int r, g, b, a = 255;
                if (ctype == 3) {
                    int i = v[0];
                    r = i * 3 < pal.length ? pal[i * 3] & 0xff : 0;
                    g = i * 3 + 1 < pal.length ? pal[i * 3 + 1] & 0xff : 0;
                    b = i * 3 + 2 < pal.length ? pal[i * 3 + 2] & 0xff : 0;
                    a = i < trns.length ? trns[i] & 0xff : 255;
                } else if (ctype == 0) {
                    r = g = b = cv(v[0], depth, maxv);
                    if (hastrns && trns.length >= 2 && v[0] == tv[0]) a = 0;
                } else if (ctype == 2) {
                    r = cv(v[0], depth, maxv); g = cv(v[1], depth, maxv); b = cv(v[2], depth, maxv);
                    if (hastrns && trns.length >= 6 && v[0] == tv[0] && v[1] == tv[1] && v[2] == tv[2]) a = 0;
                } else if (ctype == 4) {
                    r = g = b = cv(v[0], depth, maxv); a = cv(v[1], depth, maxv);
                } else {
                    r = cv(v[0], depth, maxv); g = cv(v[1], depth, maxv); b = cv(v[2], depth, maxv); a = cv(v[3], depth, maxv);
                }
                rgba[x * 4] = (byte) r; rgba[x * 4 + 1] = (byte) g; rgba[x * 4 + 2] = (byte) b; rgba[x * 4 + 3] = (byte) a;
                sum[0] += r; sum[1] += g; sum[2] += b; sum[3] += a;
            }
            crc = crcUpdate(crc, rgba, 0, rgba.length);
            byte[] t = prow; prow = cur; cur = t;
        }
        o.append("IHDR ").append(w).append('x').append(h).append(" depth=").append(depth).append(" color=").append(ctype).append('\n');
        o.append("chunks ").append(cl).append('\n');
        o.append("deflate stored=").append(s.nstored).append(" fixed=").append(s.nfixed).append(" dynamic=").append(s.ndynamic).append('\n');
        o.append("filters none=").append(fc[0]).append(" sub=").append(fc[1]).append(" up=").append(fc[2])
         .append(" average=").append(fc[3]).append(" paeth=").append(fc[4]).append('\n');
        o.append("rgba sum=").append(sum[0]).append(',').append(sum[1]).append(',').append(sum[2]).append(',').append(sum[3])
         .append(" crc32=").append(String.format("%08x", (~crc) & 0xffffffffL)).append('\n');
    }

    static int cv(int v, int depth, int maxv) {
        if (depth == 16) return v >> 8;
        if (depth == 8) return v;
        return v * 255 / maxv;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) return;
        Path dir = Paths.get(args[0]);
        List<byte[]> names = new ArrayList<>();
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir)) {
            for (Path q : ds) {
                String nm = q.getFileName().toString();
                if (nm.endsWith(".png")) names.add(nm.getBytes(StandardCharsets.UTF_8));
            }
        }
        names.sort(Arrays::compareUnsigned);
        StringBuilder o = new StringBuilder();
        for (byte[] nb : names) process(dir, new String(nb, StandardCharsets.UTF_8), o);
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(o);
        ps.flush();
    }
}
