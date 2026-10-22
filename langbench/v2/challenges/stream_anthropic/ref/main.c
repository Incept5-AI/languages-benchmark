#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <signal.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>

/* ---------- string builder ---------- */
typedef struct { char *p; size_t n, cap; } SB;
static void sb_grow(SB *b, size_t add) {
    if (b->n + add + 1 > b->cap) {
        size_t c = b->cap ? b->cap : 64;
        while (c < b->n + add + 1) c *= 2;
        b->p = realloc(b->p, c); b->cap = c;
    }
}
static void sb_addn(SB *b, const char *s, size_t n) { sb_grow(b, n); memcpy(b->p + b->n, s, n); b->n += n; b->p[b->n] = 0; }
static void sb_adds(SB *b, const char *s) { sb_addn(b, s, strlen(s)); }
static void sb_addc(SB *b, char c) { sb_addn(b, &c, 1); }
static void sb_init(SB *b) { b->p = NULL; b->n = b->cap = 0; sb_grow(b, 0); b->p[0] = 0; }

/* ---------- JSON ---------- */
enum { JNULL, JBOOL, JNUM, JSTR, JARR, JOBJ };
typedef struct J J;
struct J {
    int t;
    int b;
    char *s; size_t sl;      /* string bytes or number raw */
    J **items; char **keys; size_t *klens; size_t n, cap;
};
static J *jnew(int t) { J *j = calloc(1, sizeof(J)); j->t = t; return j; }
static J *jstr(const char *s, size_t n) { J *j = jnew(JSTR); j->s = malloc(n + 1); memcpy(j->s, s, n); j->s[n] = 0; j->sl = n; return j; }
static J *jcstr(const char *s) { return jstr(s, strlen(s)); }
static void jpush(J *a, const char *k, size_t kl, J *v) {
    if (a->n == a->cap) {
        a->cap = a->cap ? a->cap * 2 : 4;
        a->items = realloc(a->items, a->cap * sizeof(J *));
        a->keys = realloc(a->keys, a->cap * sizeof(char *));
        a->klens = realloc(a->klens, a->cap * sizeof(size_t));
    }
    a->items[a->n] = v;
    if (k) { a->keys[a->n] = malloc(kl + 1); memcpy(a->keys[a->n], k, kl); a->keys[a->n][kl] = 0; a->klens[a->n] = kl; }
    else { a->keys[a->n] = NULL; a->klens[a->n] = 0; }
    a->n++;
}
static void jset(J *o, const char *k, J *v) { jpush(o, k, strlen(k), v); }
static J *jget(J *o, const char *k) {
    if (!o || o->t != JOBJ) return NULL;
    size_t kl = strlen(k);
    for (size_t i = 0; i < o->n; i++) if (o->klens[i] == kl && memcmp(o->keys[i], k, kl) == 0) return o->items[i];
    return NULL;
}
static const char *jgets(J *o, const char *k, size_t *len) {
    J *v = jget(o, k);
    if (v && v->t == JSTR) { if (len) *len = v->sl; return v->s; }
    if (len) *len = 0;
    return "";
}
static long long jgetn(J *o, const char *k) {
    J *v = jget(o, k);
    if (v && v->t == JNUM) return (long long)strtod(v->s, NULL);
    return 0;
}

