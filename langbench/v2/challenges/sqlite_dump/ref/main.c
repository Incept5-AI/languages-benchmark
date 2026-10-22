#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <ctype.h>

static unsigned char *db;
static size_t dbsize;
static uint32_t pageSize, usable;

static unsigned char *page(uint32_t n) {
    size_t off = (size_t)(n - 1) * pageSize;
    if (n == 0 || off + pageSize > dbsize) { fprintf(stderr, "bad page %u\n", n); exit(1); }
    return db + off;
}
static uint32_t be16(const unsigned char *p) { return ((uint32_t)p[0] << 8) | p[1]; }
static uint32_t be32(const unsigned char *p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }

static uint64_t varint(const unsigned char *p, int *len) {
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) {
        v = (v << 7) | (p[i] & 0x7F);
        if (!(p[i] & 0x80)) { *len = i + 1; return v; }
    }
    v = (v << 8) | p[8];
    *len = 9;
    return v;
}

/* ---------- byte buffer ---------- */
typedef struct { unsigned char *d; size_t n, cap; } Buf;
static void bput(Buf *b, const void *s, size_t n) {
    if (b->n + n > b->cap) {
        size_t c = b->cap ? b->cap * 2 : 256;
        while (c < b->n + n) c *= 2;
        b->d = realloc(b->d, c); b->cap = c;
    }
    memcpy(b->d + b->n, s, n); b->n += n;
}
static void bstr(Buf *b, const char *s) { bput(b, s, strlen(s)); }

/* ---------- rows ---------- */
typedef struct { int64_t rowid; unsigned char *payload; size_t len; } Row;
typedef struct { Row *r; size_t n, cap; } Rows;

static void addRow(Rows *rs, int64_t rowid, unsigned char *p, size_t len) {
    if (rs->n == rs->cap) { rs->cap = rs->cap ? rs->cap * 2 : 64; rs->r = realloc(rs->r, rs->cap * sizeof(Row)); }
    rs->r[rs->n].rowid = rowid; rs->r[rs->n].payload = p; rs->r[rs->n].len = len; rs->n++;
}

static void walk(uint32_t pg, Rows *rs, int depth) {
    if (depth > 64) { fprintf(stderr, "too deep\n"); exit(1); }
    unsigned char *p = page(pg);
    unsigned char *h = p + (pg == 1 ? 100 : 0);
    int type = h[0];
    uint32_t ncell = be16(h + 3);
    if (type == 0x05) {
        unsigned char *ptrs = h + 12;
        for (uint32_t i = 0; i < ncell; i++) {
            unsigned char *c = p + be16(ptrs + 2 * i);
            walk(be32(c), rs, depth + 1);
        }
        walk(be32(h + 8), rs, depth + 1);
    } else if (type == 0x0D) {
        unsigned char *ptrs = h + 8;
        for (uint32_t i = 0; i < ncell; i++) {
            unsigned char *c = p + be16(ptrs + 2 * i);
            int l1, l2;
            uint64_t plen = varint(c, &l1);
            int64_t rowid = (int64_t)varint(c + l1, &l2);
            unsigned char *pl = c + l1 + l2;
            uint64_t X = usable - 35;
            unsigned char *out = malloc(plen ? plen : 1);
            if (plen <= X) {
                memcpy(out, pl, plen);
            } else {
                uint64_t M = ((uint64_t)(usable - 12) * 32 / 255) - 23;
                uint64_t K = M + ((plen - M) % (usable - 4));
                uint64_t local = K <= X ? K : M;
                memcpy(out, pl, local);
                uint32_t ov = be32(pl + local);
                uint64_t got = local;
                while (got < plen && ov) {
                    unsigned char *op = page(ov);
                    uint64_t take = plen - got;
                    if (take > usable - 4) take = usable - 4;
                    memcpy(out + got, op + 4, take);
                    got += take;
                    ov = be32(op);
                }
            }
            addRow(rs, rowid, out, plen);
        }
    } else {
        fprintf(stderr, "unexpected page type %d at %u\n", type, pg);
        exit(1);
    }
}

