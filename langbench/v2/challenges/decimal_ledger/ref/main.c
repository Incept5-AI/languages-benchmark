#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define BASE 1000000000ULL

typedef struct { int n; uint32_t *d; } Mag; /* little-endian base 1e9, no leading zero limbs */
typedef struct { int neg; Mag m; } Big;

static Mag mnew(int cap) { Mag r; r.n = 0; r.d = calloc(cap > 0 ? cap : 1, sizeof(uint32_t)); return r; }
static void mtrim(Mag *a) { while (a->n > 0 && a->d[a->n - 1] == 0) a->n--; }
static int mcmp(Mag a, Mag b) {
    if (a.n != b.n) return a.n < b.n ? -1 : 1;
    for (int i = a.n - 1; i >= 0; i--) if (a.d[i] != b.d[i]) return a.d[i] < b.d[i] ? -1 : 1;
    return 0;
}
static Mag madd(Mag a, Mag b) {
    int n = a.n > b.n ? a.n : b.n;
    Mag r = mnew(n + 1);
    uint64_t c = 0;
    for (int i = 0; i < n; i++) {
        uint64_t s = c + (i < a.n ? a.d[i] : 0) + (i < b.n ? b.d[i] : 0);
        r.d[i] = (uint32_t)(s % BASE); c = s / BASE;
    }
    r.n = n;
    if (c) r.d[r.n++] = (uint32_t)c;
    return r;
}
static Mag msub(Mag a, Mag b) { /* a >= b */
    Mag r = mnew(a.n);
    int64_t br = 0;
    for (int i = 0; i < a.n; i++) {
        int64_t s = (int64_t)a.d[i] - br - (i < b.n ? b.d[i] : 0);
        if (s < 0) { s += BASE; br = 1; } else br = 0;
        r.d[i] = (uint32_t)s;
    }
    r.n = a.n; mtrim(&r); return r;
}
static Mag mmul(Mag a, Mag b) {
    if (a.n == 0 || b.n == 0) return mnew(1);
    int n = a.n + b.n + 1;
    uint64_t *t = calloc(n, sizeof(uint64_t));
    for (int i = 0; i < a.n; i++) {
        uint64_t c = 0;
        for (int j = 0; j < b.n; j++) {
            uint64_t x = t[i + j] + (uint64_t)a.d[i] * b.d[j] + c;
            t[i + j] = x % BASE; c = x / BASE;
        }
        int k = i + b.n;
        while (c) { uint64_t x = t[k] + c; t[k] = x % BASE; c = x / BASE; k++; }
    }
    Mag r = mnew(n);
    for (int i = 0; i < n; i++) r.d[i] = (uint32_t)t[i];
    r.n = n; mtrim(&r); free(t); return r;
}
static Mag msmall(uint32_t v) { Mag r = mnew(1); if (v) { r.d[0] = v; r.n = 1; } return r; }
static Mag mfrom(const char *s, int len) {
    Mag r = mnew(len / 9 + 2);
    int end = len;
    while (end > 0) {
        int st = end >= 9 ? end - 9 : 0;
        uint32_t x = 0;
        for (int i = st; i < end; i++) x = x * 10 + (uint32_t)(s[i] - '0');
        r.d[r.n++] = x; end = st;
    }
    mtrim(&r); return r;
}
static char *mstr(Mag a) {
    char *s = malloc((size_t)a.n * 9 + 2);
    if (a.n == 0) { strcpy(s, "0"); return s; }
    int p = sprintf(s, "%u", a.d[a.n - 1]);
    for (int i = a.n - 2; i >= 0; i--) p += sprintf(s + p, "%09u", a.d[i]);
    return s;
}
static void mdivmod(Mag a, Mag b, Mag *q, Mag *rem) {
    char *ds = mstr(a);
    int L = (int)strlen(ds);
    char *qs = malloc(L + 1);
    Mag r = mnew(1), ten = msmall(10);
    for (int i = 0; i < L; i++) {
        r = madd(mmul(r, ten), msmall((uint32_t)(ds[i] - '0')));
        int d = 0;
        while (mcmp(r, b) >= 0) { r = msub(r, b); d++; }
        qs[i] = (char)('0' + d);
    }
    qs[L] = 0;
    *q = mfrom(qs, L); *rem = r;
}