typedef struct { const char *s; size_t n, p; int err; } JP;
static void jws(JP *p) { while (p->p < p->n && (p->s[p->p] == ' ' || p->s[p->p] == '\t' || p->s[p->p] == '\r' || p->s[p->p] == '\n')) p->p++; }
static void put_utf8(SB *b, unsigned cp) {
    char c[4];
    if (cp < 0x80) { c[0] = cp; sb_addn(b, c, 1); }
    else if (cp < 0x800) { c[0] = 0xC0 | (cp >> 6); c[1] = 0x80 | (cp & 63); sb_addn(b, c, 2); }
    else if (cp < 0x10000) { c[0] = 0xE0 | (cp >> 12); c[1] = 0x80 | ((cp >> 6) & 63); c[2] = 0x80 | (cp & 63); sb_addn(b, c, 3); }
    else { c[0] = 0xF0 | (cp >> 18); c[1] = 0x80 | ((cp >> 12) & 63); c[2] = 0x80 | ((cp >> 6) & 63); c[3] = 0x80 | (cp & 63); sb_addn(b, c, 4); }
}
static int hex4(JP *p, unsigned *out) {
    if (p->p + 4 > p->n) return 0;
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p->s[p->p + i]; v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
        else return 0;
    }
    p->p += 4; *out = v; return 1;
}
static int jpstr(JP *p, SB *b) {
    if (p->p >= p->n || p->s[p->p] != '"') return 0;
    p->p++;
    while (p->p < p->n) {
        char c = p->s[p->p++];
        if (c == '"') return 1;
        if (c == '\\') {
            if (p->p >= p->n) return 0;
            char e = p->s[p->p++];
            switch (e) {
                case 'n': sb_addc(b, '\n'); break;
                case 'r': sb_addc(b, '\r'); break;
                case 't': sb_addc(b, '\t'); break;
                case 'b': sb_addc(b, '\b'); break;
                case 'f': sb_addc(b, '\f'); break;
                case 'u': {
                    unsigned cp;
                    if (!hex4(p, &cp)) return 0;
                    if (cp >= 0xD800 && cp < 0xDC00 && p->p + 1 < p->n && p->s[p->p] == '\\' && p->s[p->p + 1] == 'u') {
                        size_t save = p->p; unsigned lo;
                        p->p += 2;
                        if (hex4(p, &lo) && lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        else p->p = save;
                    }
                    put_utf8(b, cp);
                    break;
                }
                default: sb_addc(b, e);
            }
        } else sb_addc(b, c);
    }
    return 0;
}
static J *jpval(JP *p) {
    jws(p);
    if (p->p >= p->n) { p->err = 1; return NULL; }
    char c = p->s[p->p];
    if (c == '{') {
        J *o = jnew(JOBJ); p->p++; jws(p);
        if (p->p < p->n && p->s[p->p] == '}') { p->p++; return o; }
        for (;;) {
            jws(p);
            SB k; sb_init(&k);
            if (!jpstr(p, &k)) { p->err = 1; return NULL; }
            jws(p);
            if (p->p >= p->n || p->s[p->p] != ':') { p->err = 1; return NULL; }
            p->p++;
            J *v = jpval(p); if (!v) return NULL;
            jpush(o, k.p, k.n, v); free(k.p);
            jws(p);
            if (p->p >= p->n) { p->err = 1; return NULL; }
            char d = p->s[p->p++];
            if (d == '}') return o;
            if (d != ',') { p->err = 1; return NULL; }
        }
    }
    if (c == '[') {
        J *a = jnew(JARR); p->p++; jws(p);
        if (p->p < p->n && p->s[p->p] == ']') { p->p++; return a; }
        for (;;) {
            J *v = jpval(p); if (!v) return NULL;
            jpush(a, NULL, 0, v);
            jws(p);
            if (p->p >= p->n) { p->err = 1; return NULL; }
            char d = p->s[p->p++];
            if (d == ']') return a;
            if (d != ',') { p->err = 1; return NULL; }
        }
    }
    if (c == '"') {
        SB b; sb_init(&b);
        if (!jpstr(p, &b)) { p->err = 1; return NULL; }
        J *j = jnew(JSTR); j->s = b.p; j->sl = b.n; return j;
    }
    if (p->n - p->p >= 4 && !memcmp(p->s + p->p, "true", 4)) { p->p += 4; J *j = jnew(JBOOL); j->b = 1; return j; }
    if (p->n - p->p >= 5 && !memcmp(p->s + p->p, "false", 5)) { p->p += 5; return jnew(JBOOL); }
    if (p->n - p->p >= 4 && !memcmp(p->s + p->p, "null", 4)) { p->p += 4; return jnew(JNULL); }
    size_t st = p->p;
    while (p->p < p->n && strchr("+-0123456789.eE", p->s[p->p]) && p->s[p->p]) p->p++;
    if (st == p->p) { p->err = 1; return NULL; }
    J *j = jnew(JNUM); j->s = malloc(p->p - st + 1); memcpy(j->s, p->s + st, p->p - st); j->s[p->p - st] = 0; j->sl = p->p - st;
    return j;
}
static J *jparse(const char *s, size_t n) {
    JP p = { s, n, 0, 0 };
    J *v = jpval(&p);
    if (!v || p.err) return NULL;
    jws(&p);
    if (p.p != n) return NULL;
    return v;
}
static void jser_str(SB *o, const char *s, size_t n) {
    sb_addc(o, '"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = s[i];
        if (c == '"') sb_adds(o, "\\\"");
        else if (c == '\\') sb_adds(o, "\\\\");
        else if (c == '\n') sb_adds(o, "\\n");
        else if (c == '\r') sb_adds(o, "\\r");
        else if (c == '\t') sb_adds(o, "\\t");
        else if (c < 0x20) { char t[8]; snprintf(t, sizeof t, "\\u%04x", c); sb_adds(o, t); }
        else sb_addc(o, c);
    }
    sb_addc(o, '"');
}
static void jser(SB *o, J *v) {
    if (!v) { sb_adds(o, "null"); return; }
    switch (v->t) {
        case JNULL: sb_adds(o, "null"); break;
        case JBOOL: sb_adds(o, v->b ? "true" : "false"); break;
        case JNUM: sb_addn(o, v->s, v->sl); break;
        case JSTR: jser_str(o, v->s, v->sl); break;
        case JARR:
            sb_addc(o, '[');
            for (size_t i = 0; i < v->n; i++) { if (i) sb_addc(o, ','); jser(o, v->items[i]); }
            sb_addc(o, ']'); break;
        case JOBJ:
            sb_addc(o, '{');
            for (size_t i = 0; i < v->n; i++) { if (i) sb_addc(o, ','); jser_str(o, v->keys[i], v->klens[i]); sb_addc(o, ':'); jser(o, v->items[i]); }
            sb_addc(o, '}'); break;
    }
}

/* ---------- output ---------- */
static SB OUT;
static void esc_add(SB *o, const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (c == '\\') sb_adds(o, "\\\\");
        else if (c == '\n') sb_adds(o, "\\n");
        else if (c == '\r') sb_adds(o, "\\r");
        else if (c == '\t') sb_adds(o, "\\t");
        else sb_addc(o, c);
    }
}
static void flush_out(void) { fwrite(OUT.p, 1, OUT.n, stdout); fflush(stdout); OUT.n = 0; }