/* ---------- record decoding ---------- */
typedef struct { int kind; /*0 null,1 int,2 real,3 text,4 blob*/ int64_t i; double d; const unsigned char *s; size_t n; } Val;

static size_t decodeRecord(const unsigned char *p, size_t len, Val **vals) {
    int l;
    uint64_t hlen = varint(p, &l);
    size_t hp = l, dp = hlen;
    size_t cnt = 0, cap = 16;
    Val *v = malloc(cap * sizeof(Val));
    while (hp < hlen) {
        uint64_t st = varint(p + hp, &l); hp += l;
        if (cnt == cap) { cap *= 2; v = realloc(v, cap * sizeof(Val)); }
        Val x; memset(&x, 0, sizeof x);
        if (st == 0) x.kind = 0;
        else if (st >= 1 && st <= 6) {
            static const int sz[] = {0, 1, 2, 3, 4, 6, 8};
            int n = sz[st];
            uint64_t u = 0;
            for (int i = 0; i < n; i++) u = (u << 8) | p[dp + i];
            if (n < 8 && (u >> (n * 8 - 1)) & 1) u |= ~(uint64_t)0 << (n * 8);
            x.kind = 1; x.i = (int64_t)u; dp += n;
        } else if (st == 7) {
            uint64_t u = 0;
            for (int i = 0; i < 8; i++) u = (u << 8) | p[dp + i];
            memcpy(&x.d, &u, 8); x.kind = 2; dp += 8;
        } else if (st == 8) { x.kind = 1; x.i = 0; }
        else if (st == 9) { x.kind = 1; x.i = 1; }
        else if (st >= 12) {
            size_t n = (st - 12) / 2;
            x.kind = (st & 1) ? 3 : 4; x.s = p + dp; x.n = n; dp += n;
        }
        v[cnt++] = x;
    }
    (void)len;
    *vals = v;
    return cnt;
}

