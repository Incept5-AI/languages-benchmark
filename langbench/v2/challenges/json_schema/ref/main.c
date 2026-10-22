#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

enum { T_NULL, T_BOOL, T_NUM, T_STR, T_ARR, T_OBJ };

typedef struct Val {
    int t;
    int b;
    double n;
    char *s; size_t slen;          /* string bytes (UTF-8, may not contain NUL issues: we keep length) */
    struct Val **items; size_t cnt; /* array elements or object values */
    char **keys; size_t *klens;     /* object keys */
} Val;

static const char *src; static size_t pos, srclen;

static void ws(void) { while (pos < srclen && (src[pos] == ' ' || src[pos] == '\t' || src[pos] == '\n' || src[pos] == '\r')) pos++; }

static void push_byte(char **b, size_t *len, size_t *cap, char c) {
    if (*len + 1 >= *cap) { *cap = *cap ? *cap * 2 : 16; *b = realloc(*b, *cap); }
    (*b)[(*len)++] = c;
}

static unsigned hex4(void) {
    unsigned v = 0;
    for (int k = 0; k < 4; k++) {
        char c = src[pos++]; v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else v |= c - 'A' + 10;
    }
    return v;
}

static char *pstring(size_t *outlen) {
    pos++;
    char *b = NULL; size_t len = 0, cap = 0;
    for (;;) {
        char c = src[pos++];
        if (c == '"') break;
        if (c == '\\') {
            char e = src[pos++];
            switch (e) {
            case 'n': push_byte(&b, &len, &cap, '\n'); break;
            case 't': push_byte(&b, &len, &cap, '\t'); break;
            case 'r': push_byte(&b, &len, &cap, '\r'); break;
            case 'b': push_byte(&b, &len, &cap, '\b'); break;
            case 'f': push_byte(&b, &len, &cap, '\f'); break;
            case 'u': {
                unsigned cp = hex4();
                if (cp >= 0xD800 && cp < 0xDC00 && src[pos] == '\\' && src[pos + 1] == 'u') {
                    pos += 2; unsigned lo = hex4();
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                }
                if (cp < 0x80) push_byte(&b, &len, &cap, (char)cp);
                else if (cp < 0x800) { push_byte(&b, &len, &cap, (char)(0xC0 | (cp >> 6))); push_byte(&b, &len, &cap, (char)(0x80 | (cp & 0x3F))); }
                else if (cp < 0x10000) { push_byte(&b, &len, &cap, (char)(0xE0 | (cp >> 12))); push_byte(&b, &len, &cap, (char)(0x80 | ((cp >> 6) & 0x3F))); push_byte(&b, &len, &cap, (char)(0x80 | (cp & 0x3F))); }
                else { push_byte(&b, &len, &cap, (char)(0xF0 | (cp >> 18))); push_byte(&b, &len, &cap, (char)(0x80 | ((cp >> 12) & 0x3F))); push_byte(&b, &len, &cap, (char)(0x80 | ((cp >> 6) & 0x3F))); push_byte(&b, &len, &cap, (char)(0x80 | (cp & 0x3F))); }
                break;
            }
            default: push_byte(&b, &len, &cap, e);
            }
        } else push_byte(&b, &len, &cap, c);
    }
    push_byte(&b, &len, &cap, 0); len--;
    *outlen = len;
    return b;
}

static Val *pvalue(void) {
    ws();
    Val *v = calloc(1, sizeof(Val));
    char c = src[pos];
    if (c == '{') {
        pos++; v->t = T_OBJ; size_t cap = 0;
        ws();
        if (src[pos] == '}') { pos++; return v; }
        for (;;) {
            ws();
            size_t kl; char *k = pstring(&kl);
            ws(); pos++;
            Val *x = pvalue();
            if (v->cnt == cap) { cap = cap ? cap * 2 : 4; v->items = realloc(v->items, cap * sizeof(Val *)); v->keys = realloc(v->keys, cap * sizeof(char *)); v->klens = realloc(v->klens, cap * sizeof(size_t)); }
            v->keys[v->cnt] = k; v->klens[v->cnt] = kl; v->items[v->cnt] = x; v->cnt++;
            ws();
            char d = src[pos++];
            if (d == '}') break;
        }
    } else if (c == '[') {
        pos++; v->t = T_ARR; size_t cap = 0;
        ws();
        if (src[pos] == ']') { pos++; return v; }
        for (;;) {
            Val *x = pvalue();
            if (v->cnt == cap) { cap = cap ? cap * 2 : 4; v->items = realloc(v->items, cap * sizeof(Val *)); }
            v->items[v->cnt++] = x;
            ws();
            char d = src[pos++];
            if (d == ']') break;
        }
    } else if (c == '"') {
        v->t = T_STR; v->s = pstring(&v->slen);
    } else if (c == 't') { v->t = T_BOOL; v->b = 1; pos += 4; }
    else if (c == 'f') { v->t = T_BOOL; v->b = 0; pos += 5; }
    else if (c == 'n') { v->t = T_NULL; pos += 4; }
    else {
        size_t st = pos;
        while (pos < srclen && strchr("+-0123456789.eE", src[pos]) && src[pos]) pos++;
        char buf[64]; size_t l = pos - st; if (l > 63) l = 63;
        memcpy(buf, src + st, l); buf[l] = 0;
        v->t = T_NUM; v->n = strtod(buf, NULL);
    }
    return v;
}

