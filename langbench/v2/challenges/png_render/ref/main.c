#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <ctype.h>

/* ---------- minimal JSON parser ---------- */
typedef enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ } JType;
typedef struct JVal JVal;
struct JVal {
    JType t;
    long long num;
    char *str;
    int n;
    JVal **items;
    char **keys;
};
static const char *P;
static void ws(void) { while (*P == ' ' || *P == '\t' || *P == '\n' || *P == '\r') P++; }
static JVal *jnew(JType t) { JVal *v = calloc(1, sizeof(JVal)); v->t = t; return v; }
static char *pstr(void) {
    P++; /* opening quote */
    size_t cap = 16, len = 0; char *s = malloc(cap);
    while (*P && *P != '"') {
        char c = *P++;
        if (c == '\\') {
            char e = *P++;
            switch (e) {
                case 'n': c = '\n'; break; case 't': c = '\t'; break; case 'r': c = '\r'; break;
                case 'b': c = '\b'; break; case 'f': c = '\f'; break;
                case 'u': c = '?'; for (int i = 0; i < 4 && *P; i++) P++; break;
                default: c = e;
            }
        }
        if (len + 2 > cap) { cap *= 2; s = realloc(s, cap); }
        s[len++] = c;
    }
    if (*P == '"') P++;
    s[len] = 0;
    return s;
}
static JVal *pval(void) {
    ws();
    JVal *v;
    if (*P == '{') {
        v = jnew(J_OBJ); P++; ws();
        int cap = 4; v->items = malloc(cap * sizeof(JVal*)); v->keys = malloc(cap * sizeof(char*));
        if (*P == '}') { P++; return v; }
        for (;;) {
            ws(); char *k = pstr(); ws(); if (*P == ':') P++;
            JVal *x = pval();
            if (v->n == cap) { cap *= 2; v->items = realloc(v->items, cap * sizeof(JVal*)); v->keys = realloc(v->keys, cap * sizeof(char*)); }
            v->keys[v->n] = k; v->items[v->n++] = x;
            ws(); if (*P == ',') { P++; continue; } if (*P == '}') P++; break;
        }
        return v;
    }
    if (*P == '[') {
        v = jnew(J_ARR); P++; ws();
        int cap = 4; v->items = malloc(cap * sizeof(JVal*));
        if (*P == ']') { P++; return v; }
        for (;;) {
            JVal *x = pval();
            if (v->n == cap) { cap *= 2; v->items = realloc(v->items, cap * sizeof(JVal*)); }
            v->items[v->n++] = x;
            ws(); if (*P == ',') { P++; continue; } if (*P == ']') P++; break;
        }
        return v;
    }
    if (*P == '"') { v = jnew(J_STR); v->str = pstr(); return v; }
    if (*P == 't') { P += 4; v = jnew(J_BOOL); v->num = 1; return v; }
    if (*P == 'f') { P += 5; v = jnew(J_BOOL); return v; }
    if (*P == 'n') { P += 4; return jnew(J_NULL); }
    v = jnew(J_NUM);
    char *end; double d = strtod(P, &end);
    v->num = (long long)d; P = end;
    return v;
}
static JVal *get(JVal *o, const char *k) {
    if (!o || o->t != J_OBJ) return NULL;
    for (int i = 0; i < o->n; i++) if (strcmp(o->keys[i], k) == 0) return o->items[i];
    return NULL;
}
static long long gnum(JVal *o, const char *k) { JVal *v = get(o, k); return v ? v->num : 0; }

