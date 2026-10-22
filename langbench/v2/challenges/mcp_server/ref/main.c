#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>

typedef struct { char *d; size_t n, cap; } Buf;
static void bput(Buf *b, const void *p, size_t n) {
    if (b->n + n + 1 > b->cap) { size_t c = b->cap ? b->cap * 2 : 64; while (c < b->n + n + 1) c *= 2; b->d = realloc(b->d, c); b->cap = c; }
    memcpy(b->d + b->n, p, n); b->n += n; b->d[b->n] = 0;
}
static void bc(Buf *b, char c) { bput(b, &c, 1); }
static void bs(Buf *b, const char *s) { bput(b, s, strlen(s)); }

enum { T_NULL, T_BOOL, T_NUM, T_STR, T_ARR, T_OBJ, T_RAW };
typedef struct V V;
struct V {
    int t; int b; int isInt;
    Buf s;            /* str / num raw / raw */
    V **items; size_t n, cap;   /* arr items or obj values */
    Buf *keys;        /* obj keys */
};
static V *nv(int t) { V *v = calloc(1, sizeof(V)); v->t = t; return v; }
static void vpush(V *a, Buf *key, V *x) {
    if (a->n == a->cap) { a->cap = a->cap ? a->cap * 2 : 4; a->items = realloc(a->items, a->cap * sizeof(V*)); if (a->t == T_OBJ) a->keys = realloc(a->keys, a->cap * sizeof(Buf)); }
    a->items[a->n] = x; if (key) a->keys[a->n] = *key; a->n++;
}
static int beq(const Buf *a, const char *s, size_t n) { return a->n == n && memcmp(a->d ? a->d : "", s, n) == 0; }
static V *oget(V *o, const char *k) {
    size_t n = strlen(k);
    for (size_t i = 0; i < o->n; i++) if (beq(&o->keys[i], k, n)) return o->items[i];
    return NULL;
}

typedef struct { const unsigned char *s; size_t n, p; } P;
static int fail_flag;
#define FAIL do { fail_flag = 1; return NULL; } while (0)
static void ws(P *p) { while (p->p < p->n && (p->s[p->p] == ' ' || p->s[p->p] == '\t' || p->s[p->p] == '\n' || p->s[p->p] == '\r')) p->p++; }
static int pk(P *p) { return p->p < p->n ? p->s[p->p] : -1; }
static int isd(int c) { return c >= '0' && c <= '9'; }

static void putcp(Buf *b, uint32_t cp) {
    unsigned char t[4];
    if (cp < 0x80) { t[0] = cp; bput(b, t, 1); }
    else if (cp < 0x800) { t[0] = 0xC0 | (cp >> 6); t[1] = 0x80 | (cp & 0x3F); bput(b, t, 2); }
    else if (cp < 0x10000) { t[0] = 0xE0 | (cp >> 12); t[1] = 0x80 | ((cp >> 6) & 0x3F); t[2] = 0x80 | (cp & 0x3F); bput(b, t, 3); }
    else { t[0] = 0xF0 | (cp >> 18); t[1] = 0x80 | ((cp >> 12) & 0x3F); t[2] = 0x80 | ((cp >> 6) & 0x3F); t[3] = 0x80 | (cp & 0x3F); bput(b, t, 4); }
}
/* decode one generalized utf-8 code point */
static uint32_t dec(const unsigned char *b, size_t n, size_t *i) {
    uint32_t c = b[*i];
    if (c < 0x80) { *i += 1; return c; }
    if ((c >> 5) == 6 && *i + 1 < n) { uint32_t r = ((c & 0x1F) << 6) | (b[*i + 1] & 0x3F); *i += 2; return r; }
    if ((c >> 4) == 14 && *i + 2 < n) { uint32_t r = ((c & 0x0F) << 12) | ((b[*i + 1] & 0x3F) << 6) | (b[*i + 2] & 0x3F); *i += 3; return r; }
    if (*i + 3 < n) { uint32_t r = ((c & 0x07) << 18) | ((b[*i + 1] & 0x3F) << 12) | ((b[*i + 2] & 0x3F) << 6) | (b[*i + 3] & 0x3F); *i += 4; return r; }
    *i += 1; return 0xFFFD;
}