static Val *parse_file(const char *path) {
    FILE *f = fopen(path, "rb");
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1); fread(buf, 1, sz, f); buf[sz] = 0; fclose(f);
    src = buf; pos = 0; srclen = sz;
    return pvalue();
}

static Val *getk(const Val *o, const char *k, size_t kl) {
    if (!o || o->t != T_OBJ) return NULL;
    for (size_t i = 0; i < o->cnt; i++)
        if (o->klens[i] == kl && memcmp(o->keys[i], k, kl) == 0) return o->items[i];
    return NULL;
}
static Val *get(const Val *o, const char *k) { return getk(o, k, strlen(k)); }

static int deep_eq(const Val *a, const Val *b) {
    if (a->t != b->t) return 0;
    switch (a->t) {
    case T_NULL: return 1;
    case T_BOOL: return a->b == b->b;
    case T_NUM: return a->n == b->n;
    case T_STR: return a->slen == b->slen && memcmp(a->s, b->s, a->slen) == 0;
    case T_ARR:
        if (a->cnt != b->cnt) return 0;
        for (size_t i = 0; i < a->cnt; i++) if (!deep_eq(a->items[i], b->items[i])) return 0;
        return 1;
    default:
        if (a->cnt != b->cnt) return 0;
        for (size_t i = 0; i < a->cnt; i++) {
            Val *w = getk(b, a->keys[i], a->klens[i]);
            if (!w || !deep_eq(a->items[i], w)) return 0;
        }
        return 1;
    }
}

/* pointer strings: NUL-terminated; JSON strings may in principle contain U+0000 but we treat as C strings for paths */
static char *esc(const char *t, size_t tl) {
    char *o = malloc(tl * 2 + 1); size_t n = 0;
    for (size_t i = 0; i < tl; i++) {
        if (t[i] == '~') { o[n++] = '~'; o[n++] = '0'; }
        else if (t[i] == '/') { o[n++] = '~'; o[n++] = '1'; }
        else o[n++] = t[i];
    }
    o[n] = 0; return o;
}

static char *join(const char *a, const char *b) {
    size_t la = strlen(a), lb = strlen(b);
    char *o = malloc(la + lb + 2);
    memcpy(o, a, la); o[la] = '/'; memcpy(o + la + 1, b, lb); o[la + lb + 1] = 0;
    return o;
}
static char *join2(const char *a, const char *b, const char *c) {
    char *t = join(a, b); char *r = join(t, c); free(t); return r;
}
static char *joinesc(const char *a, const char *k, size_t kl) {
    char *e = esc(k, kl); char *r = join(a, e); free(e); return r;
}
static char *joinidx(const char *a, size_t i) {
    char buf[32]; snprintf(buf, sizeof buf, "%zu", i); return join(a, buf);
}

typedef struct { char *l, *p; } Err;
typedef struct { Err *e; size_t n, cap; } Errs;

static void add(Errs *o, const char *l, char *p_owned) {
    if (o->n == o->cap) { o->cap = o->cap ? o->cap * 2 : 16; o->e = realloc(o->e, o->cap * sizeof(Err)); }
    o->e[o->n].l = strdup(l); o->e[o->n].p = p_owned; o->n++;
}
static void freeerrs(Errs *o) {
    for (size_t i = 0; i < o->n; i++) { free(o->e[i].l); free(o->e[i].p); }
    free(o->e);
}

static Val *root;

static void validate(const Val *inst, const char *L, const Val *s, const char *P, Errs *out);

static int accepts(const Val *inst, const char *L, const Val *s, const char *P) {
    Errs e = {0};
    validate(inst, L, s, P, &e);
    int ok = e.n == 0;
    freeerrs(&e);
    return ok;
}

static const char *tname(const Val *v) {
    static const char *n[] = {"null", "boolean", "number", "string", "array", "object"};
    return n[v->t];
}

