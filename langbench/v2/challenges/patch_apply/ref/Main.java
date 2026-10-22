import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public class Main {
    static class Hunk {
        int a, b, c, d;
        boolean malformed;
        List<byte[]> oldL = new ArrayList<>(), newL = new ArrayList<>();
    }
    static class Section {
        String path;
        String kind = "modify";
        List<Hunk> hunks = new ArrayList<>();
    }

    static List<byte[]> splitLines(byte[] data) {
        List<byte[]> out = new ArrayList<>();
        int start = 0;
        for (int i = 0; i < data.length; i++) {
            if (data[i] == '\n') {
                out.add(Arrays.copyOfRange(data, start, i + 1));
                start = i + 1;
            }
        }
        if (start < data.length) out.add(Arrays.copyOfRange(data, start, data.length));
        return out;
    }

    static boolean startsWith(byte[] l, String s) {
        if (l.length < s.length()) return false;
        for (int i = 0; i < s.length(); i++) if (l[i] != (byte) s.charAt(i)) return false;
        return true;
    }

    static String str(byte[] l) {
        int n = l.length;
        while (n > 0 && (l[n - 1] == '\n' || l[n - 1] == '\r')) n--;
        return new String(l, 0, n, java.nio.charset.StandardCharsets.ISO_8859_1);
    }

    static final Pattern HDR = Pattern.compile("^@@ -(\\d+)(?:,(\\d+))? \\+(\\d+)(?:,(\\d+))? @@.*$");

    static long[] crcTable = new long[256];
    static {
        for (int n = 0; n < 256; n++) {
            long c = n;
            for (int k = 0; k < 8; k++) c = ((c & 1) != 0) ? (0xEDB88320L ^ (c >>> 1)) : (c >>> 1);
            crcTable[n] = c;
        }
    }

    public static void main(String[] args) throws Exception {
        Path root = Paths.get(args[0]);
        byte[] diff = Files.readAllBytes(root.resolve("changes.diff"));
        List<byte[]> lines = splitLines(diff);
        List<Section> sections = new ArrayList<>();
        Section cur = null;
        Hunk hunk = null;
        boolean inHunk = false;
        for (int i = 0; i < lines.size(); i++) {
            byte[] l = lines.get(i);
            if (startsWith(l, "diff --git ")) {
                String[] parts = str(l).split(" ");
                String p = parts[2];
                if (p.startsWith("a/")) p = p.substring(2);
                cur = new Section();
                cur.path = p;
                sections.add(cur);
                inHunk = false;
                hunk = null;
                continue;
            }
            if (cur == null) continue;
            if (startsWith(l, "@@ ")) {
                Matcher m = HDR.matcher(str(l));
                hunk = new Hunk();
                if (m.matches()) {
                    hunk.a = Integer.parseInt(m.group(1));
                    hunk.b = m.group(2) == null ? 1 : Integer.parseInt(m.group(2));
                    hunk.c = Integer.parseInt(m.group(3));
                    hunk.d = m.group(4) == null ? 1 : Integer.parseInt(m.group(4));
                } else {
                    hunk.malformed = true;
                }
                cur.hunks.add(hunk);
                inHunk = true;
                continue;
            }
            if (!inHunk) {
                if (startsWith(l, "new file mode")) cur.kind = "create";
                else if (startsWith(l, "deleted file mode")) cur.kind = "delete";
                continue;
            }
            // body line
            if (l.length == 0) continue;
            byte mk = l[0];
            byte[] content = Arrays.copyOfRange(l, 1, l.length);
            if (mk == '\\') continue;
            boolean noNl = i + 1 < lines.size() && lines.get(i + 1).length > 0 && lines.get(i + 1)[0] == '\\';
            if (noNl && content.length > 0 && content[content.length - 1] == '\n')
                content = Arrays.copyOf(content, content.length - 1);
            if (mk == ' ') {
                hunk.oldL.add(content);
                hunk.newL.add(content);
            } else if (mk == '-') {
                hunk.oldL.add(content);
            } else if (mk == '+') {
                hunk.newL.add(content);
            } else if (mk == '\n') {
                hunk.oldL.add(new byte[]{'\n'});
                hunk.newL.add(new byte[]{'\n'});
            }
        }
        for (Section s : sections)
            for (Hunk h : s.hunks)
                if (h.oldL.size() != h.b || h.newL.size() != h.d) h.malformed = true;

        sections.sort((x, y) -> {
            byte[] bx = x.path.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
            byte[] by = y.path.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
            return Arrays.compareUnsigned(bx, by);
        });

        StringBuilder out = new StringBuilder();
        for (Section s : sections) {
            out.append(s.path).append(": ").append(s.kind).append('\n');
            Path fp = root.resolve("tree").resolve(s.path);
            boolean exists = Files.isRegularFile(fp);
            List<byte[]> content;
            if (s.kind.equals("create")) {
                if (exists) { out.append("  error: already exists\n"); continue; }
                content = new ArrayList<>();
            } else {
                if (!exists) { out.append("  error: not found\n"); continue; }
                content = splitLines(Files.readAllBytes(fp));
            }
            int delta = 0;
            boolean allApplied = true;
            int n = 0;
            for (Hunk h : s.hunks) {
                n++;
                if (h.malformed) {
                    out.append("  hunk ").append(n).append(": malformed\n");
                    allApplied = false;
                    continue;
                }
                long P = (long) h.a + delta;
                if (h.b == 0) {
                    if (P >= 0 && P <= content.size()) {
                        content.addAll((int) P, h.newL);
                        out.append("  hunk ").append(n).append(": applied at ").append(P).append(" (offset 0)\n");
                        delta += h.d - h.b;
                    } else {
                        out.append("  hunk ").append(n).append(": rejected\n");
                        allApplied = false;
                    }
                    continue;
                }
                int maxP = content.size() - h.b + 1;
                long found = -1;
                if (maxP >= 1) {
                    long lim = Math.max(Math.abs(P - 1), Math.abs(P - maxP)) + 1;
                    for (long k = 0; k <= lim && found < 0; k++) {
                        long[] cands = k == 0 ? new long[]{P} : new long[]{P - k, P + k};
                        for (long p : cands) {
                            if (p < 1 || p > maxP) continue;
                            boolean ok = true;
                            for (int j = 0; j < h.b; j++) {
                                if (!Arrays.equals(content.get((int) p - 1 + j), h.oldL.get(j))) { ok = false; break; }
                            }
                            if (ok) { found = p; break; }
                        }
                    }
                }
                if (found < 0) {
                    out.append("  hunk ").append(n).append(": rejected\n");
                    allApplied = false;
                    continue;
                }
                int fp0 = (int) found - 1;
                for (int j = 0; j < h.b; j++) content.remove(fp0);
                content.addAll(fp0, h.newL);
                long off = found - P;
                String os = off > 0 ? "+" + off : Long.toString(off);
                out.append("  hunk ").append(n).append(": applied at ").append(found).append(" (offset ").append(os).append(")\n");
                delta += h.d - h.b;
            }
            if (s.kind.equals("delete") && allApplied && content.isEmpty()) {
                out.append("  result: deleted\n");
                continue;
            }
            long c = 0xFFFFFFFFL;
            for (byte[] l : content)
                for (byte bb : l) c = crcTable[(int) ((c ^ bb) & 0xFF)] ^ (c >>> 8);
            c ^= 0xFFFFFFFFL;
            out.append("  result: ").append(content.size()).append(" lines, crc32 ").append(String.format("%08x", c)).append('\n');
        }
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false);
        ps.print(out);
        ps.flush();
    }
}