static V *value(P *p);
static int hex4(P *p, uint32_t *out) {
    if (p->p + 4 > p->n) return 0;
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) {
        int c = p->s[p->p++], d;
        if (c >= '0' && c <= '9') d = c - '0'; else if (c >= 'a' && c <= 'f') d = c - 'a' + 10; else if (c >= 'A' && c <= 'F') d = c - 'A' + 10; else return 0;
        v = v * 16 + d;
    }
    *out = v; return 1;
}
static int pstr(P *p, Buf *b) {
    p->p++;
    memset(b, 0, sizeof *b); bput(b, "", 0);
    for (;;) {
        int c = pk(p); if (c < 0) return 0;
        p->p++;
        if (c == '"') return 1;
        if (c < 0x20) return 0;
        if (c == '\\') {
            int e = pk(p); if (e < 0) return 0;
            p->p++;
            switch (e) {
                case '"': case '\\': case '/': bc(b, (char)e); break;
                case 'b': bc(b, 8); break; case 'f': bc(b, 12); break; case 'n': bc(b, 10); break;
                case 'r': bc(b, 13); break; case 't': bc(b, 9); break;
                case 'u': {
                    uint32_t cp; if (!hex4(p, &cp)) return 0;
                    if (cp >= 0xD800 && cp < 0xDC00 && p->p + 1 < p->n && p->s[p->p] == '\\' && p->s[p->p + 1] == 'u') {
                        size_t save = p->p; uint32_t lo;
                        p->p += 2;
                        if (!hex4(p, &lo)) return 0;
                        if (lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); else p->p = save;
                    }
                    putcp(b, cp); break;
                }
                default: return 0;
            }
        } else bc(b, (char)c);
    }
}
static V *lit(P *p, const char *w, V *v) {
    size_t n = strlen(w);
    if (p->p + n > p->n || memcmp(p->s + p->p, w, n) != 0) FAIL;
    p->p += n; return v;
}
static V *value(P *p) {
    int c = pk(p);
    if (c < 0) FAIL;
    if (c == '{') {
        p->p++; V *o = nv(T_OBJ); ws(p);
        if (pk(p) == '}') { p->p++; return o; }
        for (;;) {
            ws(p); if (pk(p) != '"') FAIL;
            Buf k; if (!pstr(p, &k)) FAIL;
            ws(p); if (pk(p) != ':') FAIL;
            p->p++; ws(p);
            V *v = value(p); if (!v) FAIL;
            /* duplicate key: last wins */
            size_t j = 0;
            for (size_t i = 0; i < o->n; i++) if (!beq(&o->keys[i], k.d, k.n)) { o->keys[j] = o->keys[i]; o->items[j] = o->items[i]; j++; }
            o->n = j;
            vpush(o, &k, v);
            ws(p);
            int d = pk(p); if (d < 0) FAIL;
            p->p++;
            if (d == '}') return o;
            if (d != ',') FAIL;
        }
    }
    if (c == '[') {
        p->p++; V *a = nv(T_ARR); ws(p);
        if (pk(p) == ']') { p->p++; return a; }
        for (;;) {
            ws(p);
            V *v = value(p); if (!v) FAIL;
            vpush(a, NULL, v);
            ws(p);
            int d = pk(p); if (d < 0) FAIL;
            p->p++;
            if (d == ']') return a;
            if (d != ',') FAIL;
        }
    }
    if (c == '"') { V *v = nv(T_STR); if (!pstr(p, &v->s)) FAIL; return v; }
    if (c == 't') { V *v = nv(T_BOOL); v->b = 1; return lit(p, "true", v); }
    if (c == 'f') { V *v = nv(T_BOOL); v->b = 0; return lit(p, "false", v); }
    if (c == 'n') return lit(p, "null", nv(T_NULL));
    if (c == '-' || isd(c)) {
        size_t st = p->p; int isInt = 1;
        if (pk(p) == '-') p->p++;
        if (pk(p) == '0') p->p++;
        else if (isd(pk(p))) { while (isd(pk(p))) p->p++; }
        else FAIL;
        if (pk(p) == '.') { isInt = 0; p->p++; size_t d = p->p; while (isd(pk(p))) p->p++; if (p->p == d) FAIL; }
        if (pk(p) == 'e' || pk(p) == 'E') {
            isInt = 0; p->p++;
            if (pk(p) == '+' || pk(p) == '-') p->p++;
            size_t d = p->p; while (isd(pk(p))) p->p++; if (p->p == d) FAIL;
        }
        V *v = nv(T_NUM); v->isInt = isInt; bput(&v->s, p->s + st, p->p - st); return v;
    }
    FAIL;
}
static V *parse(const unsigned char *s, size_t n) {
    P p = { s, n, 0 };
    fail_flag = 0;
    ws(&p);
    V *v = value(&p);
    if (!v || fail_flag) return NULL;
    ws(&p);
    if (p.p != n) return NULL;
    return v;
}

