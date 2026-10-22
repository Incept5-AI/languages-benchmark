#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <CommonCrypto/CommonHMAC.h>

/* ---------- JSON ---------- */
enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };
typedef struct JV {
    int type;
    double num;
    int b;
    char *str; size_t slen;
    struct JV **items; char **keys; size_t *klens; size_t n;
} JV;

typedef struct { const char *s; size_t len, pos; } P;

static void skipws(P *p) {
    while (p->pos < p->len) {
        char c = p->s[p->pos];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p->pos++; else break;
    }
}
static JV *parse_value(P *p, int depth);

static void put_utf8(char **buf, size_t *len, size_t *cap, unsigned cp) {
    if (*len + 4 >= *cap) { *cap = *cap * 2 + 8; *buf = realloc(*buf, *cap); }
    char *b = *buf;
    if (cp < 0x80) b[(*len)++] = (char)cp;
    else if (cp < 0x800) { b[(*len)++] = (char)(0xC0 | (cp >> 6)); b[(*len)++] = (char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { b[(*len)++] = (char)(0xE0 | (cp >> 12)); b[(*len)++] = (char)(0x80 | ((cp >> 6) & 0x3F)); b[(*len)++] = (char)(0x80 | (cp & 0x3F)); }
    else { b[(*len)++] = (char)(0xF0 | (cp >> 18)); b[(*len)++] = (char)(0x80 | ((cp >> 12) & 0x3F)); b[(*len)++] = (char)(0x80 | ((cp >> 6) & 0x3F)); b[(*len)++] = (char)(0x80 | (cp & 0x3F)); }
}

static int hex4(P *p, unsigned *out) {
    if (p->pos + 4 > p->len) return 0;
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p->s[p->pos++];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return 0;
    }
    *out = v; return 1;
}

static int parse_string(P *p, char **out, size_t *olen) {
    if (p->pos >= p->len || p->s[p->pos] != '"') return 0;
    p->pos++;
    size_t cap = 16, len = 0; char *buf = malloc(cap);
    while (1) {
        if (p->pos >= p->len) { free(buf); return 0; }
        unsigned char c = (unsigned char)p->s[p->pos++];
        if (c == '"') break;
        if (c < 0x20) { free(buf); return 0; }
        if (c == '\\') {
            if (p->pos >= p->len) { free(buf); return 0; }
            char e = p->s[p->pos++];
            unsigned cp;
            switch (e) {
            case '"': cp = '"'; break; case '\\': cp = '\\'; break; case '/': cp = '/'; break;
            case 'b': cp = 8; break; case 'f': cp = 12; break; case 'n': cp = 10; break;
            case 'r': cp = 13; break; case 't': cp = 9; break;
            case 'u':
                if (!hex4(p, &cp)) { free(buf); return 0; }
                if (cp >= 0xD800 && cp < 0xDC00 && p->pos + 6 <= p->len && p->s[p->pos] == '\\' && p->s[p->pos + 1] == 'u') {
                    size_t save = p->pos; unsigned lo;
                    p->pos += 2;
                    if (hex4(p, &lo) && lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    else p->pos = save;
                }
                break;
            default: free(buf); return 0;
            }
            put_utf8(&buf, &len, &cap, cp);
        } else {
            if (len + 1 >= cap) { cap *= 2; buf = realloc(buf, cap); }
            buf[len++] = (char)c;
        }
    }
    buf[len] = 0;
    *out = buf; *olen = len; return 1;
}

static int parse_number(P *p, double *out) {
    size_t st = p->pos;
    const char *s = p->s; size_t n = p->len;
    if (p->pos < n && s[p->pos] == '-') p->pos++;
    if (p->pos >= n) return 0;
    if (s[p->pos] == '0') p->pos++;
    else if (s[p->pos] >= '1' && s[p->pos] <= '9') { while (p->pos < n && s[p->pos] >= '0' && s[p->pos] <= '9') p->pos++; }
    else return 0;
    if (p->pos < n && s[p->pos] == '.') {
        p->pos++;
        if (p->pos >= n || s[p->pos] < '0' || s[p->pos] > '9') return 0;
        while (p->pos < n && s[p->pos] >= '0' && s[p->pos] <= '9') p->pos++;
    }
    if (p->pos < n && (s[p->pos] == 'e' || s[p->pos] == 'E')) {
        p->pos++;
        if (p->pos < n && (s[p->pos] == '+' || s[p->pos] == '-')) p->pos++;
        if (p->pos >= n || s[p->pos] < '0' || s[p->pos] > '9') return 0;
        while (p->pos < n && s[p->pos] >= '0' && s[p->pos] <= '9') p->pos++;
    }
    char tmp[512]; size_t l = p->pos - st;
    if (l >= sizeof tmp) { char *big = malloc(l + 1); memcpy(big, s + st, l); big[l] = 0; *out = strtod(big, NULL); free(big); }
    else { memcpy(tmp, s + st, l); tmp[l] = 0; *out = strtod(tmp, NULL); }
    return 1;
}

static int lit(P *p, const char *w) {
    size_t l = strlen(w);
    if (p->pos + l <= p->len && memcmp(p->s + p->pos, w, l) == 0) { p->pos += l; return 1; }
    return 0;
}

static JV *parse_value(P *p, int depth) {
    if (depth > 1000) return NULL;
    skipws(p);
    if (p->pos >= p->len) return NULL;
    JV *v = calloc(1, sizeof(JV));
    char c = p->s[p->pos];
    if (c == '{') {
        p->pos++; v->type = J_OBJ;
        size_t cap = 0;
        skipws(p);
        if (p->pos < p->len && p->s[p->pos] == '}') { p->pos++; return v; }
        while (1) {
            skipws(p);
            char *k; size_t kl;
            if (!parse_string(p, &k, &kl)) return NULL;
            skipws(p);
            if (p->pos >= p->len || p->s[p->pos] != ':') return NULL;
            p->pos++;
            JV *x = parse_value(p, depth + 1);
            if (!x) return NULL;
            if (v->n == cap) { cap = cap * 2 + 4; v->items = realloc(v->items, cap * sizeof(JV *)); v->keys = realloc(v->keys, cap * sizeof(char *)); v->klens = realloc(v->klens, cap * sizeof(size_t)); }
            v->keys[v->n] = k; v->klens[v->n] = kl; v->items[v->n++] = x;
            skipws(p);
            if (p->pos >= p->len) return NULL;
            if (p->s[p->pos] == ',') { p->pos++; continue; }
            if (p->s[p->pos] == '}') { p->pos++; return v; }
            return NULL;
        }
    } else if (c == '[') {
        p->pos++; v->type = J_ARR;
        size_t cap = 0;
        skipws(p);
        if (p->pos < p->len && p->s[p->pos] == ']') { p->pos++; return v; }
        while (1) {
            JV *x = parse_value(p, depth + 1);
            if (!x) return NULL;
            if (v->n == cap) { cap = cap * 2 + 4; v->items = realloc(v->items, cap * sizeof(JV *)); }
            v->items[v->n++] = x;
            skipws(p);
            if (p->pos >= p->len) return NULL;
            if (p->s[p->pos] == ',') { p->pos++; continue; }
            if (p->s[p->pos] == ']') { p->pos++; return v; }
            return NULL;
        }
    } else if (c == '"') {
        v->type = J_STR;
        if (!parse_string(p, &v->str, &v->slen)) return NULL;
        return v;
    } else if (lit(p, "true")) { v->type = J_BOOL; v->b = 1; return v; }
    else if (lit(p, "false")) { v->type = J_BOOL; return v; }
    else if (lit(p, "null")) { v->type = J_NULL; return v; }
    else { v->type = J_NUM; if (!parse_number(p, &v->num)) return NULL; return v; }
}

static JV *parse_json(const char *s, size_t len) {
    P p = { s, len, 0 };
    JV *v = parse_value(&p, 0);
    if (!v) return NULL;
    skipws(&p);
    if (p.pos != p.len) return NULL;
    return v;
}

static JV *get(JV *o, const char *k) {
    if (!o || o->type != J_OBJ) return NULL;
    size_t kl = strlen(k);
    for (size_t i = 0; i < o->n; i++)
        if (o->klens[i] == kl && memcmp(o->keys[i], k, kl) == 0) return o->items[i];
    return NULL;
}
static int is_str(JV *v, const char *s, size_t l) { return v && v->type == J_STR && v->slen == l && memcmp(v->str, s, l) == 0; }

/* ---------- base64url ---------- */
static int b64val(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '-') return 62;
    if (c == '_') return 63;
    return -1;
}
/* returns malloc'd buffer or NULL on invalid */
static unsigned char *b64dec(const char *s, size_t n, size_t *olen) {
    if (n % 4 == 1) return NULL;
    unsigned char *out = malloc(n * 3 / 4 + 4);
    size_t o = 0; unsigned acc = 0; int bits = 0;
    for (size_t i = 0; i < n; i++) {
        int v = b64val(s[i]);
        if (v < 0) { free(out); return NULL; }
        acc = (acc << 6) | (unsigned)v; bits += 6;
        if (bits >= 8) { bits -= 8; out[o++] = (unsigned char)((acc >> bits) & 0xFF); }
    }
    *olen = o; return out;
}

/* ---------- scopes ---------- */
typedef struct { char *s; size_t l; } SS;
static int sscmp(const void *a, const void *b) {
    const SS *x = a, *y = b;
    size_t m = x->l < y->l ? x->l : y->l;
    int c = memcmp(x->s, y->s, m);
    if (c) return c;
    return (x->l > y->l) - (x->l < y->l);
}

static char *read_file(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    size_t cap = 4096, n = 0; char *b = malloc(cap);
    size_t r;
    while ((r = fread(b + n, 1, cap - n, f)) > 0) { n += r; if (n == cap) { cap *= 2; b = realloc(b, cap); } }
    fclose(f);
    b[n] = 0; *len = n; return b;
}

static const char *check(const char *tok, size_t tl, JV *cfg, JV *keys, char **sub_out, size_t *sublen, SS **scopes, size_t *nsc) {
    /* split */
    size_t d1 = (size_t)-1, d2 = (size_t)-1; int dots = 0;
    for (size_t i = 0; i < tl; i++) if (tok[i] == '.') { dots++; if (dots == 1) d1 = i; else if (dots == 2) d2 = i; }
    if (dots != 2) return "MALFORMED";
    const char *seg[3] = { tok, tok + d1 + 1, tok + d2 + 1 };
    size_t sl[3] = { d1, d2 - d1 - 1, tl - d2 - 1 };
    unsigned char *dec[3]; size_t dl[3];
    for (int i = 0; i < 3; i++) {
        if (sl[i] == 0) return "MALFORMED";
        dec[i] = b64dec(seg[i], sl[i], &dl[i]);
        if (!dec[i]) return "MALFORMED";
    }
    JV *hdr = parse_json((char *)dec[0], dl[0]);
    if (!hdr || hdr->type != J_OBJ) return "MALFORMED";
    JV *pl = parse_json((char *)dec[1], dl[1]);
    if (!pl || pl->type != J_OBJ) return "MALFORMED";
    JV *alg = get(hdr, "alg");
    if (!alg || alg->type != J_STR) return "UNSUPPORTED_ALG";
    CCHmacAlgorithm ha; size_t hl;
    if (is_str(alg, "HS256", 5)) { ha = kCCHmacAlgSHA256; hl = 32; }
    else if (is_str(alg, "HS384", 5)) { ha = kCCHmacAlgSHA384; hl = 48; }
    else if (is_str(alg, "HS512", 5)) { ha = kCCHmacAlgSHA512; hl = 64; }
    else return "UNSUPPORTED_ALG";
    JV *kid = get(hdr, "kid");
    if (!kid || kid->type != J_STR) return "UNKNOWN_KEY";
    JV *key = NULL;
    if (keys && keys->type == J_ARR)
        for (size_t i = 0; i < keys->n; i++) {
            JV *kk = get(keys->items[i], "kid");
            if (is_str(kk, kid->str, kid->slen)) { key = keys->items[i]; break; }
        }
    if (!key) return "UNKNOWN_KEY";
    JV *kalg = get(key, "alg");
    if (!is_str(kalg, alg->str, alg->slen)) return "ALG_MISMATCH";
    JV *kk = get(key, "k");
    size_t secl = 0; unsigned char *sec = NULL;
    if (kk && kk->type == J_STR) sec = b64dec(kk->str, kk->slen, &secl);
    if (!sec) { sec = malloc(1); secl = 0; }
    unsigned char mac[64];
    CCHmac(ha, sec, secl, tok, d2, mac);
    if (dl[2] != hl || memcmp(mac, dec[2], hl) != 0) return "BAD_SIGNATURE";
    JV *nowv = get(cfg, "now"), *lw = get(cfg, "leeway");
    double now = nowv && nowv->type == J_NUM ? nowv->num : 0;
    double leeway = lw && lw->type == J_NUM ? lw->num : 0;
    JV *exp = get(pl, "exp");
    if (exp && exp->type == J_NUM && now >= exp->num + leeway) return "EXPIRED";
    JV *nbf = get(pl, "nbf");
    if (nbf && nbf->type == J_NUM && now + leeway < nbf->num) return "NOT_YET_VALID";
    JV *ciss = get(cfg, "issuer");
    JV *iss = get(pl, "iss");
    if (!iss || iss->type != J_STR || !ciss || ciss->type != J_STR || !is_str(iss, ciss->str, ciss->slen)) return "BAD_ISSUER";
    JV *caud = get(cfg, "audience");
    JV *aud = get(pl, "aud");
    int ok = 0;
    if (aud && caud && caud->type == J_STR) {
        if (aud->type == J_STR) ok = is_str(aud, caud->str, caud->slen);
        else if (aud->type == J_ARR)
            for (size_t i = 0; i < aud->n; i++) if (is_str(aud->items[i], caud->str, caud->slen)) { ok = 1; break; }
    }
    if (!ok) return "BAD_AUDIENCE";
    JV *sub = get(pl, "sub");
    if (sub && sub->type == J_STR) { *sub_out = sub->str; *sublen = sub->slen; }
    else { *sub_out = ""; *sublen = 0; }
    JV *sc = get(pl, "scope");
    *nsc = 0; *scopes = NULL;
    if (sc && sc->type == J_STR) {
        SS *arr = malloc((sc->slen + 1) * sizeof(SS)); size_t n = 0;
        size_t i = 0;
        while (i < sc->slen) {
            while (i < sc->slen && sc->str[i] == ' ') i++;
            size_t st = i;
            while (i < sc->slen && sc->str[i] != ' ') i++;
            if (i > st) { arr[n].s = sc->str + st; arr[n].l = i - st; n++; }
        }
        qsort(arr, n, sizeof(SS), sscmp);
        size_t m = 0;
        for (size_t j = 0; j < n; j++) if (m == 0 || sscmp(&arr[m - 1], &arr[j]) != 0) arr[m++] = arr[j];
        *scopes = arr; *nsc = m;
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    char path[4096];
    size_t cl, tl;
    snprintf(path, sizeof path, "%s/config.json", argv[1]);
    char *cfgs = read_file(path, &cl);
    snprintf(path, sizeof path, "%s/tokens.txt", argv[1]);
    char *toks = read_file(path, &tl);
    JV *cfg = cfgs ? parse_json(cfgs, cl) : NULL;
    JV *keys = get(cfg, "keys");
    int valid = 0, invalid = 0, num = 0;
    size_t i = 0;
    while (toks && i < tl) {
        size_t st = i;
        while (i < tl && toks[i] != '\n') i++;
        size_t en = i;
        if (i < tl) i++;
        if (en > st && toks[en - 1] == '\r') en--;
        while (st < en && (toks[st] == ' ' || toks[st] == '\t')) st++;
        while (en > st && (toks[en - 1] == ' ' || toks[en - 1] == '\t')) en--;
        if (en == st) continue;
        num++;
        char *sub; size_t subl; SS *sc; size_t nsc;
        const char *r = check(toks + st, en - st, cfg, keys, &sub, &subl, &sc, &nsc);
        if (r) { printf("%d: %s\n", num, r); invalid++; }
        else {
            printf("%d: VALID sub=", num);
            fwrite(sub, 1, subl, stdout);
            printf(" scope=");
            for (size_t j = 0; j < nsc; j++) { if (j) putchar(','); fwrite(sc[j].s, 1, sc[j].l, stdout); }
            putchar('\n');
            valid++;
        }
    }
    printf("\nvalid=%d invalid=%d\n", valid, invalid);
    return 0;
}
