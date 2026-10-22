#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>

typedef struct { unsigned char *p; size_t n; } Line;
typedef struct { Line *v; size_t n, cap; } Lines;

static void lpush(Lines *L, Line l) {
    if (L->n == L->cap) { L->cap = L->cap ? L->cap * 2 : 16; L->v = realloc(L->v, L->cap * sizeof(Line)); }
    L->v[L->n++] = l;
}

typedef struct { long a, b, d; int malformed; Lines oldL, newL; } Hunk;
typedef struct { char *path; const char *kind; Hunk *h; size_t nh, caph; } Section;

static unsigned char *readFile(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    size_t cap = 65536, n = 0;
    unsigned char *buf = malloc(cap);
    size_t r;
    while ((r = fread(buf + n, 1, cap - n, f)) > 0) {
        n += r;
        if (n == cap) { cap *= 2; buf = realloc(buf, cap); }
    }
    fclose(f);
    *len = n;
    return buf;
}

static Lines splitLines(unsigned char *d, size_t n) {
    Lines L = {0};
    size_t start = 0;
    for (size_t i = 0; i < n; i++) {
        if (d[i] == '\n') { Line l = { d + start, i + 1 - start }; lpush(&L, l); start = i + 1; }
    }
    if (start < n) { Line l = { d + start, n - start }; lpush(&L, l); }
    return L;
}

static int sw(Line l, const char *s) {
    size_t k = strlen(s);
    return l.n >= k && memcmp(l.p, s, k) == 0;
}

static char *lstr(Line l) {
    size_t n = l.n;
    while (n > 0 && (l.p[n - 1] == '\n' || l.p[n - 1] == '\r')) n--;
    char *s = malloc(n + 1);
    memcpy(s, l.p, n);
    s[n] = 0;
    return s;
}

static int parseRange(const char **pp, long *x, long *y) {
    const char *p = *pp;
    if (*p < '0' || *p > '9') return 0;
    char *e;
    *x = strtol(p, &e, 10);
    p = e;
    if (*p == ',') {
        p++;
        if (*p < '0' || *p > '9') return 0;
        *y = strtol(p, &e, 10);
        p = e;
    } else *y = 1;
    *pp = p;
    return 1;
}

static int parseHeader(Line l, long *a, long *b, long *d) {
    char *s = lstr(l);
    const char *p = s;
    long c;
    int ok = 0;
    if (strncmp(p, "@@ -", 4) == 0) {
        p += 4;
        if (parseRange(&p, a, b) && *p == ' ' && p[1] == '+') {
            p += 2;
            if (parseRange(&p, &c, d) && strncmp(p, " @@", 3) == 0) ok = 1;
        }
    }
    free(s);
    return ok;
}

static int leq(Line x, Line y) { return x.n == y.n && memcmp(x.p, y.p, x.n) == 0; }

static int cmpSec(const void *x, const void *y) {
    const Section *a = x, *b = y;
    return strcmp(a->path, b->path); /* strcmp compares as unsigned char */
}

static uint32_t table[256];