static void u4(Buf *o, uint32_t c) { char t[8]; snprintf(t, sizeof t, "\\u%04x", (unsigned)c); bs(o, t); }
static void serstr(Buf *o, const char *s, size_t n) {
    bc(o, '"');
    size_t i = 0;
    while (i < n) {
        uint32_t cp = dec((const unsigned char *)s, n, &i);
        if (cp == '"') bs(o, "\\\"");
        else if (cp == '\\') bs(o, "\\\\");
        else if (cp >= 0x20 && cp < 0x7F) bc(o, (char)cp);
        else if (cp < 0x10000) u4(o, cp);
        else { uint32_t c = cp - 0x10000; u4(o, 0xD800 + (c >> 10)); u4(o, 0xDC00 + (c & 0x3FF)); }
    }
    bc(o, '"');
}
static void ser(Buf *o, V *v) {
    switch (v->t) {
        case T_NULL: bs(o, "null"); break;
        case T_BOOL: bs(o, v->b ? "true" : "false"); break;
        case T_NUM: case T_RAW: bput(o, v->s.d, v->s.n); break;
        case T_STR: serstr(o, v->s.d, v->s.n); break;
        case T_ARR: bc(o, '['); for (size_t i = 0; i < v->n; i++) { if (i) bc(o, ','); ser(o, v->items[i]); } bc(o, ']'); break;
        case T_OBJ: bc(o, '{'); for (size_t i = 0; i < v->n; i++) { if (i) bc(o, ','); serstr(o, v->keys[i].d, v->keys[i].n); bc(o, ':'); ser(o, v->items[i]); } bc(o, '}'); break;
    }
}