/* ---------- HTTP ---------- */
static char host[256], prefix[1024], port_s[16];
static const char *api_key = "";

typedef struct { int fd; char buf[65536]; size_t pos, len; int eof; } Conn;
static int cgetc(Conn *c) {
    if (c->pos >= c->len) {
        if (c->eof) return -1;
        ssize_t r = read(c->fd, c->buf, sizeof c->buf);
        if (r <= 0) { c->eof = 1; return -1; }
        c->len = (size_t)r; c->pos = 0;
    }
    return (unsigned char)c->buf[c->pos++];
}
static int creadline(Conn *c, SB *line) {
    line->n = 0; line->p[0] = 0;
    int ch, any = 0;
    while ((ch = cgetc(c)) != -1) { any = 1; if (ch == '\n') break; sb_addc(line, (char)ch); }
    if (!any) return 0;
    if (line->n && line->p[line->n - 1] == '\r') line->p[--line->n] = 0;
    return 1;
}

static int http_post(const char *body, size_t blen, SB *resp) {
    struct addrinfo hints, *res = NULL;
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_INET; hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, port_s, &hints, &res) != 0 || !res) return 0;
    int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd < 0 || connect(fd, res->ai_addr, res->ai_addrlen) != 0) { freeaddrinfo(res); return 0; }
    freeaddrinfo(res);
    SB req; sb_init(&req);
    char tmp[2048];
    snprintf(tmp, sizeof tmp, "POST %s/v1/messages HTTP/1.1\r\nHost: %s:%s\r\nContent-Length: %zu\r\nx-api-key: ", prefix, host, port_s, blen);
    sb_adds(&req, tmp); sb_adds(&req, api_key);
    sb_adds(&req, "\r\nanthropic-version: 2023-06-01\r\ncontent-type: application/json\r\n\r\n");
    sb_addn(&req, body, blen);
    size_t off = 0;
    while (off < req.n) { ssize_t w = write(fd, req.p + off, req.n - off); if (w <= 0) break; off += (size_t)w; }
    free(req.p);

    Conn *c = calloc(1, sizeof(Conn)); c->fd = fd;
    SB line; sb_init(&line);
    int status = 0;
    if (creadline(c, &line)) { char *sp = strchr(line.p, ' '); if (sp) status = atoi(sp + 1); }
    long cl = -1; int chunked = 0;
    while (creadline(c, &line) && line.n > 0) {
        char *col = strchr(line.p, ':'); if (!col) continue;
        *col = 0;
        char *k = line.p, *v = col + 1;
        while (*v == ' ' || *v == '\t') v++;
        for (char *q = k; *q; q++) *q = (char)tolower((unsigned char)*q);
        while (*k == ' ') k++;
        size_t kl = strlen(k); while (kl && k[kl - 1] == ' ') k[--kl] = 0;
        if (!strcmp(k, "content-length")) cl = atol(v);
        if (!strcmp(k, "transfer-encoding")) { for (char *q = v; *q; q++) *q = (char)tolower((unsigned char)*q); if (strstr(v, "chunked")) chunked = 1; }
    }
    resp->n = 0;
    if (chunked) {
        while (creadline(c, &line)) {
            char *semi = strchr(line.p, ';'); if (semi) *semi = 0;
            char *q = line.p; while (*q == ' ' || *q == '\t') q++;
            if (!*q) continue;
            char *end; long sz = strtol(q, &end, 16);
            if (end == q) break;
            if (sz == 0) break;
            long got = 0; int ch;
            while (got < sz && (ch = cgetc(c)) != -1) { sb_addc(resp, (char)ch); got++; }
            if (got < sz) break;
            creadline(c, &line);
        }
    } else if (cl >= 0) {
        long got = 0; int ch;
        while (got < cl && (ch = cgetc(c)) != -1) { sb_addc(resp, (char)ch); got++; }
    } else {
        int ch; while ((ch = cgetc(c)) != -1) sb_addc(resp, (char)ch);
    }
    free(line.p); free(c);
    close(fd);
    return status;
}

