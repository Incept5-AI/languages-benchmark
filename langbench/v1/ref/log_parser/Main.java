import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public class Main {
    public static void main(String[] args) throws Exception {
        Pattern pat = Pattern.compile("^\\[(\\d{4}-\\d{2}-\\d{2}) (\\d{2}):\\d{2}:\\d{2}\\] (\\S+) \\(([^)]*)\\) - (.*)$");
        Map<String,Integer> levels = new TreeMap<>(), comps = new HashMap<>();
        TreeSet<String> msgs = new TreeSet<>();
        Map<String,Integer> hours = new TreeMap<>();
        for (String line : Files.readAllLines(Path.of("__FIXTURE_DIR__/app.log"))) {
            Matcher m = pat.matcher(line.trim());
            if (!m.matches()) continue;
            String lvl = m.group(3);
            levels.merge(lvl, 1, Integer::sum);
            hours.merge(m.group(2), 1, Integer::sum);
            if (lvl.equals("ERROR")) {
                comps.merge(m.group(4), 1, Integer::sum);
                msgs.add(m.group(5));
            }
        }
        StringBuilder sb = new StringBuilder("=== Log Analysis ===\n\nEntries by Level:\n");
        for (var e : levels.entrySet()) sb.append("  ").append(e.getKey()).append(": ").append(e.getValue()).append("\n");
        String bc = ""; int bn = 0;
        for (var e : new TreeMap<>(comps).entrySet()) if (e.getValue() > bn) { bn = e.getValue(); bc = e.getKey(); }
        sb.append("\nComponent with Most Errors: ").append(bc).append(" (").append(bn).append(" errors)\n");
        sb.append("\nUnique Error Messages:\n");
        for (String s : msgs) sb.append("  - ").append(s).append("\n");
        String bh = ""; int bhn = 0;
        for (var e : hours.entrySet()) if (e.getValue() > bhn) { bhn = e.getValue(); bh = e.getKey(); }
        sb.append("\nBusiest Hour: ").append(bh).append(":00 (").append(bhn).append(" entries)\n");
        System.out.print(sb);
    }
}