static const char *TOOLS = "[{\"name\":\"kv_set\",\"title\":\"Set a value\",\"description\":\"Store a string value under a key, replacing any previous value.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"key\":{\"type\":\"string\",\"minLength\":1},\"value\":{\"type\":\"string\"}},\"required\":[\"key\",\"value\"],\"additionalProperties\":false}},{\"name\":\"kv_get\",\"title\":\"Get a value\",\"description\":\"Return the value stored under a key.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"key\":{\"type\":\"string\",\"minLength\":1}},\"required\":[\"key\"],\"additionalProperties\":false}},{\"name\":\"kv_list\",\"title\":\"List keys\",\"description\":\"List the stored keys that start with a prefix, sorted by Unicode code point.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"prefix\":{\"type\":\"string\"}},\"additionalProperties\":false},\"outputSchema\":{\"type\":\"object\",\"properties\":{\"keys\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"count\":{\"type\":\"integer\"}},\"required\":[\"keys\",\"count\"]}},{\"name\":\"sum\",\"title\":\"Sum integers\",\"description\":\"Add up a non-empty list of integers.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"numbers\":{\"type\":\"array\",\"items\":{\"type\":\"integer\"},\"minItems\":1}},\"required\":[\"numbers\"],\"additionalProperties\":false},\"outputSchema\":{\"type\":\"object\",\"properties\":{\"sum\":{\"type\":\"integer\"},\"count\":{\"type\":\"integer\"}},\"required\":[\"sum\",\"count\"]},\"annotations\":{\"readOnlyHint\":true,\"idempotentHint\":true}},{\"name\":\"text_stats\",\"title\":\"Text statistics\",\"description\":\"Count the words, Unicode code points and UTF-8 bytes of a text.\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"text\":{\"type\":\"string\"}},\"required\":[\"text\"],\"additionalProperties\":false},\"outputSchema\":{\"type\":\"object\",\"properties\":{\"words\":{\"type\":\"integer\"},\"codepoints\":{\"type\":\"integer\"},\"utf8_bytes\":{\"type\":\"integer\"}},\"required\":[\"words\",\"codepoints\",\"utf8_bytes\"]},\"annotations\":{\"readOnlyHint\":true}}]";

static V *mkstr(const char *s, size_t n) { V *v = nv(T_STR); bput(&v->s, s, n); return v; }
static V *mkcs(const char *s) { return mkstr(s, strlen(s)); }
static V *mkint(long long x) { V *v = nv(T_NUM); v->isInt = 1; char t[32]; snprintf(t, sizeof t, "%lld", x); bs(&v->s, t); return v; }
static V *mkbool(int b) { V *v = nv(T_BOOL); v->b = b; return v; }
static V *mkraw(const char *s) { V *v = nv(T_RAW); bs(&v->s, s); return v; }
static void oput(V *o, const char *k, V *x) { Buf kb = {0}; bs(&kb, k); vpush(o, &kb, x); }

static void error_resp(Buf *out, V *id, int code, const char *msg, size_t mn) {
    bs(out, "{\"jsonrpc\":\"2.0\",\"id\":"); ser(out, id);
    char t[64]; snprintf(t, sizeof t, ",\"error\":{\"code\":%d,\"message\":", code); bs(out, t);
    serstr(out, msg, mn); bs(out, "}}\n");
}
static void err(Buf *out, V *id, int code, const char *msg) { error_resp(out, id, code, msg, strlen(msg)); }
static void result_resp(Buf *out, V *id, V *r) {
    bs(out, "{\"jsonrpc\":\"2.0\",\"id\":"); ser(out, id); bs(out, ",\"result\":"); ser(out, r); bs(out, "}\n");
}
static V *text_result(const char *t, size_t n, int isErr) {
    V *c = nv(T_OBJ); oput(c, "type", mkcs("text")); oput(c, "text", mkstr(t, n));
    V *a = nv(T_ARR); vpush(a, NULL, c);
    V *r = nv(T_OBJ); oput(r, "content", a); oput(r, "isError", mkbool(isErr));
    return r;
}
static V *failv(const char *t) { return text_result(t, strlen(t), 1); }
static V *fail2(const char *pre, const char *s, size_t n) { Buf b = {0}; bs(&b, pre); bput(&b, s, n); return text_result(b.d, b.n, 1); }
static V *fail3(const char *a, const char *b, const char *c) { Buf x = {0}; bs(&x, a); bs(&x, b); bs(&x, c); return text_result(x.d, x.n, 1); }
static V *struct_result(V *S) {
    Buf t = {0}; ser(&t, S);
    V *c = nv(T_OBJ); oput(c, "type", mkcs("text")); oput(c, "text", mkstr(t.d, t.n));
    V *a = nv(T_ARR); vpush(a, NULL, c);
    V *r = nv(T_OBJ); oput(r, "content", a); oput(r, "structuredContent", S); oput(r, "isError", mkbool(0));
    return r;
}

