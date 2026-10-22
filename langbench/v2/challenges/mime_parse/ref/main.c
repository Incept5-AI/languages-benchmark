#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <dirent.h>
#include <ctype.h>

typedef struct { unsigned char *p; size_t n, cap; } Buf;
typedef struct { uint32_t *p; size_t n, cap; } UBuf;

static void bpush(Buf *b, unsigned char c) {
    if (b->n == b->cap) { b->cap = b->cap ? b->cap * 2 : 64; b->p = realloc(b->p, b->cap); }
    b->p[b->n++] = c;
}
static void bappend(Buf *b, const unsigned char *s, size_t n) { for (size_t i = 0; i < n; i++) bpush(b, s[i]); }
static void upush(UBuf *b, uint32_t c) {
    if (b->n == b->cap) { b->cap = b->cap ? b->cap * 2 : 64; b->p = realloc(b->p, b->cap * 4); }
    b->p[b->n++] = c;
}

static int isws(int c) { return c == ' ' || c == '\t'; }

static long find(const unsigned char *h, size_t end, const unsigned char *n, size_t nn, size_t from) {
    for (size_t i = from; i + nn <= end; i++)
        if (memcmp(h + i, n, nn) == 0) return (long)i;
    return -1;
}

static int hexv(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int b64val(int c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

static void b64decode(const unsigned char *d, size_t n, Buf *out) {
    uint32_t acc = 0; int k = 0;
    for (size_t i = 0; i < n; i++) {
        int c = d[i];
        if (c == '=') break;
        int v = b64val(c);
        if (v < 0) continue;
        acc = ((acc << 6) | (uint32_t)v) & 0xffffff;
        if (++k == 4) {
            bpush(out, (acc >> 16) & 255); bpush(out, (acc >> 8) & 255); bpush(out, acc & 255);
            acc = 0; k = 0;
        }
    }
    if (k == 2) bpush(out, (acc >> 4) & 255);
    else if (k == 3) { bpush(out, (acc >> 10) & 255); bpush(out, (acc >> 2) & 255); }
}

static void qpdecode(const unsigned char *d, size_t n, const unsigned char *nl, size_t nll, Buf *out) {
    Buf j = {0};
    size_t pos = 0;
    for (;;) {
        long i = find(d, n, nl, nll, pos);
        int has = i >= 0;
        size_t le = has ? (size_t)i : n;
        size_t e = le;
        while (e > pos && isws(d[e - 1])) e--;
        if (e > pos && d[e - 1] == '=') bappend(&j, d + pos, e - 1 - pos);
        else { bappend(&j, d + pos, e - pos); if (has) bappend(&j, nl, nll); }
        if (!has) break;
        pos = (size_t)i + nll;
    }
    size_t i = 0;
    while (i < j.n) {
        int c = j.p[i];
        if (c == '=' && i + 2 < j.n) {
            int a = hexv(j.p[i + 1]), b = hexv(j.p[i + 2]);
            if (a >= 0 && b >= 0) { bpush(out, a * 16 + b); i += 3; continue; }
        }
        bpush(out, c);
        i++;
    }
    free(j.p);
}

static const uint32_t CP1252[32] = {
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
    0x0152, 0x008D, 0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178};

static void lowerstr(char *s) { for (; *s; s++) *s = (char)tolower((unsigned char)*s); }

static void decode_charset(const unsigned char *b, size_t n, const char *cs0, UBuf *out) {
    char cs[128];
    snprintf(cs, sizeof cs, "%s", cs0);
    lowerstr(cs);
    if (!strcmp(cs, "iso-8859-1") || !strcmp(cs, "latin1") || !strcmp(cs, "iso8859-1")) {
        for (size_t i = 0; i < n; i++) upush(out, b[i]);
        return;
    }
    if (!strcmp(cs, "windows-1252") || !strcmp(cs, "cp1252")) {
        for (size_t i = 0; i < n; i++) upush(out, (b[i] >= 0x80 && b[i] < 0xa0) ? CP1252[b[i] - 0x80] : b[i]);
        return;
    }
    size_t i = 0;
    while (i < n) {
        unsigned c = b[i];
        int len; uint32_t cp;
        if (c < 0x80) { len = 1; cp = c; }
        else if ((c & 0xe0) == 0xc0) { len = 2; cp = c & 0x1f; }
        else if ((c & 0xf0) == 0xe0) { len = 3; cp = c & 0x0f; }
        else if ((c & 0xf8) == 0xf0) { len = 4; cp = c & 0x07; }
        else { upush(out, 0xfffd); i++; continue; }
        if (i + len > n) { upush(out, 0xfffd); i++; continue; }
        int ok = 1;
        for (int k = 1; k < len; k++) {
            if ((b[i + k] & 0xc0) != 0x80) { ok = 0; break; }
            cp = (cp << 6) | (b[i + k] & 0x3f);
        }
        if (!ok) { upush(out, 0xfffd); i++; continue; }
        upush(out, cp);
        i += len;
    }
}

static void strip_lang(char *cs) { char *p = strchr(cs, '*'); if (p) *p = 0; }

typedef struct { int word; size_t ts, te; char cs[128]; Buf bytes; } Seg;

static void decode2047(const char *s, UBuf *out) {
    const unsigned char *b = (const unsigned char *)s;
    size_t n = strlen(s);
    Seg *segs = calloc(n + 2, sizeof(Seg));
    size_t ns = 0, textStart = 0, i = 0;
    while (i + 1 < n) {
        if (b[i] == '=' && b[i + 1] == '?') {
            size_t j = i + 2;
            while (j < n && b[j] != '?' && !isws(b[j])) j++;
            if (j > i + 2 && j < n && b[j] == '?' && j + 2 < n && strchr("BbQq", b[j + 1]) && b[j + 1] && b[j + 2] == '?') {
                int enc = b[j + 1];
                size_t ts = j + 3, k = ts;
                while (k < n && b[k] != '?' && !isws(b[k])) k++;
                if (k + 1 < n && b[k] == '?' && b[k + 1] == '=') {
                    if (textStart < i) { segs[ns].word = 0; segs[ns].ts = textStart; segs[ns].te = i; ns++; }
                    Seg *sg = &segs[ns++];
                    sg->word = 1;
                    size_t cl = j - (i + 2); if (cl > 127) cl = 127;
                    memcpy(sg->cs, s + i + 2, cl); sg->cs[cl] = 0;
                    strip_lang(sg->cs); lowerstr(sg->cs);
                    if (enc == 'B' || enc == 'b') b64decode(b + ts, k - ts, &sg->bytes);
                    else {
                        size_t p = ts;
                        while (p < k) {
                            int c = b[p];
                            if (c == '_') bpush(&sg->bytes, 0x20);
                            else if (c == '=' && p + 2 < k && hexv(b[p + 1]) >= 0 && hexv(b[p + 2]) >= 0) {
                                bpush(&sg->bytes, hexv(b[p + 1]) * 16 + hexv(b[p + 2]));
                                p += 3; continue;
                            } else bpush(&sg->bytes, c);
                            p++;
                        }
                    }
                    i = k + 2; textStart = i;
                    continue;
                }
            }
        }
        i++;
    }
    if (textStart < n) { segs[ns].word = 0; segs[ns].ts = textStart; segs[ns].te = n; ns++; }
    int prevWord = 0;
    size_t nc = 0;
    Seg **cl = calloc(ns + 1, sizeof(Seg *));
    for (size_t idx = 0; idx < ns; idx++) {
        Seg *sg = &segs[idx];
        int drop = 0;
        if (!sg->word && prevWord && idx + 1 < ns && segs[idx + 1].word) {
            drop = 1;
            for (size_t q = sg->ts; q < sg->te; q++) if (!isws(b[q])) { drop = 0; break; }
        }
        if (!drop) { prevWord = sg->word; cl[nc++] = sg; }
    }
    size_t k = 0;
    while (k < nc) {
        Seg *sg = cl[k];
        if (!sg->word) { for (size_t q = sg->ts; q < sg->te; q++) upush(out, b[q]); k++; continue; }
        Buf by = {0};
        while (k < nc && cl[k]->word && !strcmp(cl[k]->cs, sg->cs)) { bappend(&by, cl[k]->bytes.p, cl[k]->bytes.n); k++; }
        decode_charset(by.p, by.n, sg->cs, out);
        free(by.p);
    }
    for (size_t q = 0; q < ns; q++) free(segs[q].bytes.p);
    free(segs); free(cl);
}

typedef struct { char *attr; char *val; } Param;
typedef struct { char main[256]; Param *params; int np; } Ctype;

static void parse_ctype(const char *v, int hasSub, Ctype *ct) {
    size_t i = 0, n = strlen(v);
    ct->params = NULL; ct->np = 0;
#define SKIP while (i < n && isws(v[i])) i++
    SKIP;
    size_t st = i;
    while (i < n && v[i] != '/' && v[i] != ';' && !isws(v[i])) i++;
    char tmp[256];
    size_t l = i - st; if (l > 120) l = 120;
    memcpy(tmp, v + st, l); tmp[l] = 0;
    snprintf(ct->main, sizeof ct->main, "%s", tmp);
    if (hasSub) {
        SKIP;
        if (i < n && v[i] == '/') {
            i++;
            SKIP;
            size_t s2 = i;
            while (i < n && v[i] != ';' && !isws(v[i])) i++;
            l = i - s2; if (l > 120) l = 120;
            memcpy(tmp, v + s2, l); tmp[l] = 0;
            size_t ml = strlen(ct->main);
            ct->main[ml] = '/';
            snprintf(ct->main + ml + 1, sizeof ct->main - ml - 1, "%s", tmp);
        }
    }
    lowerstr(ct->main);
    for (;;) {
        SKIP;
        if (i >= n) break;
        if (v[i] != ';') { i++; continue; }
        i++;
        SKIP;
        size_t a = i;
        while (i < n && v[i] != '=' && v[i] != ';' && !isws(v[i])) i++;
        size_t ae = i;
        SKIP;
        if (i < n && v[i] == '=') {
            i++;
            SKIP;
            Buf val = {0};
            if (i < n && v[i] == '"') {
                i++;
                while (i < n && v[i] != '"') {
                    if (v[i] == '\\' && i + 1 < n) i++;
                    bpush(&val, (unsigned char)v[i]);
                    i++;
                }
                if (i < n) i++;
            } else {
                while (i < n && v[i] != ';' && !isws(v[i])) { bpush(&val, (unsigned char)v[i]); i++; }
            }
            bpush(&val, 0);
            if (ae > a) {
                ct->params = realloc(ct->params, sizeof(Param) * (ct->np + 1));
                char *at = malloc(ae - a + 1);
                memcpy(at, v + a, ae - a); at[ae - a] = 0; lowerstr(at);
                ct->params[ct->np].attr = at;
                ct->params[ct->np].val = (char *)val.p;
                ct->np++;
            } else free(val.p);
        }
    }
#undef SKIP
}

static const char *get_param(const Ctype *c, const char *n) {
    for (int i = 0; i < c->np; i++) if (!strcmp(c->params[i].attr, n)) return c->params[i].val;
    return NULL;
}

static void pct_decode(const char *s, Buf *o) {
    size_t n = strlen(s), i = 0;
    while (i < n) {
        if (s[i] == '%' && i + 2 < n && hexv(s[i + 1]) >= 0 && hexv(s[i + 2]) >= 0) {
            bpush(o, hexv(s[i + 1]) * 16 + hexv(s[i + 2])); i += 3;
        } else { bpush(o, (unsigned char)s[i]); i++; }
    }
}

/* charset'lang'value: copies charset into cs, returns pointer to value */
static const char *split_cs(const char *v, char *cs) {
    const char *p1 = strchr(v, '\'');
    if (p1) {
        const char *p2 = strchr(p1 + 1, '\'');
        if (p2) {
            size_t l = p1 - v; if (l > 127) l = 127;
            memcpy(cs, v, l); cs[l] = 0;
            return p2 + 1;
        }
    }
    cs[0] = 0;
    return v;
}

typedef struct { unsigned long long k; int enc; const char *v; } Part2231;
static int cmp2231(const void *a, const void *b) {
    unsigned long long x = ((const Part2231 *)a)->k, y = ((const Part2231 *)b)->k;
    return x < y ? -1 : x > y;
}

static int rfc2231(const Ctype *c, const char *a, UBuf *out) {
    char key[64], cs[128];
    snprintf(key, sizeof key, "%s*", a);
    const char *single = get_param(c, key);
    if (single) {
        const char *rest = split_cs(single, cs);
        Buf by = {0};
        pct_decode(rest, &by);
        strip_lang(cs);
        decode_charset(by.p, by.n, cs[0] ? cs : "us-ascii", out);
        free(by.p);
        return 1;
    }
    size_t pl = strlen(key);
    Part2231 *segs = calloc(c->np + 1, sizeof(Part2231));
    int ns = 0;
    for (int i = 0; i < c->np; i++) {
        const char *n = c->params[i].attr;
        if (strncmp(n, key, pl)) continue;
        const char *rest = n + pl;
        size_t rl = strlen(rest);
        int enc = 0;
        if (rl > 0 && rest[rl - 1] == '*') { enc = 1; rl--; }
        if (rl == 0) continue;
        int ok = 1;
        unsigned long long k = 0;
        for (size_t q = 0; q < rl; q++) { if (!isdigit((unsigned char)rest[q])) { ok = 0; break; } k = k * 10 + (rest[q] - '0'); }
        if (!ok) continue;
        segs[ns].k = k; segs[ns].enc = enc; segs[ns].v = c->params[i].val; ns++;
    }
    if (ns == 0) { free(segs); return 0; }
    qsort(segs, ns, sizeof(Part2231), cmp2231);
    cs[0] = 0;
    Buf by = {0};
    int firstEnc = 1;
    for (int i = 0; i < ns; i++) {
        if (segs[i].enc) {
            if (firstEnc) { firstEnc = 0; pct_decode(split_cs(segs[i].v, cs), &by); }
            else pct_decode(segs[i].v, &by);
        } else bappend(&by, (const unsigned char *)segs[i].v, strlen(segs[i].v));
    }
    strip_lang(cs);
    decode_charset(by.p, by.n, cs[0] ? cs : "us-ascii", out);
    free(by.p); free(segs);
    return 1;
}

static uint32_t crc_table[256];
static uint32_t crc32(const unsigned char *d, size_t n) {
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = crc_table[(c ^ d[i]) & 0xff] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

static Buf OUT;
static void outs(const char *s) { bappend(&OUT, (const unsigned char *)s, strlen(s)); }
static void outf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#include <stdarg.h>
static void outf(const char *fmt, ...) {
    char tmp[1024];
    va_list ap; va_start(ap, fmt);
    vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    outs(tmp);
}
static void out_cp(uint32_t cp) {
    if (cp < 0x80) bpush(&OUT, cp);
    else if (cp < 0x800) { bpush(&OUT, 0xc0 | (cp >> 6)); bpush(&OUT, 0x80 | (cp & 0x3f)); }
    else if (cp < 0x10000) { bpush(&OUT, 0xe0 | (cp >> 12)); bpush(&OUT, 0x80 | ((cp >> 6) & 0x3f)); bpush(&OUT, 0x80 | (cp & 0x3f)); }
    else { bpush(&OUT, 0xf0 | (cp >> 18)); bpush(&OUT, 0x80 | ((cp >> 12) & 0x3f)); bpush(&OUT, 0x80 | ((cp >> 6) & 0x3f)); bpush(&OUT, 0x80 | (cp & 0x3f)); }
}
static void out_quote(const UBuf *s) {
    outs("\"");
    for (size_t i = 0; i < s->n; i++) {
        uint32_t cp = s->p[i];
        if (cp == '\\') outs("\\\\");
        else if (cp == '"') outs("\\\"");
        else if (cp == '\n') outs("\\n");
        else if (cp == '\t') outs("\\t");
        else if (cp < 0x20 || (cp >= 0x7f && cp <= 0x9f)) outf("\\u%04x", (unsigned)cp);
        else out_cp(cp);
    }
    outs("\"");
}

typedef struct { char *name; char *val; } Header;
typedef struct { Header *h; int nh; size_t bs, be; } Entity;

static char *trim_dup(const unsigned char *s, size_t n) {
    size_t a = 0, b = n;
    while (a < b && isws(s[a])) a++;
    while (b > a && isws(s[b - 1])) b--;
    char *r = malloc(b - a + 1);
    memcpy(r, s + a, b - a); r[b - a] = 0;
    return r;
}

static void parse_entity(const unsigned char *d, size_t s, size_t end, const unsigned char *nl, size_t nll, Entity *e) {
    Buf *lines = NULL; int nlns = 0;
    size_t pos = s;
    e->bs = end; e->be = end; e->h = NULL; e->nh = 0;
    while (pos < end) {
        long i = find(d, end, nl, nll, pos);
        size_t le = i >= 0 ? (size_t)i : end;
        size_t next = i >= 0 ? (size_t)i + nll : end;
        if (le == pos) { e->bs = next < end ? next : end; break; }
        if ((d[pos] == ' ' || d[pos] == '\t') && nlns > 0) bappend(&lines[nlns - 1], d + pos, le - pos);
        else {
            lines = realloc(lines, sizeof(Buf) * (nlns + 1));
            memset(&lines[nlns], 0, sizeof(Buf));
            bappend(&lines[nlns], d + pos, le - pos);
            nlns++;
        }
        pos = next;
    }
    for (int k = 0; k < nlns; k++) {
        Buf *l = &lines[k];
        unsigned char *c = memchr(l->p, ':', l->n);
        if (c) {
            char *name = trim_dup(l->p, c - l->p);
            lowerstr(name);
            int dup = 0;
            for (int q = 0; q < e->nh; q++) if (!strcmp(e->h[q].name, name)) dup = 1;
            if (!dup) {
                e->h = realloc(e->h, sizeof(Header) * (e->nh + 1));
                e->h[e->nh].name = name;
                e->h[e->nh].val = trim_dup(c + 1, l->n - (c + 1 - l->p));
                e->nh++;
            } else free(name);
        }
        free(l->p);
    }
    free(lines);
}

static const char *get_header(const Entity *e, const char *n) {
    for (int i = 0; i < e->nh; i++) if (!strcmp(e->h[i].name, n)) return e->h[i].val;
    return NULL;
}

static int all_ws(const unsigned char *d, size_t s, size_t e) {
    for (size_t i = s; i < e; i++) if (!isws(d[i])) return 0;
    return 1;
}

static void render(const unsigned char *d, size_t s, size_t end, const unsigned char *nl, size_t nll, int depth) {
    Entity e;
    parse_entity(d, s, end, nl, nll, &e);
    char ind[512];
    int il = depth * 2; if (il > 500) il = 500;
    memset(ind, ' ', il); ind[il] = 0;
    Ctype ct;
    const char *ctv = get_header(&e, "content-type");
    if (ctv) parse_ctype(ctv, 1, &ct);
    else { strcpy(ct.main, "text/plain"); ct.params = NULL; ct.np = 0; }
    const char *ctev = get_header(&e, "content-transfer-encoding");
    char cte[128];
    snprintf(cte, sizeof cte, "%s", ctev ? ctev : "7bit");
    lowerstr(cte);
    const char *cdv = get_header(&e, "content-disposition");
    Ctype cd; int hascd = 0;
    if (cdv) { parse_ctype(cdv, 0, &cd); hascd = 1; }
    char ty[256];
    snprintf(ty, sizeof ty, "%s", ct.main);
    char *sl = strchr(ty, '/'); if (sl) *sl = 0;
    if (!strcmp(ty, "multipart")) {
        const char *bnd = get_param(&ct, "boundary");
        if (!bnd) bnd = "";
        size_t dbl = strlen(bnd) + 2;
        unsigned char *db = malloc(dbl + 1);
        db[0] = '-'; db[1] = '-'; memcpy(db + 2, bnd, dbl - 2);
        size_t *ps = NULL, *pe = NULL; int np = 0;
        size_t pos = e.bs; long cur = -1; int closed = 0;
        size_t be = e.be;
        for (;;) {
            long i = find(d, be, nl, nll, pos);
            int has = i >= 0;
            size_t le = has ? (size_t)i : be;
            size_t next = has ? (size_t)i + nll : be;
            int kind = 0;
            if (le - pos >= dbl && memcmp(d + pos, db, dbl) == 0) {
                size_t r = pos + dbl;
                if (all_ws(d, r, le)) kind = 1;
                else if (le - r >= 2 && d[r] == '-' && d[r + 1] == '-' && all_ws(d, r + 2, le)) kind = 2;
            }
            if (kind) {
                if (cur >= 0) {
                    size_t pend = (pos >= (size_t)cur + nll) ? pos - nll : (size_t)cur;
                    ps = realloc(ps, sizeof(size_t) * (np + 1)); pe = realloc(pe, sizeof(size_t) * (np + 1));
                    ps[np] = (size_t)cur; pe[np] = pend; np++;
                }
                if (kind == 2) { closed = 1; break; }
                cur = (long)next;
            }
            if (!has) break;
            pos = next;
        }
        if (!closed && cur >= 0 && (size_t)cur <= be) {
            ps = realloc(ps, sizeof(size_t) * (np + 1)); pe = realloc(pe, sizeof(size_t) * (np + 1));
            ps[np] = (size_t)cur; pe[np] = be; np++;
        }
        outf("%s%s parts=%d\n", ind, ct.main, np);
        for (int k = 0; k < np; k++) render(d, ps[k], pe[k], nl, nll, depth + 1);
        free(ps); free(pe); free(db);
        return;
    }
    Buf raw = {0};
    if (!strcmp(cte, "base64")) b64decode(d + e.bs, e.be - e.bs, &raw);
    else if (!strcmp(cte, "quoted-printable")) qpdecode(d + e.bs, e.be - e.bs, nl, nll, &raw);
    else bappend(&raw, d + e.bs, e.be - e.bs);
    const char *disp = hascd ? cd.main : NULL;
    if (!strcmp(ty, "text") && !(disp && !strcmp(disp, "attachment"))) {
        const char *csv = get_param(&ct, "charset");
        char cs[128];
        snprintf(cs, sizeof cs, "%s", csv ? csv : "us-ascii");
        lowerstr(cs);
        UBuf sc = {0}, txt = {0};
        decode_charset(raw.p, raw.n, cs, &sc);
        for (size_t i = 0; i < sc.n; i++) {
            if (sc.p[i] == 13) { upush(&txt, 10); if (i + 1 < sc.n && sc.p[i + 1] == 10) i++; }
            else upush(&txt, sc.p[i]);
        }
        outf("%s%s charset=%s cte=%s chars=%zu\n", ind, ct.main, cs, cte, txt.n);
        outs(ind); outs("  "); out_quote(&txt); outs("\n");
        free(sc.p); free(txt.p);
    } else {
        UBuf name = {0};
        int has = 0;
        if (hascd) {
            has = rfc2231(&cd, "filename", &name);
            if (!has) { const char *f = get_param(&cd, "filename"); if (f) { decode2047(f, &name); has = 1; } }
        }
        if (!has) has = rfc2231(&ct, "name", &name);
        if (!has) { const char *f = get_param(&ct, "name"); if (f) { decode2047(f, &name); has = 1; } }
        outf("%s%s cte=%s disposition=%s name=", ind, ct.main, cte, disp ? disp : "-");
        if (has) out_quote(&name); else outs("-");
        outf(" size=%zu crc32=%08x\n", raw.n, (unsigned)crc32(raw.p, raw.n));
        free(name.p);
    }
    free(raw.p);
}

static int cmpname(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crc_table[i] = c;
    }
    DIR *dir = opendir(argv[1]);
    if (!dir) return 0;
    char **names = NULL; int nn = 0;
    struct dirent *de;
    while ((de = readdir(dir))) {
        size_t l = strlen(de->d_name);
        if (l >= 4 && !strcmp(de->d_name + l - 4, ".eml")) {
            names = realloc(names, sizeof(char *) * (nn + 1));
            names[nn++] = strdup(de->d_name);
        }
    }
    closedir(dir);
    qsort(names, nn, sizeof(char *), cmpname);
    for (int idx = 0; idx < nn; idx++) {
        if (idx > 0) outs("\n");
        char path[4096];
        snprintf(path, sizeof path, "%s/%s", argv[1], names[idx]);
        FILE *f = fopen(path, "rb");
        Buf d = {0};
        if (f) {
            unsigned char tmp[65536]; size_t r;
            while ((r = fread(tmp, 1, sizeof tmp, f)) > 0) bappend(&d, tmp, r);
            fclose(f);
        }
        bpush(&d, 0); d.n--;
        const unsigned char *nl = (const unsigned char *)"\n"; size_t nll = 1;
        unsigned char *p = memchr(d.p, '\n', d.n);
        if (p && p > d.p && p[-1] == '\r') { nl = (const unsigned char *)"\r\n"; nll = 2; }
        Entity e;
        parse_entity(d.p, 0, d.n, nl, nll, &e);
        outs("== "); outs(names[idx]); outs("\n");
        const char *hn[2] = {"from", "subject"}, *lab[2] = {"From", "Subject"};
        for (int k = 0; k < 2; k++) {
            const char *v = get_header(&e, hn[k]);
            outs(lab[k]); outs(": ");
            if (v) { UBuf u = {0}; decode2047(v, &u); out_quote(&u); free(u.p); }
            else outs("-");
            outs("\n");
        }
        render(d.p, 0, d.n, nl, nll, 0);
        free(d.p);
    }
    fwrite(OUT.p, 1, OUT.n, stdout);
    return 0;
}