int main(int argc, char **argv) {
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        table[n] = c;
    }
    const char *root = argv[1];
    char *dp = malloc(strlen(root) + 32);
    sprintf(dp, "%s/changes.diff", root);
    size_t dlen = 0;
    unsigned char *diff = readFile(dp, &dlen);
    if (!diff) { diff = malloc(1); dlen = 0; }
    Lines lines = splitLines(diff, dlen);

    Section *secs = NULL;
    size_t ns = 0, caps = 0;
    int inHunk = 0;
    static unsigned char NL[1] = {'\n'};
    for (size_t i = 0; i < lines.n; i++) {
        Line l = lines.v[i];
        if (sw(l, "diff --git ")) {
            char *s = lstr(l);
            char *p = s + 11;
            char *e = strchr(p, ' ');
            if (e) *e = 0;
            if (strncmp(p, "a/", 2) == 0) p += 2;
            if (ns == caps) { caps = caps ? caps * 2 : 16; secs = realloc(secs, caps * sizeof(Section)); }
            Section sec = {0};
            sec.path = strdup(p);
            sec.kind = "modify";
            secs[ns++] = sec;
            free(s);
            inHunk = 0;
            continue;
        }
        if (ns == 0) continue;
        Section *cur = &secs[ns - 1];
        if (sw(l, "@@ ")) {
            if (cur->nh == cur->caph) { cur->caph = cur->caph ? cur->caph * 2 : 8; cur->h = realloc(cur->h, cur->caph * sizeof(Hunk)); }
            Hunk h;
            memset(&h, 0, sizeof h);
            if (!parseHeader(l, &h.a, &h.b, &h.d)) h.malformed = 1;
            cur->h[cur->nh++] = h;
            inHunk = 1;
            continue;
        }
        if (!inHunk) {
            if (sw(l, "new file mode")) cur->kind = "create";
            else if (sw(l, "deleted file mode")) cur->kind = "delete";
            continue;
        }
        if (l.n == 0) continue;
        Hunk *h = &cur->h[cur->nh - 1];
        unsigned char mk = l.p[0];
        if (mk == '\\') continue;
        Line c = { l.p + 1, l.n - 1 };
        int noNl = i + 1 < lines.n && lines.v[i + 1].n > 0 && lines.v[i + 1].p[0] == '\\';
        if (noNl && c.n > 0 && c.p[c.n - 1] == '\n') c.n--;
        if (mk == ' ') { lpush(&h->oldL, c); lpush(&h->newL, c); }
        else if (mk == '-') lpush(&h->oldL, c);
        else if (mk == '+') lpush(&h->newL, c);
        else if (mk == '\n') { Line nl = { NL, 1 }; lpush(&h->oldL, nl); lpush(&h->newL, nl); }
    }
    for (size_t s = 0; s < ns; s++)
        for (size_t k = 0; k < secs[s].nh; k++) {
            Hunk *h = &secs[s].h[k];
            if ((long)h->oldL.n != h->b || (long)h->newL.n != h->d) h->malformed = 1;
        }
    qsort(secs, ns, sizeof(Section), cmpSec);

    for (size_t s = 0; s < ns; s++) {
        Section *sec = &secs[s];
        printf("%s: %s\n", sec->path, sec->kind);
        char *fp = malloc(strlen(root) + strlen(sec->path) + 16);
        sprintf(fp, "%s/tree/%s", root, sec->path);
        struct stat st;
        int exists = stat(fp, &st) == 0 && S_ISREG(st.st_mode);
        Lines content = {0};
        if (strcmp(sec->kind, "create") == 0) {
            if (exists) { printf("  error: already exists\n"); continue; }
        } else {
            if (!exists) { printf("  error: not found\n"); continue; }
            size_t fl = 0;
            unsigned char *fd = readFile(fp, &fl);
            if (!fd) { fd = malloc(1); fl = 0; }
            content = splitLines(fd, fl);
        }
        long delta = 0;
        int allApplied = 1;
        for (size_t k = 0; k < sec->nh; k++) {
            Hunk *h = &sec->h[k];
            size_t n = k + 1;
            if (h->malformed) { printf("  hunk %zu: malformed\n", n); allApplied = 0; continue; }
            long P = h->a + delta;
            long found = -1;
            if (h->b == 0) {
                if (P >= 0 && P <= (long)content.n) found = P;
                else { printf("  hunk %zu: rejected\n", n); allApplied = 0; continue; }
            } else {
                long maxP = (long)content.n - h->b + 1;
                if (maxP >= 1) {
                    long d1 = labs(P - 1), d2 = labs(P - maxP);
                    long lim = (d1 > d2 ? d1 : d2) + 1;
                    for (long kk = 0; kk <= lim && found < 0; kk++) {
                        long cands[2] = { P - kk, P + kk };
                        int nc = kk == 0 ? 1 : 2;
                        for (int ci = 0; ci < nc; ci++) {
                            long p = cands[ci];
                            if (p < 1 || p > maxP) continue;
                            int ok = 1;
                            for (long j = 0; j < h->b; j++)
                                if (!leq(content.v[p - 1 + j], h->oldL.v[j])) { ok = 0; break; }
                            if (ok) { found = p; break; }
                        }
                    }
                }
                if (found < 0) { printf("  hunk %zu: rejected\n", n); allApplied = 0; continue; }
            }
            /* replace b lines at index start with newL */
            size_t start = h->b == 0 ? (size_t)found : (size_t)(found - 1);
            size_t newN = content.n - (size_t)h->b + h->newL.n;
            Line *nv = malloc((newN ? newN : 1) * sizeof(Line));
            if (start) memcpy(nv, content.v, start * sizeof(Line));
            if (h->newL.n) memcpy(nv + start, h->newL.v, h->newL.n * sizeof(Line));
            size_t tail = content.n - start - (size_t)h->b;
            if (tail) memcpy(nv + start + h->newL.n, content.v + start + h->b, tail * sizeof(Line));
            free(content.v);
            content.v = nv;
            content.n = newN;
            content.cap = newN ? newN : 1;
            if (h->b == 0) printf("  hunk %zu: applied at %ld (offset 0)\n", n, P);
            else {
                long off = found - P;
                if (off > 0) printf("  hunk %zu: applied at %ld (offset +%ld)\n", n, found, off);
                else printf("  hunk %zu: applied at %ld (offset %ld)\n", n, found, off);
            }
            delta += h->d - h->b;
        }
        if (strcmp(sec->kind, "delete") == 0 && allApplied && content.n == 0) {
            printf("  result: deleted\n");
            continue;
        }
        uint32_t c = 0xFFFFFFFFu;
        for (size_t li = 0; li < content.n; li++)
            for (size_t j = 0; j < content.v[li].n; j++)
                c = table[(c ^ content.v[li].p[j]) & 0xFF] ^ (c >> 8);
        c ^= 0xFFFFFFFFu;
        printf("  result: %zu lines, crc32 %08x\n", content.n, c);
    }
    return 0;
}