static int bcmp2(const char *a, size_t an, const char *b, size_t bn) {
    size_t m = an < bn ? an : bn;
    int c = m ? memcmp(a, b, m) : 0;
    if (c) return c;
    return an < bn ? -1 : an > bn ? 1 : 0;
}

/* sorted kv store */
static Buf *K, *VV; static size_t KN, KCAP;
static size_t kfind(const char *k, size_t n, int *found) {
    size_t lo = 0, hi = KN;
    while (lo < hi) { size_t mid = (lo + hi) / 2; int c = bcmp2(K[mid].d, K[mid].n, k, n); if (c == 0) { *found = 1; return mid; } if (c < 0) lo = mid + 1; else hi = mid; }
    *found = 0; return lo;
}

typedef struct { const char *name; int isStr, req, nonEmpty; } Prop;
typedef struct { const char *name; Prop props[2]; int np; } Tool;
static Tool TOOLSPEC[] = {
    {"kv_set", {{"key", 1, 1, 1}, {"value", 1, 1, 0}}, 2},
    {"kv_get", {{"key", 1, 1, 1}}, 1},
    {"kv_list", {{"prefix", 1, 0, 0}}, 1},
    {"sum", {{"numbers", 0, 1, 1}}, 1},
    {"text_stats", {{"text", 1, 1, 0}}, 1},
};

static V *call_tool(int ti, V *args) {
    Tool *tl = &TOOLSPEC[ti];
    V empty = {0}; empty.t = T_OBJ;
    if (!args) args = &empty;
    else if (args->t != T_OBJ) return failv("arguments must be an object");
    Buf *unk = NULL;
    for (size_t i = 0; i < args->n; i++) {
        int known = 0;
        for (int j = 0; j < tl->np; j++) if (beq(&args->keys[i], tl->props[j].name, strlen(tl->props[j].name))) known = 1;
        if (!known && (!unk || bcmp2(args->keys[i].d, args->keys[i].n, unk->d, unk->n) < 0)) unk = &args->keys[i];
    }
    if (unk) return fail2("unknown argument: ", unk->d, unk->n);
    for (int j = 0; j < tl->np; j++) {
        Prop *pr = &tl->props[j];
        V *v = oget(args, pr->name);
        if (!v) { if (pr->req) return fail3("missing required argument: ", pr->name, ""); continue; }
        if (pr->isStr) {
            if (v->t != T_STR) return fail3("argument ", pr->name, " must be a string");
            if (pr->nonEmpty && v->s.n == 0) return fail3("argument ", pr->name, " must not be empty");
        } else {
            int ok = v->t == T_ARR;
            if (ok) for (size_t i = 0; i < v->n; i++) if (!(v->items[i]->t == T_NUM && v->items[i]->isInt)) ok = 0;
            if (!ok) return fail3("argument ", pr->name, " must be an array of integers");
            if (pr->nonEmpty && v->n == 0) return fail3("argument ", pr->name, " must not be empty");
        }
    }
    if (ti == 0) {
        V *k = oget(args, "key"), *val = oget(args, "value");
        int found; size_t pos = kfind(k->s.d, k->s.n, &found);
        Buf nvb = {0}; bput(&nvb, val->s.d, val->s.n);
        if (found) VV[pos] = nvb;
        else {
            if (KN == KCAP) { KCAP = KCAP ? KCAP * 2 : 16; K = realloc(K, KCAP * sizeof(Buf)); VV = realloc(VV, KCAP * sizeof(Buf)); }
            memmove(K + pos + 1, K + pos, (KN - pos) * sizeof(Buf)); memmove(VV + pos + 1, VV + pos, (KN - pos) * sizeof(Buf));
            Buf kb = {0}; bput(&kb, k->s.d, k->s.n);
            K[pos] = kb; VV[pos] = nvb; KN++;
        }
        Buf t = {0}; bs(&t, found ? "updated " : "created "); bput(&t, k->s.d, k->s.n);
        return text_result(t.d, t.n, 0);
    }
    if (ti == 1) {
        V *k = oget(args, "key");
        int found; size_t pos = kfind(k->s.d, k->s.n, &found);
        if (!found) return fail2("key not found: ", k->s.d, k->s.n);
        return text_result(VV[pos].d, VV[pos].n, 0);
    }
    if (ti == 2) {
        V *pv = oget(args, "prefix");
        const char *pre = pv ? pv->s.d : ""; size_t pn = pv ? pv->s.n : 0;
        V *keys = nv(T_ARR);
        for (size_t i = 0; i < KN; i++) if (K[i].n >= pn && (pn == 0 || memcmp(K[i].d, pre, pn) == 0)) vpush(keys, NULL, mkstr(K[i].d, K[i].n));
        V *S = nv(T_OBJ); oput(S, "keys", keys); oput(S, "count", mkint((long long)keys->n));
        return struct_result(S);
    }
    if (ti == 3) {
        V *a = oget(args, "numbers");
        long long sum = 0;
        for (size_t i = 0; i < a->n; i++) sum += strtoll(a->items[i]->s.d, NULL, 10);
        V *S = nv(T_OBJ); oput(S, "sum", mkint(sum)); oput(S, "count", mkint((long long)a->n));
        return struct_result(S);
    }
    V *t = oget(args, "text");
    long long words = 0, n = 0, bytes = 0; int inw = 0;
    size_t i = 0;
    while (i < t->s.n) {
        uint32_t cp = dec((const unsigned char *)t->s.d, t->s.n, &i);
        n++;
        bytes += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
        int sep = cp == 32 || cp == 9 || cp == 10 || cp == 13;
        if (!sep && !inw) words++;
        inw = !sep;
    }
    V *S = nv(T_OBJ); oput(S, "words", mkint(words)); oput(S, "codepoints", mkint(n)); oput(S, "utf8_bytes", mkint(bytes));
    return struct_result(S);
}