static size_t cplen(const Val *v) {
    size_t n = 0;
    for (size_t i = 0; i < v->slen; i++) if ((v->s[i] & 0xC0) != 0x80) n++;
    return n;
}

static int is_str(const Val *v, const char *s) { return v && v->t == T_STR && v->slen == strlen(s) && memcmp(v->s, s, v->slen) == 0; }

static void validate(const Val *inst, const char *L, const Val *s, const char *P, Errs *out) {
    if (s->t == T_BOOL) {
        if (!s->b) add(out, L, strdup(P));
        return;
    }
    if (s->t != T_OBJ) return;
    const Val *m = s;
    Val *x;
#define ADD(kw) add(out, L, join(P, kw))
    if ((x = get(m, "type"))) {
        int ok = 0;
        size_t cnt = x->t == T_ARR ? x->cnt : 1;
        for (size_t i = 0; i < cnt; i++) {
            Val *n = x->t == T_ARR ? x->items[i] : x;
            if (is_str(n, tname(inst))) ok = 1;
            else if (is_str(n, "integer") && inst->t == T_NUM && floor(inst->n) == inst->n) ok = 1;
        }
        if (!ok) ADD("type");
    }
    if ((x = get(m, "enum")) && x->t == T_ARR) {
        int ok = 0;
        for (size_t i = 0; i < x->cnt && !ok; i++) if (deep_eq(inst, x->items[i])) ok = 1;
        if (!ok) ADD("enum");
    }
    if ((x = get(m, "const"))) {
        if (!deep_eq(inst, x)) ADD("const");
    }
    if (inst->t == T_NUM) {
        double d = inst->n;
        if ((x = get(m, "minimum")) && !(d >= x->n)) ADD("minimum");
        if ((x = get(m, "maximum")) && !(d <= x->n)) ADD("maximum");
        if ((x = get(m, "exclusiveMinimum")) && !(d > x->n)) ADD("exclusiveMinimum");
        if ((x = get(m, "exclusiveMaximum")) && !(d < x->n)) ADD("exclusiveMaximum");
    }
    if (inst->t == T_STR) {
        double len = (double)cplen(inst);
        if ((x = get(m, "minLength")) && !(len >= x->n)) ADD("minLength");
        if ((x = get(m, "maxLength")) && !(len <= x->n)) ADD("maxLength");
    }
    if (inst->t == T_ARR) {
        double n = (double)inst->cnt;
        if ((x = get(m, "minItems")) && !(n >= x->n)) ADD("minItems");
        if ((x = get(m, "maxItems")) && !(n <= x->n)) ADD("maxItems");
        if ((x = get(m, "uniqueItems")) && x->t == T_BOOL && x->b) {
            int dup = 0;
            for (size_t a = 0; a < inst->cnt && !dup; a++)
                for (size_t b = a + 1; b < inst->cnt && !dup; b++)
                    if (deep_eq(inst->items[a], inst->items[b])) dup = 1;
            if (dup) ADD("uniqueItems");
        }
        size_t pl = 0;
        if ((x = get(m, "prefixItems")) && x->t == T_ARR) {
            pl = x->cnt;
            char *pp = join(P, "prefixItems");
            for (size_t k = 0; k < pl && k < inst->cnt; k++) {
                char *cl = joinidx(L, k), *cp = joinidx(pp, k);
                validate(inst->items[k], cl, x->items[k], cp, out);
                free(cl); free(cp);
            }
            free(pp);
        }
        if ((x = get(m, "items"))) {
            char *ip = join(P, "items");
            for (size_t k = pl; k < inst->cnt; k++) {
                char *cl = joinidx(L, k);
                validate(inst->items[k], cl, x, ip, out);
                free(cl);
            }
            free(ip);
        }
    }
    if (inst->t == T_OBJ) {
        Val *props = get(m, "properties");
        if (props) {
            char *pp = join(P, "properties");
            for (size_t i = 0; i < inst->cnt; i++) {
                Val *ps = getk(props, inst->keys[i], inst->klens[i]);
                if (ps) {
                    char *cl = joinesc(L, inst->keys[i], inst->klens[i]);
                    char *cp = joinesc(pp, inst->keys[i], inst->klens[i]);
                    validate(inst->items[i], cl, ps, cp, out);
                    free(cl); free(cp);
                }
            }
            free(pp);
        }
        if ((x = get(m, "required")) && x->t == T_ARR) {
            char *rp = join(P, "required");
            for (size_t j = 0; j < x->cnt; j++) {
                Val *nm = x->items[j];
                if (nm->t == T_STR && !getk(inst, nm->s, nm->slen)) add(out, L, joinidx(rp, j));
            }
            free(rp);
        }
        if ((x = get(m, "additionalProperties"))) {
            char *ap = join(P, "additionalProperties");
            for (size_t i = 0; i < inst->cnt; i++) {
                if (!getk(props, inst->keys[i], inst->klens[i])) {
                    char *cl = joinesc(L, inst->keys[i], inst->klens[i]);
                    validate(inst->items[i], cl, x, ap, out);
                    free(cl);
                }
            }
            free(ap);
        }
        double n = (double)inst->cnt;
        if ((x = get(m, "minProperties")) && !(n >= x->n)) ADD("minProperties");
        if ((x = get(m, "maxProperties")) && !(n <= x->n)) ADD("maxProperties");
    }
    if ((x = get(m, "allOf")) && x->t == T_ARR) {
        char *ap = join(P, "allOf");
        for (size_t k = 0; k < x->cnt; k++) { char *cp = joinidx(ap, k); validate(inst, L, x->items[k], cp, out); free(cp); }
        free(ap);
    }
    if ((x = get(m, "anyOf")) && x->t == T_ARR) {
        char *ap = join(P, "anyOf"); int ok = 0;
        for (size_t k = 0; k < x->cnt && !ok; k++) { char *cp = joinidx(ap, k); if (accepts(inst, L, x->items[k], cp)) ok = 1; free(cp); }
        free(ap);
        if (!ok) ADD("anyOf");
    }
    if ((x = get(m, "oneOf")) && x->t == T_ARR) {
        char *ap = join(P, "oneOf"); int c = 0;
        for (size_t k = 0; k < x->cnt; k++) { char *cp = joinidx(ap, k); if (accepts(inst, L, x->items[k], cp)) c++; free(cp); }
        free(ap);
        if (c != 1) ADD("oneOf");
    }
    if ((x = get(m, "not"))) {
        char *np = join(P, "not");
        int a = accepts(inst, L, x, np);
        free(np);
        if (a) ADD("not");
    }
    if ((x = get(m, "if"))) {
        char *ip = join(P, "if");
        int a = accepts(inst, L, x, ip);
        free(ip);
        Val *br = a ? get(m, "then") : get(m, "else");
        if (br) { char *bp = join(P, a ? "then" : "else"); validate(inst, L, br, bp, out); free(bp); }
    }
    if ((x = get(m, "$ref")) && x->t == T_STR) {
        if (x->slen == 1 && x->s[0] == '#') {
            validate(inst, L, root, "#", out);
        } else if (x->slen >= 8) {
            const char *t = x->s + 8; size_t tl = x->slen - 8;
            char *name = malloc(tl + 1); size_t nl = 0;
            for (size_t i = 0; i < tl; i++) {
                if (t[i] == '~' && i + 1 < tl && t[i + 1] == '1') { name[nl++] = '/'; i++; }
                else if (t[i] == '~' && i + 1 < tl && t[i + 1] == '0') { name[nl++] = '~'; i++; }
                else name[nl++] = t[i];
            }
            name[nl] = 0;
            Val *target = getk(get(root, "$defs"), name, nl);
            if (target) {
                char *rp = joinesc("#/$defs", name, nl);
                validate(inst, L, target, rp, out);
                free(rp);
            }
            free(name);
        }
    }
#undef ADD
}