/* ---------- SSE ---------- */
typedef struct { long long idx; char type[32]; SB text, sig, json; char *id, *name; } Block;
typedef struct { Block *b; size_t n, cap; char *stop; } Reply;
static long long uIn, uOut, uCache;

static Block *find_block(Reply *r, long long idx) {
    for (size_t i = 0; i < r->n; i++) if (r->b[i].idx == idx) return &r->b[i];
    return NULL;
}
static char *dupn(const char *s, size_t n) { char *d = malloc(n + 1); memcpy(d, s, n); d[n] = 0; return d; }

static void dispatch(Reply *r, const char *ev, int have_ev, SB *data, int ndata) {
    if (ndata == 0) return;
    J *o = jparse(data->p, data->n);
    if (!o || o->t != JOBJ) return;
    const char *t = have_ev ? ev : jgets(o, "type", NULL);
    if (!strcmp(t, "message_start")) {
        J *u = jget(jget(o, "message"), "usage");
        if (u) { uIn += jgetn(u, "input_tokens"); uCache += jgetn(u, "cache_read_input_tokens"); }
    } else if (!strcmp(t, "content_block_start")) {
        J *cb = jget(o, "content_block");
        if (!cb) return;
        long long idx = jgetn(o, "index");
        Block *b = find_block(r, idx);
        if (!b) {
            if (r->n == r->cap) { r->cap = r->cap ? r->cap * 2 : 8; r->b = realloc(r->b, r->cap * sizeof(Block)); }
            b = &r->b[r->n++];
        }
        memset(b, 0, sizeof *b);
        b->idx = idx;
        size_t l; const char *s = jgets(cb, "type", &l);
        snprintf(b->type, sizeof b->type, "%s", s);
        s = jgets(cb, "id", &l); b->id = dupn(s, l);
        s = jgets(cb, "name", &l); b->name = dupn(s, l);
        sb_init(&b->text); sb_init(&b->sig); sb_init(&b->json);
    } else if (!strcmp(t, "content_block_delta")) {
        Block *b = find_block(r, jgetn(o, "index"));
        J *d = jget(o, "delta");
        if (!b || !d) return;
        const char *dt = jgets(d, "type", NULL);
        size_t l; const char *s;
        if (!strcmp(dt, "thinking_delta")) { s = jgets(d, "thinking", &l); sb_addn(&b->text, s, l); }
        else if (!strcmp(dt, "signature_delta")) { s = jgets(d, "signature", &l); sb_addn(&b->sig, s, l); }
        else if (!strcmp(dt, "text_delta")) { s = jgets(d, "text", &l); sb_addn(&b->text, s, l); }
        else if (!strcmp(dt, "input_json_delta")) { s = jgets(d, "partial_json", &l); sb_addn(&b->json, s, l); }
    } else if (!strcmp(t, "message_delta")) {
        J *d = jget(o, "delta");
        J *sr = jget(d, "stop_reason");
        if (sr && sr->t == JSTR) r->stop = dupn(sr->s, sr->sl);
        J *u = jget(o, "usage");
        if (u) uOut += jgetn(u, "output_tokens");
    }
}