/* ---------- PNG ---------- */
static uint32_t crctab[256];
static void crcinit(void) {
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crctab[n] = c;
    }
}
static uint32_t crcupd(uint32_t c, const unsigned char *b, size_t n) {
    for (size_t i = 0; i < n; i++) c = crctab[(c ^ b[i]) & 0xff] ^ (c >> 8);
    return c;
}
static void be32(unsigned char *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static void chunk(FILE *f, const char *type, const unsigned char *data, size_t len) {
    unsigned char h[8]; be32(h, (uint32_t)len); memcpy(h + 4, type, 4);
    fwrite(h, 1, 8, f);
    if (len) fwrite(data, 1, len, f);
    uint32_t c = crcupd(0xffffffffu, (const unsigned char *)type, 4);
    c = crcupd(c, data, len) ^ 0xffffffffu;
    unsigned char t[4]; be32(t, c); fwrite(t, 1, 4, f);
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    char path[4096]; snprintf(path, sizeof path, "%s/scene.json", argv[1]);
    FILE *f = fopen(path, "rb"); if (!f) return 1;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1); fread(buf, 1, sz, f); buf[sz] = 0; fclose(f);
    P = buf; JVal *root = pval();
    long long W = gnum(root, "width"), H = gnum(root, "height");
    JVal *bg = get(root, "background");
    unsigned char *img = malloc((size_t)W * H * 3);
    for (long long i = 0; i < W * H; i++) for (int c = 0; c < 3; c++) img[i * 3 + c] = (unsigned char)bg->items[c]->num;
    JVal *shapes = get(root, "shapes");
    int ns = shapes ? shapes->n : 0;
    for (int si = 0; si < ns; si++) {
        JVal *s = shapes->items[si];
        const char *type = get(s, "type")->str;
        JVal *col = get(s, "color");
        long long sc[3], A;
        for (int c = 0; c < 3; c++) sc[c] = col->items[c]->num;
        A = col->items[3]->num;
        int kind; long long x0, y0, x1, y1; /* bbox inclusive */
        long long rx = 0, ry = 0, rw = 0, rh = 0, cx = 0, cy = 0, r = 0, px[3] = {0}, py[3] = {0}, lx1 = 0, ly1 = 0, lx2 = 0, ly2 = 0;
        if (strcmp(type, "rect") == 0) {
            kind = 0; rx = gnum(s, "x"); ry = gnum(s, "y"); rw = gnum(s, "w"); rh = gnum(s, "h");
            x0 = rx; y0 = ry; x1 = rx + rw - 1; y1 = ry + rh - 1;
        } else if (strcmp(type, "circle") == 0) {
            kind = 1; cx = gnum(s, "cx"); cy = gnum(s, "cy"); r = gnum(s, "r");
            x0 = cx - r; x1 = cx + r; y0 = cy - r; y1 = cy + r;
        } else if (strcmp(type, "triangle") == 0) {
            kind = 2; JVal *pts = get(s, "points");
            for (int i = 0; i < 3; i++) { px[i] = pts->items[i]->items[0]->num; py[i] = pts->items[i]->items[1]->num; }
            x0 = x1 = px[0]; y0 = y1 = py[0];
            for (int i = 1; i < 3; i++) {
                if (px[i] < x0) x0 = px[i]; if (px[i] > x1) x1 = px[i];
                if (py[i] < y0) y0 = py[i]; if (py[i] > y1) y1 = py[i];
            }
        } else {
            kind = 3; lx1 = gnum(s, "x1"); ly1 = gnum(s, "y1"); lx2 = gnum(s, "x2"); ly2 = gnum(s, "y2"); r = gnum(s, "r");
            x0 = (lx1 < lx2 ? lx1 : lx2) - r; x1 = (lx1 > lx2 ? lx1 : lx2) + r;
            y0 = (ly1 < ly2 ? ly1 : ly2) - r; y1 = (ly1 > ly2 ? ly1 : ly2) + r;
        }
        if (x0 < 0) x0 = 0; if (y0 < 0) y0 = 0; if (x1 > W - 1) x1 = W - 1; if (y1 > H - 1) y1 = H - 1;
        long long count = 0;
        long long ldx = lx2 - lx1, ldy = ly2 - ly1, L = ldx * ldx + ldy * ldy, r2 = r * r;
        for (long long y = y0; y <= y1; y++) {
            for (long long x = x0; x <= x1; x++) {
                int cov = 0;
                switch (kind) {
                    case 0: cov = 1; break;
                    case 1: cov = (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2; break;
                    case 2: {
                        int pos = 1, neg = 1;
                        for (int i = 0; i < 3; i++) {
                            int j = (i + 1) % 3;
                            long long E = (px[j] - px[i]) * (y - py[i]) - (py[j] - py[i]) * (x - px[i]);
                            if (E < 0) pos = 0; if (E > 0) neg = 0;
                        }
                        cov = pos || neg; break;
                    }
                    case 3: {
                        long long t = (x - lx1) * ldx + (y - ly1) * ldy;
                        if (t <= 0) cov = (x - lx1) * (x - lx1) + (y - ly1) * (y - ly1) <= r2;
                        else if (t >= L) cov = (x - lx2) * (x - lx2) + (y - ly2) * (y - ly2) <= r2;
                        else { long long c = (x - lx1) * ldy - (y - ly1) * ldx; cov = c * c <= r2 * L; }
                        break;
                    }
                }
                if (cov) {
                    count++;
                    unsigned char *p = img + (y * W + x) * 3;
                    for (int c = 0; c < 3; c++) p[c] = (unsigned char)((sc[c] * A + p[c] * (255 - A) + 127) / 255);
                }
            }
        }
        printf("%d %s %lld\n", si + 1, type, count);
    }
    /* distinct colours */
    unsigned char *seen = calloc(1 << 21, 1);
    long long k = 0;
    for (long long i = 0; i < W * H; i++) {
        uint32_t v = ((uint32_t)img[i * 3] << 16) | (img[i * 3 + 1] << 8) | img[i * 3 + 2];
        if (!(seen[v >> 3] & (1 << (v & 7)))) { seen[v >> 3] |= 1 << (v & 7); k++; }
    }
    printf("colors %lld\n", k);

    /* PNG write */
    crcinit();
    size_t rawlen = (size_t)H * (W * 3 + 1);
    unsigned char *raw = malloc(rawlen);
    for (long long y = 0; y < H; y++) { raw[y * (W * 3 + 1)] = 0; memcpy(raw + y * (W * 3 + 1) + 1, img + y * W * 3, W * 3); }
    size_t nblocks = (rawlen + 65534) / 65535; if (nblocks == 0) nblocks = 1;
    size_t zlen = 2 + nblocks * 5 + rawlen + 4;
    unsigned char *z = malloc(zlen), *zp = z;
    *zp++ = 0x78; *zp++ = 0x01;
    size_t off = 0;
    for (size_t b = 0; b < nblocks; b++) {
        size_t n = rawlen - off; if (n > 65535) n = 65535;
        *zp++ = (b == nblocks - 1) ? 1 : 0;
        *zp++ = n & 0xff; *zp++ = n >> 8; *zp++ = (~n) & 0xff; *zp++ = ((~n) >> 8) & 0xff;
        memcpy(zp, raw + off, n); zp += n; off += n;
    }
    uint32_t a = 1, bsum = 0;
    for (size_t i = 0; i < rawlen; i++) { a = (a + raw[i]) % 65521; bsum = (bsum + a) % 65521; }
    be32(zp, (bsum << 16) | a); zp += 4;
    FILE *o = fopen("out.png", "wb");
    static const unsigned char sig[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    fwrite(sig, 1, 8, o);
    unsigned char ihdr[13]; be32(ihdr, (uint32_t)W); be32(ihdr + 4, (uint32_t)H);
    ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    chunk(o, "IHDR", ihdr, 13);
    chunk(o, "IDAT", z, zp - z);
    chunk(o, "IEND", NULL, 0);
    fclose(o);
    return 0;
}