static int initialized = 0;
static V NULLV = { T_NULL };

static void handle(Buf *out, const unsigned char *line, size_t n) {
    V *m = parse(line, n);
    if (!m) { err(out, &NULLV, -32700, "Parse error"); return; }
    if (m->t != T_OBJ) { err(out, &NULLV, -32600, "Invalid Request"); return; }
    V *meth = oget(m, "method");
    if (!meth && (oget(m, "result") || oget(m, "error"))) return;
    V *id = oget(m, "id");
    int idOk = id && (id->t == T_STR || (id->t == T_NUM && id->isInt));
    V *jr = oget(m, "jsonrpc");
    int jrOk = jr && jr->t == T_STR && beq(&jr->s, "2.0", 3);
    if (!jrOk || !meth || meth->t != T_STR || (id && !idOk)) { err(out, idOk ? id : &NULLV, -32600, "Invalid Request"); return; }
    if (!id) return;
    Buf *mb = &meth->s;
    int isInit = beq(mb, "initialize", 10), isPing = beq(mb, "ping", 4), isList = beq(mb, "tools/list", 10), isCall = beq(mb, "tools/call", 10);
    if (!(isInit || isPing || isList || isCall)) { err(out, id, -32601, "Method not found"); return; }
    V *pm = oget(m, "params");
    if (pm && pm->t != T_OBJ) { err(out, id, -32602, "Invalid params"); return; }
    if (!initialized && !isInit && !isPing) { err(out, id, -32002, "Server not initialized"); return; }
    if (isInit) {
        if (initialized) { err(out, id, -32600, "Already initialized"); return; }
        V *pv = pm ? oget(pm, "protocolVersion") : NULL;
        if (!pv || pv->t != T_STR) { err(out, id, -32602, "Invalid params"); return; }
        const char *v = "2025-06-18";
        if (beq(&pv->s, "2025-03-26", 10)) v = "2025-03-26";
        else if (beq(&pv->s, "2024-11-05", 10)) v = "2024-11-05";
        initialized = 1;
        Buf r = {0}; bs(&r, "{\"protocolVersion\":\""); bs(&r, v);
        bs(&r, "\",\"capabilities\":{\"tools\":{\"listChanged\":false}},\"serverInfo\":{\"name\":\"cb2-kv\",\"title\":\"CB2 key-value server\",\"version\":\"1.4.2\"}}");
        result_resp(out, id, mkraw(r.d)); return;
    }
    if (isPing) { result_resp(out, id, mkraw("{}")); return; }
    if (isList) { Buf r = {0}; bs(&r, "{\"tools\":"); bs(&r, TOOLS); bc(&r, '}'); result_resp(out, id, mkraw(r.d)); return; }
    V *nm = pm ? oget(pm, "name") : NULL;
    if (!nm || nm->t != T_STR) { err(out, id, -32602, "Invalid params"); return; }
    int ti = -1;
    for (int i = 0; i < 5; i++) if (beq(&nm->s, TOOLSPEC[i].name, strlen(TOOLSPEC[i].name))) ti = i;
    if (ti < 0) { Buf t = {0}; bs(&t, "Unknown tool: "); bput(&t, nm->s.d, nm->s.n); error_resp(out, id, -32602, t.d, t.n); return; }
    result_resp(out, id, call_tool(ti, oget(pm, "arguments")));
}

