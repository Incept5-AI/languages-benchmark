import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Main {
    static final int N_CHAR = 0, N_ANY = 1, N_CLASS = 2, N_BOL = 3, N_EOL = 4, N_CAT = 5, N_ALT = 6, N_GROUP = 7, N_REP = 8;

    static class Node {
        int type, c, cls, gidx, min, max;
        boolean greedy;
        List<Node> kids = new ArrayList<>();
        Node(int t) { type = t; }
    }

    static boolean inset(char e, int c) {
        switch (e) {
            case 'd': return c >= '0' && c <= '9';
            case 'w': return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
            case 's': return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == 12 || c == 11;
            case 'D': return !inset('d', c);
            case 'W': return !inset('w', c);
            case 'S': return !inset('s', c);
        }
        return false;
    }
    static boolean isClsEsc(char e) { return "dwsDWS".indexOf(e) >= 0 && e != 0; }

    static String P; static int PI, NGROUPS;
    static List<boolean[]> CLS = new ArrayList<>();

    static char peek(int k) { return PI + k < P.length() ? P.charAt(PI + k) : 0; }

    static Node parseRegex() {
        Node alt = new Node(N_ALT);
        alt.kids.add(parseBranch());
        while (peek(0) == '|') { PI++; alt.kids.add(parseBranch()); }
        return alt;
    }
    static int readInt() {
        int v = 0;
        while (peek(0) >= '0' && peek(0) <= '9') { v = v * 10 + (peek(0) - '0'); PI++; }
        return v;
    }
    static Node parseBranch() {
        Node cat = new Node(N_CAT);
        while (PI < P.length() && peek(0) != '|' && peek(0) != ')') {
            Node a = parseAtom();
            char q = peek(0);
            int mn = -1, mx = -1;
            if (q == '*') { mn = 0; PI++; }
            else if (q == '+') { mn = 1; PI++; }
            else if (q == '?') { mn = 0; mx = 1; PI++; }
            else if (q == '{') {
                PI++; mn = readInt();
                if (peek(0) == '}') { mx = mn; PI++; }
                else { PI++; if (peek(0) == '}') { mx = -1; PI++; } else { mx = readInt(); PI++; } }
            }
            if (mn >= 0) {
                Node r = new Node(N_REP); r.min = mn; r.max = mx; r.greedy = true;
                if (peek(0) == '?') { r.greedy = false; PI++; }
                r.kids.add(a); a = r;
            }
            cat.kids.add(a);
        }
        return cat;
    }
    static Node parseAtom() {
        char ch = peek(0);
        if (ch == '(') {
            PI++;
            if (peek(0) == '?' && peek(1) == ':') { PI += 2; Node r = parseRegex(); PI++; return r; }
            Node g = new Node(N_GROUP); g.gidx = ++NGROUPS;
            g.kids.add(parseRegex()); PI++; return g;
        }
        if (ch == '.') { PI++; return new Node(N_ANY); }
        if (ch == '^') { PI++; return new Node(N_BOL); }
        if (ch == '$') { PI++; return new Node(N_EOL); }
        if (ch == '[') {
            PI++;
            boolean neg = false;
            if (peek(0) == '^') { neg = true; PI++; }
            boolean[] t = new boolean[128];
            while (PI < P.length() && peek(0) != ']') {
                int lo;
                if (peek(0) == '\\') {
                    char e = peek(1); PI += 2;
                    if (isClsEsc(e)) { for (int c = 0; c < 128; c++) if (inset(e, c)) t[c] = true; continue; }
                    lo = e;
                } else { lo = peek(0); PI++; }
                int hi = lo;
                if (peek(0) == '-' && peek(1) != ']') {
                    PI++;
                    if (peek(0) == '\\') { hi = peek(1); PI += 2; } else { hi = peek(0); PI++; }
                }
                for (int c = lo; c <= hi && c < 128; c++) t[c] = true;
            }
            PI++;
            if (neg) for (int c = 0; c < 128; c++) t[c] = !t[c];
            CLS.add(t);
            Node n = new Node(N_CLASS); n.cls = CLS.size() - 1; return n;
        }
        if (ch == '\\') {
            char e = peek(1); PI += 2;
            if (isClsEsc(e)) {
                boolean[] t = new boolean[128];
                for (int c = 0; c < 128; c++) t[c] = inset(e, c);
                CLS.add(t);
                Node n = new Node(N_CLASS); n.cls = CLS.size() - 1; return n;
            }
            Node n = new Node(N_CHAR); n.c = e; return n;
        }
        PI++;
        Node n = new Node(N_CHAR); n.c = ch; return n;
    }

    static final int I_CHAR = 0, I_ANY = 1, I_CLASS = 2, I_BOL = 3, I_EOL = 4, I_SAVE = 5, I_SPLIT = 6, I_JMP = 7, I_LOOP = 8, I_ITEREND = 9, I_MATCH = 10;
    static int[] OP = new int[256], A = new int[256], B = new int[256], C = new int[256];
    static int NP, MAXLEVEL;
    static int emit(int op, int a, int b, int c) {
        if (NP == OP.length) {
            int nc = NP * 2;
            OP = Arrays.copyOf(OP, nc); A = Arrays.copyOf(A, nc); B = Arrays.copyOf(B, nc); C = Arrays.copyOf(C, nc);
        }
        OP[NP] = op; A[NP] = a; B[NP] = b; C[NP] = c; return NP++;
    }
    static void compile(Node n, int level) {
        if (level > MAXLEVEL) MAXLEVEL = level;
        switch (n.type) {
            case N_CHAR: emit(I_CHAR, n.c, 0, 0); break;
            case N_ANY: emit(I_ANY, 0, 0, 0); break;
            case N_CLASS: emit(I_CLASS, n.cls, 0, 0); break;
            case N_BOL: emit(I_BOL, 0, 0, 0); break;
            case N_EOL: emit(I_EOL, 0, 0, 0); break;
            case N_CAT: for (Node k : n.kids) compile(k, level); break;
            case N_GROUP:
                emit(I_SAVE, 2 * n.gidx, 0, 0); compile(n.kids.get(0), level); emit(I_SAVE, 2 * n.gidx + 1, 0, 0); break;
            case N_ALT: {
                int k = n.kids.size();
                if (k == 1) { compile(n.kids.get(0), level); break; }
                List<Integer> jmps = new ArrayList<>();
                for (int i = 0; i < k; i++) {
                    if (i < k - 1) {
                        int s = emit(I_SPLIT, 0, 0, 0);
                        A[s] = NP;
                        compile(n.kids.get(i), level);
                        jmps.add(emit(I_JMP, 0, 0, 0));
                        B[s] = NP;
                    } else compile(n.kids.get(i), level);
                }
                for (int j : jmps) A[j] = NP;
                break;
            }
            case N_REP: {
                Node k = n.kids.get(0);
                for (int i = 0; i < n.min; i++) compile(k, level);
                int g = n.greedy ? 1 : 0;
                if (n.max < 0) {
                    int l = emit(I_LOOP, 0, 0, g);
                    A[l] = NP;
                    compile(k, level + 1);
                    int e = emit(I_ITEREND, l, 0, 0);
                    B[l] = NP; B[e] = NP;
                } else if (n.max > n.min) {
                    int cnt = n.max - n.min;
                    int[] loops = new int[cnt], ends = new int[cnt];
                    for (int j = 0; j < cnt; j++) {
                        loops[j] = emit(I_LOOP, 0, 0, g);
                        A[loops[j]] = NP;
                        compile(k, level + 1);
                        ends[j] = emit(I_ITEREND, 0, 0, 0);
                        A[ends[j]] = NP;
                    }
                    for (int j = 0; j < cnt; j++) { B[loops[j]] = NP; B[ends[j]] = NP; }
                }
                break;
            }
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) return;
        String data = new String(Files.readAllBytes(Paths.get(args[0], "cases.txt")), StandardCharsets.ISO_8859_1);
        String[] lines = data.split("\n", -1);
        int nlines = lines.length;
        if (nlines > 0 && lines[nlines - 1].isEmpty()) nlines--;
        StringBuilder out = new StringBuilder();
        byte[] memo = new byte[0];
        int[] logs = new int[1024]; int[] stk = new int[1024];
        for (int li = 0; li < nlines; li++) {
            int lineno = li + 1;
            String line = lines[li];
            int tab = line.indexOf('\t');
            if (tab < 0) continue;
            P = line.substring(0, tab); PI = 0; NGROUPS = 0; CLS.clear();
            String s = line.substring(tab + 1);
            int n = s.length();
            int[] sc = new int[n];
            for (int i = 0; i < n; i++) sc[i] = s.charAt(i) & 127;
            Node root = parseRegex();
            NP = 0; MAXLEVEL = 0;
            compile(root, 0);
            emit(I_MATCH, 0, 0, 0);
            int D = MAXLEVEL + 1;
            int msz = NP * (n + 1) * D;
            if (memo.length < msz) memo = new byte[msz]; else Arrays.fill(memo, 0, msz, (byte) 0);
            int ncap = 2 * (NGROUPS + 1);
            int[] caps = new int[ncap];
            boolean found = false; int ms = 0, me = 0;
            for (int st = 0; st <= n && !found; st++) {
                Arrays.fill(caps, -1);
                int nlog = 0, nst = 0;
                int pc = 0, pos = st, d = 0;
                for (;;) {
                    int key = (pc * (n + 1) + pos) * D + d;
                    boolean ok = true;
                    if (memo[key] != 0) ok = false;
                    else {
                        memo[key] = 1;
                        switch (OP[pc]) {
                            case I_CHAR: if (pos < n && sc[pos] == A[pc]) { pos++; d = 0; pc++; } else ok = false; break;
                            case I_ANY: if (pos < n) { pos++; d = 0; pc++; } else ok = false; break;
                            case I_CLASS: if (pos < n && CLS.get(A[pc])[sc[pos]]) { pos++; d = 0; pc++; } else ok = false; break;
                            case I_BOL: if (pos == 0) pc++; else ok = false; break;
                            case I_EOL: if (pos == n) pc++; else ok = false; break;
                            case I_SAVE:
                                if (nlog + 2 > logs.length) logs = Arrays.copyOf(logs, logs.length * 2);
                                logs[nlog++] = A[pc]; logs[nlog++] = caps[A[pc]];
                                caps[A[pc]] = pos; pc++; break;
                            case I_JMP: pc = A[pc]; break;
                            case I_SPLIT:
                            case I_LOOP: {
                                int first, fd, second, sd;
                                if (OP[pc] == I_SPLIT) { first = A[pc]; fd = d; second = B[pc]; sd = d; }
                                else if (C[pc] != 0) { first = A[pc]; fd = d + 1; second = B[pc]; sd = d; }
                                else { first = B[pc]; fd = d; second = A[pc]; sd = d + 1; }
                                if (nst + 4 > stk.length) stk = Arrays.copyOf(stk, stk.length * 2);
                                stk[nst++] = second; stk[nst++] = pos; stk[nst++] = sd; stk[nst++] = nlog;
                                pc = first; d = fd; break;
                            }
                            case I_ITEREND: if (d >= 1) { d--; pc = B[pc]; } else pc = A[pc]; break;
                            case I_MATCH: found = true; ms = st; me = pos; break;
                        }
                    }
                    if (found) break;
                    if (!ok) {
                        if (nst == 0) break;
                        int lh = stk[--nst]; d = stk[--nst]; pos = stk[--nst]; pc = stk[--nst];
                        while (nlog > lh) { int old = logs[--nlog]; int slot = logs[--nlog]; caps[slot] = old; }
                    }
                }
            }
            if (!found) out.append(lineno).append(": no match\n");
            else {
                out.append(lineno).append(": ").append(ms).append('-').append(me);
                for (int g = 1; g <= NGROUPS; g++) {
                    if (caps[2 * g] < 0 || caps[2 * g + 1] < 0) out.append(" -");
                    else out.append(' ').append(caps[2 * g]).append('-').append(caps[2 * g + 1]);
                }
                out.append('\n');
            }
        }
        PrintStream ps = new PrintStream(new FileOutputStream(FileDescriptor.out), false, "ISO-8859-1");
        ps.print(out);
        ps.flush();
    }
}