static Big bnorm(Big x) { mtrim(&x.m); if (x.m.n == 0) x.neg = 0; return x; }
static Big bigzero(void) { Big b; b.neg = 0; b.m = mnew(1); return b; }
static int bsign(Big x) { return x.m.n == 0 ? 0 : (x.neg ? -1 : 1); }
static Big bneg(Big x) { x.neg = !x.neg; return bnorm(x); }
static Big badd(Big a, Big b) {
    Big r;
    if (a.neg == b.neg) { r.neg = a.neg; r.m = madd(a.m, b.m); }
    else if (mcmp(a.m, b.m) < 0) { r.neg = b.neg; r.m = msub(b.m, a.m); }
    else { r.neg = a.neg; r.m = msub(a.m, b.m); }
    return bnorm(r);
}
static Big bsub(Big a, Big b) { return badd(a, bneg(b)); }
static Big bmul(Big a, Big b) { Big r; r.neg = a.neg != b.neg; r.m = mmul(a.m, b.m); return bnorm(r); }
static int bigcmp(Big a, Big b) {
    int sa = bsign(a), sb = bsign(b);
    if (sa != sb) return sa < sb ? -1 : 1;
    int c = mcmp(a.m, b.m);
    return a.neg ? -c : c;
}

typedef struct { Big v; int s; } Dec;
static Dec parse(const char *t) {
    Dec d; int neg = 0;
    if (*t == '-') { neg = 1; t++; }
    size_t L = strlen(t);
    char *buf = malloc(L + 1); int n = 0; int s = 0, after = 0;
    for (size_t i = 0; i < L; i++) {
        if (t[i] == '.') { after = 1; continue; }
        buf[n++] = t[i]; if (after) s++;
    }
    d.v.neg = neg; d.v.m = mfrom(buf, n); d.v = bnorm(d.v); d.s = s;
    free(buf);
    return d;
}
static Big rnd(Dec d, int m) {
    char *s = mstr(d.v.m);
    int L = (int)strlen(s);
    Big r; r.neg = d.v.neg;
    if (d.s <= m) {
        int k = m - d.s;
        char *b = malloc(L + k + 1);
        memcpy(b, s, L); memset(b + L, '0', k); b[L + k] = 0;
        r.m = mfrom(b, L + k);
        return bnorm(r);
    }
    int k = d.s - m;
    if (L <= k) {
        char *b = malloc(k + 2);
        int pad = k + 1 - L;
        memset(b, '0', pad); memcpy(b + pad, s, L); b[k + 1] = 0;
        s = b; L = k + 1;
    }
    const char *tail = s + (L - k);
    int c = 0;
    if (tail[0] > '5') c = 1;
    else if (tail[0] < '5') c = -1;
    else { for (int i = 1; i < k; i++) if (tail[i] != '0') { c = 1; break; } }
    int odd = (s[L - k - 1] - '0') % 2;
    r.m = mfrom(s, L - k);
    if (c > 0 || (c == 0 && odd)) r.m = madd(r.m, msmall(1));
    return bnorm(r);
}
static char *fmt(Big v, int m) {
    char *s = mstr(v.m);
    int L = (int)strlen(s);
    if (L <= m) {
        char *b = malloc(m + 2);
        int pad = m + 1 - L;
        memset(b, '0', pad); memcpy(b + pad, s, L); b[m + 1] = 0;
        s = b; L = m + 1;
    }
    char *o = malloc(L * 2 + 4); int p = 0;
    if (bsign(v) < 0) o[p++] = '-';
    int n = L - m;
    for (int i = 0; i < n; i++) {
        if (i > 0 && (n - i) % 3 == 0) o[p++] = ',';
        o[p++] = s[i];
    }
    if (m > 0) { o[p++] = '.'; memcpy(o + p, s + n, m); p += m; }
    o[p] = 0;
    return o;
}

typedef struct { char code[64]; int minor; } Cur;
typedef struct { char name[256]; int cur; Big bal, limit; } Acct;
static Cur *curs; static int ncur, capcur;
static Acct *accts; static int nacc, capacc;

static int findcur(const char *c) { for (int i = 0; i < ncur; i++) if (!strcmp(curs[i].code, c)) return i; return -1; }
static int findacc(const char *n) { for (int i = 0; i < nacc; i++) if (!strcmp(accts[i].name, n)) return i; return -1; }

static char reason[600];
static int unknown(char **names, int k) {
    for (int i = 0; i < k; i++) if (findacc(names[i]) < 0) { snprintf(reason, sizeof reason, "unknown account %s", names[i]); return 1; }
    return 0;
}
static int insufficient(Acct *a, Big amt) { return bigcmp(bsub(a->bal, amt), bneg(a->limit)) < 0; }
#define REJ(...) do { snprintf(reason, sizeof reason, __VA_ARGS__); return 1; } while (0)