static int cmp_block(const void *a, const void *b) {
    long long x = ((const Block *)a)->idx, y = ((const Block *)b)->idx;
    return x < y ? -1 : x > y;
}

static void parse_sse(const char *body, size_t n, Reply *r) {
    memset(r, 0, sizeof *r);
    r->stop = "";
    SB ev; sb_init(&ev); int have_ev = 0;
    SB data; sb_init(&data); int ndata = 0;
    size_t p = 0;
    while (p < n) {
        size_t e = p; while (e < n && body[e] != '\n') e++;
        size_t le = e;
        if (le > p && body[le - 1] == '\r') le--;
        const char *line = body + p; size_t ll = le - p;
        p = e < n ? e + 1 : n;
        if (ll == 0) { dispatch(r, ev.p, have_ev, &data, ndata); have_ev = 0; data.n = 0; data.p[0] = 0; ndata = 0; continue; }
        if (line[0] == ':') continue;
        const char *col = memchr(line, ':', ll);
        size_t fl; const char *v; size_t vl;
        if (col) { fl = (size_t)(col - line); v = col + 1; vl = ll - fl - 1; if (vl && *v == ' ') { v++; vl--; } }
        else { fl = ll; v = ""; vl = 0; }
        if (fl == 5 && !memcmp(line, "event", 5)) { ev.n = 0; sb_addn(&ev, v, vl); have_ev = 1; }
        else if (fl == 4 && !memcmp(line, "data", 4)) { if (ndata) sb_addc(&data, '\n'); sb_addn(&data, v, vl); ndata++; }
    }
    dispatch(r, ev.p, have_ev, &data, ndata);
    qsort(r->b, r->n, sizeof(Block), cmp_block);
}

static void print_reply(Reply *r) {
    for (size_t i = 0; i < r->n; i++) {
        Block *b = &r->b[i];
        if (!strcmp(b->type, "thinking")) {
            sb_adds(&OUT, "thinking "); esc_add(&OUT, b->text.p, b->text.n); sb_addc(&OUT, '\n');
            sb_adds(&OUT, "signature "); esc_add(&OUT, b->sig.p, b->sig.n); sb_addc(&OUT, '\n');
        } else if (!strcmp(b->type, "text")) {
            sb_adds(&OUT, "text "); esc_add(&OUT, b->text.p, b->text.n); sb_addc(&OUT, '\n');
        } else if (!strcmp(b->type, "tool_use")) {
            sb_adds(&OUT, "tool_use "); esc_add(&OUT, b->id, strlen(b->id)); sb_addc(&OUT, ' ');
            esc_add(&OUT, b->name, strlen(b->name)); sb_addc(&OUT, ' ');
            esc_add(&OUT, b->json.p, b->json.n); sb_addc(&OUT, '\n');
        }
    }
    sb_adds(&OUT, "stop_reason "); esc_add(&OUT, r->stop, strlen(r->stop)); sb_addc(&OUT, '\n');
}