/* replace invalid UTF-8 with U+FFFD */
static void sanitize(const unsigned char *s, size_t n, Buf *o) {
    size_t i = 0;
    while (i < n) {
        unsigned c = s[i];
        size_t len = 0; uint32_t min = 0;
        if (c < 0x80) { bc(o, (char)c); i++; continue; }
        else if (c >= 0xC2 && c <= 0xDF) { len = 2; min = 0x80; }
        else if (c >= 0xE0 && c <= 0xEF) { len = 3; min = 0x800; }
        else if (c >= 0xF0 && c <= 0xF4) { len = 4; min = 0x10000; }
        int ok = len && i + len <= n;
        uint32_t cp = 0;
        if (ok) {
            cp = c & (len == 2 ? 0x1F : len == 3 ? 0x0F : 0x07);
            for (size_t k = 1; k < len; k++) { if ((s[i + k] & 0xC0) != 0x80) { ok = 0; break; } cp = (cp << 6) | (s[i + k] & 0x3F); }
            if (ok && (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp < 0xE000))) ok = 0;
        }
        if (ok) { bput(o, s + i, len); i += len; }
        else { bs(o, "\xEF\xBF\xBD"); i++; }
    }
}

static void *run(void *arg) {
    (void)arg;
    Buf in = {0}; char tmp[65536]; size_t r;
    bput(&in, "", 0);
    while ((r = fread(tmp, 1, sizeof tmp, stdin)) > 0) bput(&in, tmp, r);
    Buf out = {0}; bput(&out, "", 0);
    size_t st = 0;
    for (size_t i = 0; i <= in.n; i++) {
        if (i == in.n || in.d[i] == '\n') {
            if (i == in.n && st == i) break;
            const unsigned char *l = (const unsigned char *)in.d + st; size_t ln = i - st;
            st = i + 1;
            int blank = 1;
            for (size_t k = 0; k < ln; k++) if (l[k] != ' ' && l[k] != '\t' && l[k] != '\r') { blank = 0; break; }
            if (blank) continue;
            Buf sl = {0}; bput(&sl, "", 0); sanitize(l, ln, &sl);
            handle(&out, (const unsigned char *)sl.d, sl.n);
            free(sl.d);
        }
    }
    fwrite(out.d, 1, out.n, stdout);
    fflush(stdout);
    return NULL;
}

int main(void) {
    pthread_attr_t a; pthread_t t;
    pthread_attr_init(&a);
    pthread_attr_setstacksize(&a, (size_t)1 << 30);
    if (pthread_create(&t, &a, run, NULL) != 0) run(NULL);
    else pthread_join(t, NULL);
    return 0;
}
