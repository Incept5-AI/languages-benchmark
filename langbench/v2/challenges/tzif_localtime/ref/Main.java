import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static final class TType {
        final long off; final String abbr;
        TType(long off, String abbr) { this.off = off; this.abbr = abbr; }
    }
    static final class Rule {
        long m, w, d, t;
        Rule(long m, long w, long d, long t) { this.m = m; this.w = w; this.d = d; this.t = t; }
    }

    static long daysFromCivil(long y, long m, long d) {
        if (m <= 2) y -= 1;
        long era = Math.floorDiv(y, 400);
        long yoe = y - era * 400;
        long mp = m > 2 ? m - 3 : m + 9;
        long doy = (153 * mp + 2) / 5 + d - 1;
        long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
    }
    static long[] civilFromDays(long z) {
        z += 719468;
        long era = Math.floorDiv(z, 146097);
        long doe = z - era * 146097;
        long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        long y = yoe + era * 400;
        long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        long mp = (5 * doy + 2) / 153;
        long d = doy - (153 * mp + 2) / 5 + 1;
        long m = mp < 10 ? mp + 3 : mp - 9;
        return new long[]{m <= 2 ? y + 1 : y, m, d};
    }
    static long monthLen(long y, long m) {
        boolean leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
        if (m == 2) return leap ? 29 : 28;
        if (m == 4 || m == 6 || m == 9 || m == 11) return 30;
        return 31;
    }
    static long ruleLocal(long y, Rule r) {
        long first = daysFromCivil(y, r.m, 1);
        long dow = Math.floorMod(first + 4, 7);
        long day = 1 + (r.d - dow + 7) % 7 + (r.w - 1) * 7;
        long ml = monthLen(y, r.m);
        while (day > ml) day -= 7;
        return (first + day - 1) * 86400 + r.t;
    }
    static long yearOf(long t) { return civilFromDays(Math.floorDiv(t, 86400))[0]; }

    static final class P {
        final String s; int i = 0;
        P(String s) { this.s = s; }
        char peek() { return i < s.length() ? s.charAt(i) : '\0'; }
        String name() {
            StringBuilder sb = new StringBuilder();
            if (peek() == '<') {
                i++;
                while (peek() != '\0' && peek() != '>') sb.append(s.charAt(i++));
                if (peek() == '>') i++;
            } else {
                while ((peek() >= 'A' && peek() <= 'Z') || (peek() >= 'a' && peek() <= 'z')) sb.append(s.charAt(i++));
            }
            return sb.toString();
        }
        long num() {
            long v = 0;
            while (peek() >= '0' && peek() <= '9') v = v * 10 + (s.charAt(i++) - '0');
            return v;
        }
        long hms() {
            long sign = 1;
            if (peek() == '+') i++;
            else if (peek() == '-') { sign = -1; i++; }
            long h = num(), m = 0, sec = 0;
            if (peek() == ':') { i++; m = num(); if (peek() == ':') { i++; sec = num(); } }
            return sign * (h * 3600 + m * 60 + sec);
        }
        void rule(Rule r) {
            r.t = 7200;
            if (peek() == 'M') {
                i++;
                r.m = num(); if (peek() == '.') i++;
                r.w = num(); if (peek() == '.') i++;
                r.d = num();
            }
            if (peek() == '/') { i++; r.t = hms(); }
        }
    }

    static final class Zone {
        long[] times; int[] idx; TType[] types;
        boolean hasFooter; TType fstd = new TType(0, ""); TType fdst = null;
        Rule start = new Rule(3, 2, 0, 7200), end = new Rule(11, 1, 0, 7200);

        static long be(byte[] b, int p, int n) {
            long v = 0;
            for (int k = 0; k < n; k++) v = (v << 8) | (b[p + k] & 0xff);
            if (n == 4) return (int) v;
            return v;
        }

        Zone(byte[] b) {
            long[] c = new long[6];
            for (int i = 0; i < 6; i++) c[i] = be(b, 20 + 4 * i, 4);
            long v1 = c[3] * 5 + c[4] * 6 + c[5] + c[2] * 8 + c[1] + c[0];
            int p = (int) (44 + v1);
            for (int i = 0; i < 6; i++) c[i] = be(b, p + 20 + 4 * i, 4);
            int isut = (int) c[0], isstd = (int) c[1], leap = (int) c[2], tc = (int) c[3], ty = (int) c[4], ch = (int) c[5];
            p += 44;
            times = new long[tc];
            for (int i = 0; i < tc; i++) times[i] = be(b, p + 8 * i, 8);
            p += 8 * tc;
            idx = new int[tc];
            for (int i = 0; i < tc; i++) idx[i] = b[p + i] & 0xff;
            p += tc;
            int tp = p;
            p += 6 * ty;
            int cp = p;
            types = new TType[ty];
            for (int i = 0; i < ty; i++) {
                long off = be(b, tp + 6 * i, 4);
                int ai = b[tp + 6 * i + 5] & 0xff;
                int e = ai;
                while (e < ch && b[cp + e] != 0) e++;
                types[i] = new TType(off, new String(b, cp + Math.min(ai, ch), Math.max(0, e - Math.min(ai, ch)), StandardCharsets.ISO_8859_1));
            }
            p += ch + leap * 12 + isstd + isut;
            String foot = "";
            if (p < b.length && b[p] == '\n') {
                p++;
                int e = p;
                while (e < b.length && b[e] != '\n') e++;
                foot = new String(b, p, e - p, StandardCharsets.ISO_8859_1);
            }
            hasFooter = !foot.isEmpty();
            if (hasFooter) {
                P ps = new P(foot);
                String n = ps.name();
                long o = ps.hms();
                fstd = new TType(-o, n);
                if (ps.peek() != '\0' && ps.peek() != ',') {
                    String dn = ps.name();
                    long doff;
                    if (ps.peek() != '\0' && ps.peek() != ',') doff = -ps.hms();
                    else doff = fstd.off + 3600;
                    fdst = new TType(doff, dn);
                    if (ps.peek() == ',') {
                        ps.i++;
                        ps.rule(start);
                        if (ps.peek() == ',') { ps.i++; ps.rule(end); }
                    }
                }
            }
        }

        long[] events(long y) {
            return new long[]{ruleLocal(y, start) - fstd.off, ruleLocal(y, end) - fdst.off};
        }

        TType footerType(long t) {
            if (fdst == null) return fstd;
            long Y = yearOf(t);
            boolean have = false, bestDst = false;
            long best = 0;
            for (long y = Y - 2; y <= Y + 2; y++) {
                long[] ev = events(y);
                if (ev[0] <= t && (!have || ev[0] > best)) { best = ev[0]; bestDst = true; have = true; }
                if (ev[1] <= t && (!have || ev[1] > best)) { best = ev[1]; bestDst = false; have = true; }
            }
            return bestDst ? fdst : fstd;
        }

        TType typeAt(long t) {
            int n = times.length;
            if (n == 0) return hasFooter ? footerType(t) : types[0];
            if (t < times[0]) return types[0];
            if (t > times[n - 1]) return hasFooter ? footerType(t) : types[idx[n - 1]];
            int lo = 0, hi = n - 1;
            while (lo < hi) {
                int mid = (lo + hi + 1) >>> 1;
                if (times[mid] <= t) lo = mid; else hi = mid - 1;
            }
            return types[idx[lo]];
        }
    }

    static String fmtOff(long off) {
        char sign = off < 0 ? '-' : '+';
        long a = Math.abs(off);
        if (a % 60 != 0) return String.format("%c%02d:%02d:%02d", sign, a / 3600, (a / 60) % 60, a % 60);
        return String.format("%c%02d:%02d", sign, a / 3600, (a / 60) % 60);
    }
    static String fmtLocal(long t, long off) {
        long l = t + off;
        long[] ymd = civilFromDays(Math.floorDiv(l, 86400));
        long s = Math.floorMod(l, 86400);
        return String.format("%04d-%02d-%02dT%02d:%02d:%02d", ymd[0], ymd[1], ymd[2], s / 3600, (s / 60) % 60, s % 60) + fmtOff(off);
    }

    public static void main(String[] args) throws IOException {
        Path dir = Paths.get(args[0]);
        List<String> lines = Files.readAllLines(dir.resolve("queries.txt"), StandardCharsets.UTF_8);
        Map<String, Zone> zones = new HashMap<>();
        StringBuilder out = new StringBuilder();
        for (String raw : lines) {
            String line = raw.endsWith("\r") ? raw.substring(0, raw.length() - 1) : raw;
            if (line.trim().isEmpty()) continue;
            String[] parts = line.split(" ");
            if (parts.length < 3) continue;
            Zone z = zones.get(parts[1]);
            if (z == null) {
                try { z = new Zone(Files.readAllBytes(dir.resolve(parts[1]))); } catch (Exception e) { continue; }
                zones.put(parts[1], z);
            }
            if (parts[0].equals("UTC")) {
                long t = Long.parseLong(parts[2]);
                TType tt = z.typeAt(t);
                out.append(line).append(" -> ").append(fmtLocal(t, tt.off)).append(' ').append(tt.abbr).append('\n');
            } else {
                String a = parts[2];
                long W = daysFromCivil(Long.parseLong(a.substring(0, 4)), Long.parseLong(a.substring(5, 7)), Long.parseLong(a.substring(8, 10))) * 86400
                        + Long.parseLong(a.substring(11, 13)) * 3600 + Long.parseLong(a.substring(14, 16)) * 60 + Long.parseLong(a.substring(17, 19));
                List<Long> offs = new ArrayList<>();
                for (TType x : z.types) offs.add(x.off);
                if (z.hasFooter) { offs.add(z.fstd.off); if (z.fdst != null) offs.add(z.fdst.off); }
                long minoff = Collections.min(offs), maxoff = Collections.max(offs);
                TreeSet<Long> res = new TreeSet<>();
                for (long o : offs) {
                    long t = W - o;
                    if (z.typeAt(t).off == o) res.add(t);
                }
                if (!res.isEmpty()) {
                    out.append(line).append(" ->");
                    boolean first = true;
                    for (long t : res) {
                        TType tt = z.typeAt(t);
                        out.append(first ? " " : " | ").append(t).append(' ').append(fmtOff(tt.off)).append(' ').append(tt.abbr);
                        first = false;
                    }
                    out.append('\n');
                } else {
                    long lo = W - maxoff - 2, hi = W - minoff + 2;
                    List<Long> c = new ArrayList<>();
                    for (long x : z.times) if (x >= lo && x <= hi) c.add(x);
                    long lastT = z.times.length > 0 ? z.times[z.times.length - 1] : Long.MIN_VALUE / 2;
                    if (z.times.length > 0 && lastT + 1 >= lo && lastT + 1 <= hi) c.add(lastT + 1);
                    if (z.fdst != null) {
                        for (long y = yearOf(lo) - 1; y <= yearOf(hi) + 1; y++) {
                            for (long x : z.events(y)) if (x > lastT && x >= lo && x <= hi) c.add(x);
                        }
                    }
                    Collections.sort(c);
                    long t = W - z.typeAt(W).off;
                    for (long tr : c) {
                        long ob = z.typeAt(tr - 1).off, oa = z.typeAt(tr).off;
                        if (tr + ob <= W && W < tr + oa) { t = W - ob; break; }
                    }
                    TType tt = z.typeAt(t);
                    out.append(line).append(" -> GAP ").append(t).append(' ').append(fmtLocal(t, tt.off)).append(' ').append(tt.abbr).append('\n');
                }
            }
        }
        System.out.print(out);
        System.out.flush();
    }
}