/* ---------- tools ---------- */
static int run_tool(const char *name, J *in, SB *res) {
    size_t tl; const char *text = jgets(in, "text", &tl);
    if (!strcmp(name, "measure")) {
        size_t ul; const char *unit = jgets(in, "unit", &ul);
        size_t cps = 0, four = 0;
        for (size_t i = 0; i < tl; i++) {
            unsigned char c = text[i];
            if ((c & 0xC0) != 0x80) cps++;
            if (c >= 0xF0) four++;
        }
        char t[64];
        if (!strcmp(unit, "code_points")) { snprintf(t, sizeof t, "%zu", cps); sb_adds(res, t); return 1; }
        if (!strcmp(unit, "utf8_bytes")) { snprintf(t, sizeof t, "%zu", tl); sb_adds(res, t); return 1; }
        if (!strcmp(unit, "utf16_units")) { snprintf(t, sizeof t, "%zu", cps + four); sb_adds(res, t); return 1; }
        sb_adds(res, "unsupported unit: "); sb_addn(res, unit, ul);
        return 0;
    }
    if (!strcmp(name, "reverse")) {
        size_t e = tl;
        while (e > 0) {
            size_t s = e - 1;
            while (s > 0 && ((unsigned char)text[s] & 0xC0) == 0x80) s--;
            sb_addn(res, text + s, e - s);
            e = s;
        }
        return 1;
    }
    sb_adds(res, "unknown tool: "); sb_adds(res, name);
    return 0;
}

static int handle_error(int status, SB *body) {
    if (status == 200) return 0;
    const char *et = "", *em = ""; size_t etl = 0, eml = 0;
    J *o = jparse(body->p, body->n);
    J *e = jget(o, "error");
    if (e) { et = jgets(e, "type", &etl); em = jgets(e, "message", &eml); }
    char t[32]; snprintf(t, sizeof t, "ERROR %d ", status);
    sb_adds(&OUT, t); esc_add(&OUT, et, etl); sb_adds(&OUT, ": "); esc_add(&OUT, em, eml); sb_addc(&OUT, '\n');
    return 1;
}

static J *req;
static J *mk_body(J *msgs) {
    J *b = jnew(JOBJ);
    J *nul = jnew(JNULL);
    J *v;
    jset(b, "model", (v = jget(req, "model")) ? v : nul);
    jset(b, "max_tokens", (v = jget(req, "max_tokens")) ? v : nul);
    J *tr = jnew(JBOOL); tr->b = 1;
    jset(b, "stream", tr);
    J *th = jnew(JOBJ);
    jset(th, "type", jcstr("enabled"));
    jset(th, "budget_tokens", (v = jget(req, "thinking_budget")) ? v : nul);
    jset(b, "thinking", th);
    jset(b, "system", (v = jget(req, "system")) ? v : nul);
    jset(b, "tools", (v = jget(req, "tools")) ? v : nul);
    jset(b, "messages", msgs);
    return b;
}

