#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { N_CHAR, N_ANY, N_CLASS, N_BOL, N_EOL, N_CAT, N_ALT, N_GROUP, N_REP };
typedef struct Node {
    int type, c, cls, gidx, min, max, greedy;
    int nkids, cap;
    struct Node **kids;
} Node;

static const char *P; static int PI, PLEN, NGROUPS;
static unsigned char CLS[4096][128]; static int NCLS;

static Node *mk(int t) { Node *n = calloc(1, sizeof(Node)); n->type = t; return n; }
static void add(Node *p, Node *k) {
    if (p->nkids == p->cap) { p->cap = p->cap ? p->cap * 2 : 4; p->kids = realloc(p->kids, sizeof(Node*) * p->cap); }
    p->kids[p->nkids++] = k;
}
static int inset(char e, int c) {
    switch (e) {
    case 'd': return c >= '0' && c <= '9';
    case 'w': return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
    case 's': return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
    case 'D': return !inset('d', c);
    case 'W': return !inset('w', c);
    case 'S': return !inset('s', c);
    }
    return 0;
}
static int isclassesc(char e) { return e && strchr("dwsDWS", e) != NULL; }

static Node *parse_regex(void);
static Node *parse_atom(void) {
    char ch = P[PI];
    if (ch == '(') {
        PI++;
        if (P[PI] == '?' && P[PI + 1] == ':') {
            PI += 2; Node *r = parse_regex(); PI++; return r;
        }
        Node *g = mk(N_GROUP); g->gidx = ++NGROUPS;
        add(g, parse_regex()); PI++; return g;
    }
    if (ch == '.') { PI++; return mk(N_ANY); }
    if (ch == '^') { PI++; return mk(N_BOL); }
    if (ch == '$') { PI++; return mk(N_EOL); }
    if (ch == '[') {
        PI++;
        int neg = 0;
        if (P[PI] == '^') { neg = 1; PI++; }
        int id = NCLS++;
        unsigned char *t = CLS[id]; memset(t, 0, 128);
        while (P[PI] != ']') {
            int lo;
            if (P[PI] == '\\') {
                char e = P[PI + 1]; PI += 2;
                if (isclassesc(e)) { for (int c = 0; c < 128; c++) if (inset(e, c)) t[c] = 1; continue; }
                lo = (unsigned char)e;
            } else lo = (unsigned char)P[PI++];
            int hi = lo;
            if (P[PI] == '-' && P[PI + 1] != ']') {
                PI++;
                if (P[PI] == '\\') { hi = (unsigned char)P[PI + 1]; PI += 2; }
                else hi = (unsigned char)P[PI++];
            }
            for (int c = lo; c <= hi && c < 128; c++) t[c] = 1;
        }
        PI++;
        if (neg) for (int c = 0; c < 128; c++) t[c] = !t[c];
        Node *n = mk(N_CLASS); n->cls = id; return n;
    }
    if (ch == '\\') {
        char e = P[PI + 1]; PI += 2;
        if (isclassesc(e)) {
            int id = NCLS++;
            for (int c = 0; c < 128; c++) CLS[id][c] = (unsigned char)inset(e, c);
            Node *n = mk(N_CLASS); n->cls = id; return n;
        }
        Node *n = mk(N_CHAR); n->c = (unsigned char)e; return n;
    }
    PI++;
    Node *n = mk(N_CHAR); n->c = (unsigned char)ch; return n;
}
static int readint(void) { int v = 0; while (P[PI] >= '0' && P[PI] <= '9') v = v * 10 + (P[PI++] - '0'); return v; }
static Node *parse_branch(void) {
    Node *cat = mk(N_CAT);
    while (PI < PLEN && P[PI] != '|' && P[PI] != ')') {
        Node *a = parse_atom();
        char q = P[PI];
        int mn = -1, mx = -1;
        if (q == '*') { mn = 0; mx = -1; PI++; }
        else if (q == '+') { mn = 1; mx = -1; PI++; }
        else if (q == '?') { mn = 0; mx = 1; PI++; }
        else if (q == '{') {
            PI++; mn = readint();
            if (P[PI] == '}') { mx = mn; PI++; }
            else { PI++; if (P[PI] == '}') { mx = -1; PI++; } else { mx = readint(); PI++; } }
        }
        if (mn >= 0) {
            Node *r = mk(N_REP); r->min = mn; r->max = mx; r->greedy = 1;
            if (P[PI] == '?') { r->greedy = 0; PI++; }
            add(r, a); a = r;
        }
        add(cat, a);
    }
    return cat;
}
static Node *parse_regex(void) {
    Node *alt = mk(N_ALT);
    add(alt, parse_branch());
    while (PI < PLEN && P[PI] == '|') { PI++; add(alt, parse_branch()); }
    return alt;
}