static uint32_t crcTable[256];
static void crcInit(void) {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crcTable[i] = c;
    }
}
static uint32_t crc32(const unsigned char *s, size_t n) {
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = crcTable[(c ^ s[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

static void fmtVal(Buf *b, const Val *v) {
    char tmp[512];
    switch (v->kind) {
    case 0: bstr(b, "NULL"); break;
    case 1: snprintf(tmp, sizeof tmp, "%lld", (long long)v->i); bstr(b, tmp); break;
    case 2: {
        char *big = malloc(1500);
        snprintf(big, 1500, "%.1100f", v->d);
        size_t n = strlen(big);
        while (n > 0 && big[n - 1] == '0') n--;
        if (n > 0 && big[n - 1] == '.') big[n++] = '0';
        big[n] = 0;
        bstr(b, big);
        free(big);
        break;
    }
    case 3:
        if (v->n > 100) {
            snprintf(tmp, sizeof tmp, "<TEXT len=%zu crc32=%08x>", v->n, crc32(v->s, v->n)); bstr(b, tmp);
        } else {
            bstr(b, "\"");
            for (size_t i = 0; i < v->n; i++) {
                unsigned char c = v->s[i];
                if (c == '\\') bstr(b, "\\\\");
                else if (c == '"') bstr(b, "\\\"");
                else if (c == '\n') bstr(b, "\\n");
                else if (c == '\r') bstr(b, "\\r");
                else if (c == '\t') bstr(b, "\\t");
                else if (c < 0x20 || c == 0x7F) { snprintf(tmp, sizeof tmp, "\\x%02X", c); bstr(b, tmp); }
                else bput(b, &c, 1);
            }
            bstr(b, "\"");
        }
        break;
    case 4:
        if (v->n > 100) {
            snprintf(tmp, sizeof tmp, "<BLOB len=%zu crc32=%08x>", v->n, crc32(v->s, v->n)); bstr(b, tmp);
        } else {
            bstr(b, "X'");
            for (size_t i = 0; i < v->n; i++) { snprintf(tmp, sizeof tmp, "%02X", v->s[i]); bstr(b, tmp); }
            bstr(b, "'");
        }
        break;
    }
}

/* ---------- SQL column parsing ---------- */
typedef struct { char *name; int alias; } Col;

static int ieq(const char *a, size_t n, const char *kw) {
    size_t k = strlen(kw);
    if (n != k) return 0;
    for (size_t i = 0; i < n; i++) if (toupper((unsigned char)a[i]) != kw[i]) return 0;
    return 1;
}

static int isIdChar(int c) { return isalnum(c) || c == '_' || c >= 0x80 || c == '$'; }

static size_t parseCols(const char *sql, size_t sn, Col **out) {
    size_t i = 0;
    while (i < sn && sql[i] != '(') i++;
    i++;
    /* find matching close paren */
    size_t start = i; int depth = 1; size_t end = i;
    for (size_t j = i; j < sn; j++) {
        if (sql[j] == '"') { j++; while (j < sn && sql[j] != '"') j++; continue; }
        if (sql[j] == '(') depth++;
        else if (sql[j] == ')') { depth--; if (depth == 0) { end = j; break; } }
    }
    Col *cols = NULL; size_t nc = 0;
    size_t ds = start; depth = 0;
    for (size_t j = start; j <= end; j++) {
        int c = (j == end) ? ',' : sql[j];
        if (c == '"' && j < end) { j++; while (j < end && sql[j] != '"') j++; continue; }
        if (c == '(') depth++;
        else if (c == ')') depth--;
        else if (c == ',' && depth == 0) {
            /* definition sql[ds..j) */
            const char *d = sql + ds; size_t dn = j - ds;
            /* tokenize */
            const char *tok[256]; size_t tl[256]; int nt = 0;
            size_t k = 0;
            while (k < dn && nt < 256) {
                unsigned char ch = d[k];
                if (isspace(ch)) { k++; continue; }
                if (ch == '"') {
                    size_t s = k + 1; k = s; while (k < dn && d[k] != '"') k++;
                    tok[nt] = d + s - 1; tl[nt] = k - s + 2; nt++; k++;
                } else if (isIdChar(ch)) {
                    size_t s = k; while (k < dn && isIdChar((unsigned char)d[k])) k++;
                    tok[nt] = d + s; tl[nt] = k - s; nt++;
                } else { tok[nt] = d + k; tl[nt] = 1; nt++; k++; }
            }
            if (nt > 0) {
                Col col; col.alias = 0;
                if (tok[0][0] == '"') { col.name = strndup(tok[0] + 1, tl[0] - 2); }
                else col.name = strndup(tok[0], tl[0]);
                /* type: identifier tokens after name until constraint keyword or punctuation */
                static const char *kws[] = {"CONSTRAINT","PRIMARY","NOT","NULL","UNIQUE","CHECK","DEFAULT","COLLATE","REFERENCES","GENERATED","AS",NULL};
                int t = 1, typeWords = 0, typeIsInteger = 0, typeHasParen = 0;
                while (t < nt) {
                    if (!isIdChar((unsigned char)tok[t][0]) && tok[t][0] != '"') break;
                    int isKw = 0;
                    for (int q = 0; kws[q]; q++) if (ieq(tok[t], tl[t], kws[q])) { isKw = 1; break; }
                    if (isKw) break;
                    typeWords++;
                    if (typeWords == 1 && ieq(tok[t], tl[t], "INTEGER")) typeIsInteger = 1;
                    t++;
                }
                if (t < nt && tok[t][0] == '(' && typeWords > 0) typeHasParen = 1;
                int pk = 0;
                for (int q = 0; q + 1 < nt; q++) if (ieq(tok[q], tl[q], "PRIMARY") && ieq(tok[q + 1], tl[q + 1], "KEY")) pk = 1;
                col.alias = (typeWords == 1 && typeIsInteger && !typeHasParen && pk);
                cols = realloc(cols, (nc + 1) * sizeof(Col));
                cols[nc++] = col;
            }
            ds = j + 1;
        }
    }
    *out = cols;
    return nc;
}

/* ---------- main ---------- */
typedef struct { char *name; size_t nlen; uint32_t root; char *sql; size_t sqllen; } Tab;

static int tabCmp(const void *a, const void *b) {
    const Tab *x = a, *y = b;
    size_t n = x->nlen < y->nlen ? x->nlen : y->nlen;
    int c = memcmp(x->name, y->name, n);
    if (c) return c;
    return (x->nlen > y->nlen) - (x->nlen < y->nlen);
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    crcInit();
    char path[4096];
    snprintf(path, sizeof path, "%s/db.sqlite", argv[1]);
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return 1; }
    fseek(f, 0, SEEK_END); dbsize = ftell(f); fseek(f, 0, SEEK_SET);
    db = malloc(dbsize);
    if (fread(db, 1, dbsize, f) != dbsize) return 1;
    fclose(f);
    pageSize = be16(db + 16);
    if (pageSize == 1) pageSize = 65536;
    usable = pageSize - db[20];

    Rows schema = {0};
    walk(1, &schema, 0);
    Tab *tabs = NULL; size_t nt = 0;
    for (size_t i = 0; i < schema.n; i++) {
        Val *v; size_t n = decodeRecord(schema.r[i].payload, schema.r[i].len, &v);
        if (n >= 5 && v[0].kind == 3 && v[0].n == 5 && memcmp(v[0].s, "table", 5) == 0 && v[1].kind == 3) {
            if (v[1].n >= 7 && memcmp(v[1].s, "sqlite_", 7) == 0) { free(v); continue; }
            Tab t;
            t.name = malloc(v[1].n + 1); memcpy(t.name, v[1].s, v[1].n); t.name[v[1].n] = 0; t.nlen = v[1].n;
            t.root = (uint32_t)v[3].i;
            t.sql = malloc(v[4].n + 1); memcpy(t.sql, v[4].s, v[4].n); t.sql[v[4].n] = 0; t.sqllen = v[4].n;
            tabs = realloc(tabs, (nt + 1) * sizeof(Tab)); tabs[nt++] = t;
        }
        free(v);
    }
    qsort(tabs, nt, sizeof(Tab), tabCmp);

    Buf out = {0};
    char tmp[128];
    for (size_t ti = 0; ti < nt; ti++) {
        Tab *t = &tabs[ti];
        Col *cols; size_t nc = parseCols(t->sql, t->sqllen, &cols);
        Rows rs = {0};
        if (t->root) walk(t->root, &rs, 0);
        if (ti) bstr(&out, "\n");
        bstr(&out, "TABLE "); bput(&out, t->name, t->nlen);
        snprintf(tmp, sizeof tmp, " rows=%zu\nCOLUMNS ", rs.n); bstr(&out, tmp);
        for (size_t c = 0; c < nc; c++) { if (c) bstr(&out, ", "); bstr(&out, cols[c].name); }
        bstr(&out, "\n");
        for (size_t r = 0; r < rs.n; r++) {
            Val *v; size_t n = decodeRecord(rs.r[r].payload, rs.r[r].len, &v);
            snprintf(tmp, sizeof tmp, "%lld: ", (long long)rs.r[r].rowid); bstr(&out, tmp);
            for (size_t c = 0; c < nc; c++) {
                if (c) bstr(&out, ", ");
                if (cols[c].alias) { snprintf(tmp, sizeof tmp, "%lld", (long long)rs.r[r].rowid); bstr(&out, tmp); }
                else if (c < n) fmtVal(&out, &v[c]);
                else bstr(&out, "NULL");
            }
            bstr(&out, "\n");
            free(v);
        }
    }
    fwrite(out.d, 1, out.n, stdout);
    return 0;
}