static int exec(char **f, int nf) {
    const char *cmd = f[0];
    if (!strcmp(cmd, "currency")) {
        int i = findcur(f[1]);
        if (i < 0) {
            if (ncur == capcur) { capcur = capcur ? capcur * 2 : 16; curs = realloc(curs, capcur * sizeof(Cur)); }
            i = ncur++; snprintf(curs[i].code, sizeof curs[i].code, "%s", f[1]);
        }
        curs[i].minor = atoi(f[2]);
        return 0;
    }
    if (!strcmp(cmd, "open")) {
        if (findacc(f[1]) >= 0) REJ("account exists %s", f[1]);
        int c = findcur(f[2]);
        if (nacc == capacc) { capacc = capacc ? capacc * 2 : 16; accts = realloc(accts, capacc * sizeof(Acct)); }
        Acct *a = &accts[nacc++];
        snprintf(a->name, sizeof a->name, "%s", f[1]);
        a->cur = c; a->bal = bigzero();
        a->limit = nf > 3 ? rnd(parse(f[3]), curs[c].minor) : bigzero();
        return 0;
    }
    if (!strcmp(cmd, "deposit") || !strcmp(cmd, "withdraw")) {
        if (unknown(f + 1, 1)) return 1;
        Acct *a = &accts[findacc(f[1])];
        Big amt = rnd(parse(f[2]), curs[a->cur].minor);
        if (bsign(amt) <= 0) REJ("non-positive amount");
        if (cmd[0] == 'w') {
            if (insufficient(a, amt)) REJ("insufficient funds %s", f[1]);
            a->bal = bsub(a->bal, amt);
        } else a->bal = badd(a->bal, amt);
        return 0;
    }
    if (!strcmp(cmd, "transfer")) {
        if (unknown(f + 1, 2)) return 1;
        Acct *a = &accts[findacc(f[1])], *b = &accts[findacc(f[2])];
        if (a->cur != b->cur) REJ("currency mismatch");
        Big amt = rnd(parse(f[3]), curs[a->cur].minor);
        if (bsign(amt) <= 0) REJ("non-positive amount");
        if (insufficient(a, amt)) REJ("insufficient funds %s", f[1]);
        a->bal = bsub(a->bal, amt); b->bal = badd(b->bal, amt);
        return 0;
    }
    if (!strcmp(cmd, "convert")) {
        if (unknown(f + 1, 2)) return 1;
        Acct *a = &accts[findacc(f[1])], *b = &accts[findacc(f[2])];
        int ma = curs[a->cur].minor, mb = curs[b->cur].minor;
        Big amt = rnd(parse(f[3]), ma);
        if (bsign(amt) <= 0) REJ("non-positive amount");
        if (insufficient(a, amt)) REJ("insufficient funds %s", f[1]);
        Dec r = parse(f[4]);
        Dec p; p.v = bmul(amt, r.v); p.s = ma + r.s;
        Big got = rnd(p, mb);
        a->bal = bsub(a->bal, amt); b->bal = badd(b->bal, got);
        return 0;
    }
    if (!strcmp(cmd, "interest")) {
        if (unknown(f + 1, 1)) return 1;
        Acct *a = &accts[findacc(f[1])];
        int m = curs[a->cur].minor;
        Dec r = parse(f[2]);
        Dec p; p.v = bmul(a->bal, r.v); p.s = m + r.s;
        a->bal = badd(a->bal, rnd(p, m));
        return 0;
    }
    if (!strcmp(cmd, "split")) {
        int k = nf - 3;
        char **names = malloc(sizeof(char *) * (k + 1));
        Mag *w = malloc(sizeof(Mag) * (k > 0 ? k : 1));
        names[0] = f[1];
        for (int i = 0; i < k; i++) {
            char *t = f[3 + i];
            char *c = strrchr(t, ':');
            *c = 0;
            names[i + 1] = t;
            w[i] = mfrom(c + 1, (int)strlen(c + 1));
        }
        if (unknown(names, k + 1)) return 1;
        Acct *a = &accts[findacc(f[1])];
        for (int i = 1; i <= k; i++) if (accts[findacc(names[i])].cur != a->cur) REJ("currency mismatch");
        Big amt = rnd(parse(f[2]), curs[a->cur].minor);
        if (bsign(amt) <= 0) REJ("non-positive amount");
        if (insufficient(a, amt)) REJ("insufficient funds %s", f[1]);
        Mag W = mnew(1);
        for (int i = 0; i < k; i++) W = madd(W, w[i]);
        Mag *q = malloc(sizeof(Mag) * k), *rem = malloc(sizeof(Mag) * k);
        Mag given = mnew(1);
        for (int i = 0; i < k; i++) { mdivmod(mmul(amt.m, w[i]), W, &q[i], &rem[i]); given = madd(given, q[i]); }
        long left = atol(mstr(msub(amt.m, given)));
        int *idx = malloc(sizeof(int) * k);
        for (int i = 0; i < k; i++) idx[i] = i;
        for (int i = 1; i < k; i++) { /* stable insertion sort by remainder desc */
            int x = idx[i], j = i - 1;
            while (j >= 0 && mcmp(rem[idx[j]], rem[x]) < 0) { idx[j + 1] = idx[j]; j--; }
            idx[j + 1] = x;
        }
        for (long j = 0; j < left; j++) q[idx[j]] = madd(q[idx[j]], msmall(1));
        a->bal = bsub(a->bal, amt);
        for (int i = 0; i < k; i++) {
            Acct *t = &accts[findacc(names[i + 1])];
            Big g; g.neg = 0; g.m = q[i];
            t->bal = badd(t->bal, bnorm(g));
        }
        return 0;
    }
    return 0;
}

