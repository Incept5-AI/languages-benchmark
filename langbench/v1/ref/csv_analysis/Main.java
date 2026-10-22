import java.nio.file.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws Exception {
        List<String> lines = Files.readAllLines(Path.of("__FIXTURE_DIR__/employees.csv"));
        Map<String, List<Double>> byDept = new TreeMap<>();
        String topName = ""; double top = -1; double total = 0;
        for (int i = 1; i < lines.size(); i++) {
            String line = lines.get(i).trim();
            if (line.isEmpty()) continue;
            String[] f = line.split(",");
            double sal = Double.parseDouble(f[3].trim());
            String dept = f[2].trim();
            byDept.computeIfAbsent(dept, k -> new ArrayList<>()).add(sal);
            total += sal;
            if (sal > top) { top = sal; topName = f[1].trim(); }
        }
        StringBuilder sb = new StringBuilder();
        sb.append("=== Salary Report ===\n\nAverage Salary by Department:\n");
        for (var e : byDept.entrySet()) {
            double avg = e.getValue().stream().mapToDouble(d -> d).sum() / e.getValue().size();
            sb.append(String.format("  %s: $%,.2f%n", e.getKey(), avg));
        }
        sb.append(String.format("%nHighest Paid: %s ($%,d)%n", topName, Math.round(top)));
        sb.append("\nDepartments with >2 employees:\n");
        for (var e : byDept.entrySet())
            if (e.getValue().size() > 2) sb.append(String.format("  %s (%d employees)%n", e.getKey(), e.getValue().size()));
        sb.append(String.format("%nTotal Budget: $%,d%n", Math.round(total)));
        System.out.print(sb);
    }
}
