import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;

public class Main {
    record F(String rel, long lines, long chars) {}
    public static void main(String[] args) throws Exception {
        Path root = Path.of("__FIXTURE_DIR__/project_tree");
        List<Path> files;
        try (Stream<Path> st = Files.walk(root)) {
            files = st.filter(x -> Files.isRegularFile(x, LinkOption.NOFOLLOW_LINKS)).collect(Collectors.toList());
        }
        Map<String,List<F>> by = new TreeMap<>();
        long tf = 0, tl = 0, tc = 0;
        for (Path f : files) {
            byte[] data = Files.readAllBytes(f);
            long lines = 0;
            for (byte b : data) if (b == '\n') lines++;
            if (data.length > 0 && data[data.length - 1] != '\n') lines++;
            String rel = root.relativize(f).toString().replace('\\', '/');
            String name = f.getFileName().toString();
            int dot = name.lastIndexOf('.');
            String ext = dot > 0 ? name.substring(dot) : (dot == 0 ? name : "(no extension)");
            by.computeIfAbsent(ext, k -> new ArrayList<>()).add(new F(rel, lines, data.length));
        }
        StringBuilder sb = new StringBuilder("=== File Tree Analysis ===\n\n");
        for (var e : by.entrySet()) {
            List<F> l = e.getValue();
            l.sort(Comparator.comparing(F::rel));
            long ls = l.stream().mapToLong(F::lines).sum(), cs = l.stream().mapToLong(F::chars).sum();
            sb.append("Extension: ").append(e.getKey()).append("\n  Files: ").append(l.size())
              .append("\n  Total Lines: ").append(ls).append("\n  Total Characters: ").append(cs).append("\n");
            for (F f : l) sb.append("    ").append(f.rel()).append(": ").append(f.lines()).append(" lines, ").append(f.chars()).append(" chars\n");
            tf += l.size(); tl += ls; tc += cs;
        }
        sb.append("\nTotal: ").append(tf).append(" files, ").append(tl).append(" lines, ").append(tc).append(" characters\n");
        System.out.print(sb);
    }
}