static int cmpacc(const void *x, const void *y) { return strcmp(((const Acct *)x)->name, ((const Acct *)y)->name); }

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    char path[4096];
    snprintf(path, sizeof path, "%s/ledger.txt", argv[1]);
    FILE *fp = fopen(path, "rb");
    if (!fp) return 0;
    fseek(fp, 0, SEEK_END); long sz = ftell(fp); fseek(fp, 0, SEEK_SET);
    char *text = malloc(sz + 1);
    sz = (long)fread(text, 1, sz, fp); text[sz] = 0; fclose(fp);

    size_t ocap = 1 << 16, olen = 0;
    char *out = malloc(ocap);
#define OUT(s) do { size_t _l = strlen(s); while (olen + _l + 1 > ocap) { ocap *= 2; out = realloc(out, ocap); } memcpy(out + olen, s, _l); olen += _l; } while (0)

    int lineno = 0;
    char *p = text;
    while (*p || p == text) {
        if (!*p) break;
        char *e = strchr(p, '\n');
        char *next = e ? e + 1 : p + strlen(p);
        if (e) *e = 0;
        lineno++;
        /* strip CR and surrounding spaces */
        char *ln = p;
        size_t L = strlen(ln);
        while (L > 0 && (ln[L - 1] == '\r' || ln[L - 1] == ' ' || ln[L - 1] == '\t')) ln[--L] = 0;
        while (*ln == ' ' || *ln == '\t') ln++;
        if (*ln && *ln != '#') {
            char *fields[4096]; int nf = 0;
            char *tok = strtok(ln, " ");
            while (tok && nf < 4096) { fields[nf++] = tok; tok = strtok(NULL, " "); }
            if (nf > 0 && exec(fields, nf)) {
                char buf[800];
                snprintf(buf, sizeof buf, "line %d: REJECT %s\n", lineno, reason);
                OUT(buf);
            }
        }
        p = next;
    }
    OUT("\n");
    qsort(accts, nacc, sizeof(Acct), cmpacc);
    for (int i = 0; i < nacc; i++) {
        Acct *a = &accts[i];
        OUT(a->name); OUT(" "); OUT(curs[a->cur].code); OUT(" "); OUT(fmt(a->bal, curs[a->cur].minor)); OUT("\n");
    }
    OUT("\n");
    int *order = malloc(sizeof(int) * (ncur + 1));
    for (int i = 0; i < ncur; i++) order[i] = i;
    for (int i = 1; i < ncur; i++) { int x = order[i], j = i - 1; while (j >= 0 && strcmp(curs[order[j]].code, curs[x].code) > 0) { order[j + 1] = order[j]; j--; } order[j + 1] = x; }
    for (int oi = 0; oi < ncur; oi++) {
        int c = order[oi];
        Big s = bigzero(); int cnt = 0;
        for (int i = 0; i < nacc; i++) if (accts[i].cur == c) { s = badd(s, accts[i].bal); cnt++; }
        if (!cnt) continue;
        char buf[128];
        OUT("total "); OUT(curs[c].code); OUT(" "); OUT(fmt(s, curs[c].minor));
        snprintf(buf, sizeof buf, " (%d accounts)\n", cnt); OUT(buf);
    }
    fwrite(out, 1, olen, stdout);
    return 0;
}
