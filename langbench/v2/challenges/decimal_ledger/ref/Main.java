import java.io.*;
import java.math.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static Map<String, Integer> curs = new HashMap<>();
    static class Acct { String cur; BigDecimal bal; BigDecimal limit; }
    static TreeMap<String, Acct> accts = new TreeMap<>();

    static BigDecimal rnd(BigDecimal v, int m) { return v.setScale(m, RoundingMode.HALF_EVEN); }

    static String fmt(BigDecimal v, int m) {
        v = v.setScale(m, RoundingMode.HALF_EVEN);
        boolean neg = v.signum() < 0;
        String s = v.abs().toPlainString();
        String ip = s, fp = "";
        int dot = s.indexOf('.');
        if (dot >= 0) { ip = s.substring(0, dot); fp = s.substring(dot); }
        StringBuilder sb = new StringBuilder();
        int n = ip.length();
        for (int i = 0; i < n; i++) {
            if (i > 0 && (n - i) % 3 == 0) sb.append(',');
            sb.append(ip.charAt(i));
        }
        return (neg ? "-" : "") + sb + fp;
    }

    public static void main(String[] args) throws IOException {
        List<String> lines = Files.readAllLines(Paths.get(args[0], "ledger.txt"), StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder();
        for (int ln = 0; ln < lines.size(); ln++) {
            String line = lines.get(ln).trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] f = line.split(" +");
            String r = exec(f);
            if (r != null) out.append("line ").append(ln + 1).append(": REJECT ").append(r).append('\n');
        }
        out.append('\n');
        TreeMap<String, BigDecimal> tot = new TreeMap<>();
        TreeMap<String, Integer> cnt = new TreeMap<>();
        for (Map.Entry<String, Acct> e : accts.entrySet()) {
            Acct a = e.getValue();
            int m = curs.get(a.cur);
            out.append(e.getKey()).append(' ').append(a.cur).append(' ').append(fmt(a.bal, m)).append('\n');
            tot.merge(a.cur, a.bal, BigDecimal::add);
            cnt.merge(a.cur, 1, Integer::sum);
        }
        out.append('\n');
        for (String c : tot.keySet()) {
            out.append("total ").append(c).append(' ').append(fmt(tot.get(c), curs.get(c)))
               .append(" (").append(cnt.get(c)).append(" accounts)\n");
        }
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        ps.print(out);
        ps.flush();
    }

    static String unknown(String... names) {
        for (String n : names) if (!accts.containsKey(n)) return "unknown account " + n;
        return null;
    }

    static boolean insufficient(Acct a, BigDecimal amt) {
        return a.bal.subtract(amt).compareTo(a.limit.negate()) < 0;
    }

    static String exec(String[] f) {
        String cmd = f[0];
        switch (cmd) {
            case "currency": {
                curs.put(f[1], Integer.parseInt(f[2]));
                return null;
            }
            case "open": {
                if (accts.containsKey(f[1])) return "account exists " + f[1];
                Acct a = new Acct();
                a.cur = f[2];
                int m = curs.get(a.cur);
                a.bal = BigDecimal.ZERO.setScale(m);
                a.limit = f.length > 3 ? rnd(new BigDecimal(f[3]), m) : BigDecimal.ZERO.setScale(m);
                accts.put(f[1], a);
                return null;
            }
            case "deposit":
            case "withdraw": {
                String u = unknown(f[1]);
                if (u != null) return u;
                Acct a = accts.get(f[1]);
                BigDecimal amt = rnd(new BigDecimal(f[2]), curs.get(a.cur));
                if (amt.signum() <= 0) return "non-positive amount";
                if (cmd.equals("withdraw")) {
                    if (insufficient(a, amt)) return "insufficient funds " + f[1];
                    a.bal = a.bal.subtract(amt);
                } else a.bal = a.bal.add(amt);
                return null;
            }
            case "transfer": {
                String u = unknown(f[1], f[2]);
                if (u != null) return u;
                Acct a = accts.get(f[1]), b = accts.get(f[2]);
                if (!a.cur.equals(b.cur)) return "currency mismatch";
                BigDecimal amt = rnd(new BigDecimal(f[3]), curs.get(a.cur));
                if (amt.signum() <= 0) return "non-positive amount";
                if (insufficient(a, amt)) return "insufficient funds " + f[1];
                a.bal = a.bal.subtract(amt);
                b.bal = b.bal.add(amt);
                return null;
            }
            case "convert": {
                String u = unknown(f[1], f[2]);
                if (u != null) return u;
                Acct a = accts.get(f[1]), b = accts.get(f[2]);
                BigDecimal amt = rnd(new BigDecimal(f[3]), curs.get(a.cur));
                if (amt.signum() <= 0) return "non-positive amount";
                if (insufficient(a, amt)) return "insufficient funds " + f[1];
                BigDecimal got = rnd(amt.multiply(new BigDecimal(f[4])), curs.get(b.cur));
                a.bal = a.bal.subtract(amt);
                b.bal = b.bal.add(got);
                return null;
            }
            case "interest": {
                String u = unknown(f[1]);
                if (u != null) return u;
                Acct a = accts.get(f[1]);
                a.bal = a.bal.add(rnd(a.bal.multiply(new BigDecimal(f[2])), curs.get(a.cur)));
                return null;
            }
            case "split": {
                int k = f.length - 3;
                String[] names = new String[k + 1];
                BigInteger[] w = new BigInteger[k];
                names[0] = f[1];
                for (int i = 0; i < k; i++) {
                    String t = f[3 + i];
                    int c = t.lastIndexOf(':');
                    names[i + 1] = t.substring(0, c);
                    w[i] = new BigInteger(t.substring(c + 1));
                }
                String u = unknown(names);
                if (u != null) return u;
                Acct a = accts.get(f[1]);
                for (int i = 1; i <= k; i++) if (!accts.get(names[i]).cur.equals(a.cur)) return "currency mismatch";
                int m = curs.get(a.cur);
                BigDecimal amt = rnd(new BigDecimal(f[2]), m);
                if (amt.signum() <= 0) return "non-positive amount";
                if (insufficient(a, amt)) return "insufficient funds " + f[1];
                BigInteger A = amt.unscaledValue();
                BigInteger W = BigInteger.ZERO;
                for (BigInteger x : w) W = W.add(x);
                BigInteger[] q = new BigInteger[k], rem = new BigInteger[k];
                BigInteger left = A;
                for (int i = 0; i < k; i++) {
                    BigInteger[] qr = A.multiply(w[i]).divideAndRemainder(W);
                    q[i] = qr[0]; rem[i] = qr[1];
                    left = left.subtract(q[i]);
                }
                Integer[] idx = new Integer[k];
                for (int i = 0; i < k; i++) idx[i] = i;
                final BigInteger[] rr = rem;
                Arrays.sort(idx, (x, y) -> { int c = rr[y].compareTo(rr[x]); return c != 0 ? c : Integer.compare(x, y); });
                int lft = left.intValueExact();
                for (int j = 0; j < lft; j++) q[idx[j]] = q[idx[j]].add(BigInteger.ONE);
                a.bal = a.bal.subtract(amt);
                for (int i = 0; i < k; i++) {
                    Acct t = accts.get(names[i + 1]);
                    t.bal = t.bal.add(new BigDecimal(q[i], m));
                }
                return null;
            }
        }
        return null;
    }
}