enum { I_CHAR, I_ANY, I_CLASS, I_BOL, I_EOL, I_SAVE, I_SPLIT, I_JMP, I_LOOP, I_ITEREND, I_MATCH };
typedef struct { int op, a, b, c; } Inst;
static Inst *PROG; static int NP, PCAP, MAXLEVEL;
static int emit(int op, int a, int b, int c) {
    if (NP == PCAP) { PCAP = PCAP ? PCAP * 2 : 256; PROG = realloc(PROG, sizeof(Inst) * PCAP); }
    PROG[NP].op = op; PROG[NP].a = a; PROG[NP].b = b; PROG[NP].c = c; return NP++;
}
static void compile(Node *n, int level) {
    if (level > MAXLEVEL) MAXLEVEL = level;
    switch (n->type) {
    case N_CHAR: emit(I_CHAR, n->c, 0, 0); break;
    case N_ANY: emit(I_ANY, 0, 0, 0); break;
    case N_CLASS: emit(I_CLASS, n->cls, 0, 0); break;
    case N_BOL: emit(I_BOL, 0, 0, 0); break;
    case N_EOL: emit(I_EOL, 0, 0, 0); break;
    case N_CAT: for (int i = 0; i < n->nkids; i++) compile(n->kids[i], level); break;
    case N_GROUP:
        emit(I_SAVE, 2 * n->gidx, 0, 0); compile(n->kids[0], level); emit(I_SAVE, 2 * n->gidx + 1, 0, 0); break;
    case N_ALT: {
        if (n->nkids == 1) { compile(n->kids[0], level); break; }
        int *jmps = malloc(sizeof(int) * n->nkids); int nj = 0;
        for (int i = 0; i < n->nkids; i++) {
            if (i < n->nkids - 1) {
                int s = emit(I_SPLIT, 0, 0, 0);
                PROG[s].a = NP;
                compile(n->kids[i], level);
                jmps[nj++] = emit(I_JMP, 0, 0, 0);
                PROG[s].b = NP;
            } else compile(n->kids[i], level);
        }
        for (int i = 0; i < nj; i++) PROG[jmps[i]].a = NP;
        free(jmps);
        break;
    }
    case N_REP: {
        Node *k = n->kids[0];
        for (int i = 0; i < n->min; i++) compile(k, level);
        if (n->max < 0) {
            int L = emit(I_LOOP, 0, 0, n->greedy);
            PROG[L].a = NP;
            compile(k, level + 1);
            int e = emit(I_ITEREND, L, 0, 0);
            PROG[L].b = NP; PROG[e].b = NP;
        } else if (n->max > n->min) {
            int cnt = n->max - n->min;
            int *loops = malloc(sizeof(int) * cnt), *ends = malloc(sizeof(int) * cnt);
            for (int j = 0; j < cnt; j++) {
                loops[j] = emit(I_LOOP, 0, 0, n->greedy);
                PROG[loops[j]].a = NP;
                compile(k, level + 1);
                ends[j] = emit(I_ITEREND, 0, 0, 0);
                PROG[ends[j]].a = NP; /* next copy (or chain end) */
            }
            for (int j = 0; j < cnt; j++) { PROG[loops[j]].b = NP; PROG[ends[j]].b = NP; }
            free(loops); free(ends);
        }
        break;
    }
    }
}

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    char path[4096]; snprintf(path, sizeof path, "%s/cases.txt", argv[1]);
    FILE *f = fopen(path, "rb"); if (!f) return 0;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1); fread(buf, 1, sz, f); buf[sz] = 0; fclose(f);
    unsigned char *memo = NULL; size_t memocap = 0;
    int *caps = NULL; int *logs = NULL; size_t logcap = 0; int *stk = NULL; size_t stkcap = 0;
    char *line = buf; int lineno = 0;
    while (*line) {
        char *nl = strchr(line, '\n'); if (!nl) nl = line + strlen(line);
        char *next = *nl ? nl + 1 : nl; *nl = 0;
        lineno++;
        char *tab = strchr(line, '\t');
        if (!tab) { line = next; continue; }
        *tab = 0;
        P = line; PLEN = (int)strlen(line); PI = 0; NGROUPS = 0; NCLS = 0;
        const char *S = tab + 1; int n = (int)strlen(S);
        Node *root = parse_regex();
        NP = 0; MAXLEVEL = 0;
        compile(root, 0);
        emit(I_MATCH, 0, 0, 0);
        int D = MAXLEVEL + 1;
        size_t msz = (size_t)NP * (n + 1) * D;
        if (msz > memocap) { memocap = msz; memo = realloc(memo, memocap); }
        memset(memo, 0, msz);
        int ncap = 2 * (NGROUPS + 1);
        caps = realloc(caps, sizeof(int) * ncap);
        int found = 0, mstart = 0, mend = 0;
        for (int s = 0; s <= n && !found; s++) {
            for (int i = 0; i < ncap; i++) caps[i] = -1;
            size_t nlog = 0, nst = 0;
            int pc = 0, pos = s, d = 0;
            for (;;) {
                size_t key = ((size_t)pc * (n + 1) + pos) * D + d;
                int ok = 1;
                if (memo[key]) ok = 0;
                else {
                    memo[key] = 1;
                    Inst *in = &PROG[pc];
                    switch (in->op) {
                    case I_CHAR: if (pos < n && (unsigned char)S[pos] == in->a) { pos++; d = 0; pc++; } else ok = 0; break;
                    case I_ANY: if (pos < n) { pos++; d = 0; pc++; } else ok = 0; break;
                    case I_CLASS: if (pos < n && CLS[in->a][(unsigned char)S[pos] & 127]) { pos++; d = 0; pc++; } else ok = 0; break;
                    case I_BOL: if (pos == 0) pc++; else ok = 0; break;
                    case I_EOL: if (pos == n) pc++; else ok = 0; break;
                    case I_SAVE:
                        if (nlog + 2 > logcap) { logcap = logcap ? logcap * 2 : 1024; logs = realloc(logs, sizeof(int) * logcap); }
                        logs[nlog++] = in->a; logs[nlog++] = caps[in->a];
                        caps[in->a] = pos; pc++; break;
                    case I_JMP: pc = in->a; break;
                    case I_SPLIT:
                    case I_LOOP: {
                        int first, fd, second, sd;
                        if (in->op == I_SPLIT) { first = in->a; fd = d; second = in->b; sd = d; }
                        else if (in->c) { first = in->a; fd = d + 1; second = in->b; sd = d; }
                        else { first = in->b; fd = d; second = in->a; sd = d + 1; }
                        if (nst + 4 > stkcap) { stkcap = stkcap ? stkcap * 2 : 1024; stk = realloc(stk, sizeof(int) * stkcap); }
                        stk[nst++] = second; stk[nst++] = pos; stk[nst++] = sd; stk[nst++] = (int)nlog;
                        pc = first; d = fd; break;
                    }
                    case I_ITEREND:
                        if (d >= 1) { d--; pc = in->b; }
                        else pc = in->a;
                        break;
                    case I_MATCH: found = 1; mstart = s; mend = pos; break;
                    }
                }
                if (found) break;
                if (!ok) {
                    if (nst == 0) break;
                    int lh = stk[--nst]; d = stk[--nst]; pos = stk[--nst]; pc = stk[--nst];
                    while ((int)nlog > lh) { int old = logs[--nlog]; int slot = logs[--nlog]; caps[slot] = old; }
                }
            }
        }
        if (!found) printf("%d: no match\n", lineno);
        else {
            printf("%d: %d-%d", lineno, mstart, mend);
            for (int g = 1; g <= NGROUPS; g++) {
                if (caps[2 * g] < 0 || caps[2 * g + 1] < 0) printf(" -");
                else printf(" %d-%d", caps[2 * g], caps[2 * g + 1]);
            }
            printf("\n");
        }
        line = next;
    }
    return 0;
}