static void run(int argc, char **argv) {
    if (argc < 3) return;
    const char *base = argv[2];
    if (!strncmp(base, "http://", 7)) base += 7;
    const char *sl = strchr(base, '/');
    size_t hpl = sl ? (size_t)(sl - base) : strlen(base);
    char hp[300]; snprintf(hp, sizeof hp, "%.*s", (int)hpl, base);
    snprintf(prefix, sizeof prefix, "%s", sl ? sl : "");
    { size_t pl = strlen(prefix); while (pl && prefix[pl - 1] == '/') prefix[--pl] = 0; }
    char *colon = strrchr(hp, ':');
    if (colon) { *colon = 0; snprintf(port_s, sizeof port_s, "%s", colon + 1); } else snprintf(port_s, sizeof port_s, "80");
    snprintf(host, sizeof host, "%s", hp);

    char path[4096]; snprintf(path, sizeof path, "%s/request.json", argv[1]);
    FILE *f = fopen(path, "rb"); if (!f) return;
    SB rd; sb_init(&rd); char buf[8192]; size_t k;
    while ((k = fread(buf, 1, sizeof buf, f)) > 0) sb_addn(&rd, buf, k);
    fclose(f);
    req = jparse(rd.p, rd.n);
    if (!req) return;
    api_key = jgets(req, "api_key", NULL);

    J *user = jnew(JOBJ);
    jset(user, "role", jcstr("user"));
    J *pr = jget(req, "prompt");
    jset(user, "content", pr ? pr : jnew(JNULL));
    J *msgs = jnew(JARR);
    jpush(msgs, NULL, 0, user);

    sb_adds(&OUT, "request 1\n");
    SB body; sb_init(&body); jser(&body, mk_body(msgs));
    SB resp; sb_init(&resp);
    int st = http_post(body.p, body.n, &resp);
    if (handle_error(st, &resp)) return;
    Reply r1; parse_sse(resp.p, resp.n, &r1);
    print_reply(&r1);

    J *asst = jnew(JARR), *results = jnew(JARR);
    for (size_t i = 0; i < r1.n; i++) {
        Block *b = &r1.b[i];
        J *m = jnew(JOBJ);
        if (!strcmp(b->type, "thinking")) {
            jset(m, "type", jcstr("thinking"));
            jset(m, "thinking", jstr(b->text.p, b->text.n));
            jset(m, "signature", jstr(b->sig.p, b->sig.n));
            jpush(asst, NULL, 0, m);
        } else if (!strcmp(b->type, "text")) {
            jset(m, "type", jcstr("text"));
            jset(m, "text", jstr(b->text.p, b->text.n));
            jpush(asst, NULL, 0, m);
        } else if (!strcmp(b->type, "tool_use")) {
            J *input = jparse(b->json.p, b->json.n);
            if (!input) input = jnew(JOBJ);
            jset(m, "type", jcstr("tool_use"));
            jset(m, "id", jcstr(b->id));
            jset(m, "name", jcstr(b->name));
            jset(m, "input", input);
            jpush(asst, NULL, 0, m);
            SB res; sb_init(&res);
            int ok = run_tool(b->name, input, &res);
            sb_adds(&OUT, ok ? "result " : "error ");
            esc_add(&OUT, b->id, strlen(b->id)); sb_addc(&OUT, ' ');
            esc_add(&OUT, res.p, res.n); sb_addc(&OUT, '\n');
            J *tr = jnew(JOBJ);
            jset(tr, "type", jcstr("tool_result"));
            jset(tr, "tool_use_id", jcstr(b->id));
            jset(tr, "content", jstr(res.p, res.n));
            if (!ok) { J *t = jnew(JBOOL); t->b = 1; jset(tr, "is_error", t); }
            jpush(results, NULL, 0, tr);
        }
    }
    sb_adds(&OUT, "request 2\n");
    J *am = jnew(JOBJ); jset(am, "role", jcstr("assistant")); jset(am, "content", asst);
    J *um = jnew(JOBJ); jset(um, "role", jcstr("user")); jset(um, "content", results);
    jpush(msgs, NULL, 0, am); jpush(msgs, NULL, 0, um);
    body.n = 0; jser(&body, mk_body(msgs));
    st = http_post(body.p, body.n, &resp);
    if (handle_error(st, &resp)) return;
    Reply r2; parse_sse(resp.p, resp.n, &r2);
    print_reply(&r2);
    char t[160];
    snprintf(t, sizeof t, "usage input=%lld output=%lld cache_read=%lld\n", uIn, uOut, uCache);
    sb_adds(&OUT, t);
}

int main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    sb_init(&OUT);
    run(argc, argv);
    flush_out();
    return 0;
}