static int cmp_err(const void *a, const void *b) {
    const Err *x = a, *y = b;
    int c = strcmp(x->l, y->l); /* strcmp compares as unsigned char = UTF-8 byte order */
    if (c) return c;
    return strcmp(x->p, y->p);
}

int main(int argc, char **argv) {
    (void)argc;
    char path[4096];
    snprintf(path, sizeof path, "%s/schema.json", argv[1]);
    root = parse_file(path);
    snprintf(path, sizeof path, "%s/instances.json", argv[1]);
    Val *insts = parse_file(path);
    int valid = 0, invalid = 0;
    for (size_t n = 0; n < insts->cnt; n++) {
        Errs e = {0};
        validate(insts->items[n], "#", root, "#", &e);
        if (e.n == 0) { valid++; printf("%zu: valid\n", n); continue; }
        invalid++;
        qsort(e.e, e.n, sizeof(Err), cmp_err);
        size_t k = 0;
        for (size_t i = 0; i < e.n; i++)
            if (i == 0 || cmp_err(&e.e[i], &e.e[k - 1]) != 0) e.e[k++] = e.e[i];
        printf("%zu: invalid (%zu)\n", n, k);
        for (size_t i = 0; i < k; i++) printf("  %s %s\n", e.e[i].l, e.e[i].p);
    }
    printf("valid=%d invalid=%d\n", valid, invalid);
    return 0;
}
