#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>

/* ---------- dynamic buffer ---------- */
typedef struct { char *p; size_t n, cap; } Buf;
static void bput(Buf *b, const char *s, size_t n) {
    if (b->n + n + 1 > b->cap) {
        size_t c = b->cap ? b->cap : 256;
        while (c < b->n + n + 1) c *= 2;
        b->p = realloc(b->p, c); b->cap = c;
    }
    memcpy(b->p + b->n, s, n); b->n += n; b->p[b->n] = 0;
}
static void bs(Buf *b, const char *s) { bput(b, s, strlen(s)); }
static void bc(Buf *b, char c) { bput(b, &c, 1); }

/* ---------- JSON ---------- */
enum { JNULL, JBOOL, JNUM, JSTR, JARR, JOBJ };
typedef struct J J;
struct J {
    int t;
    int b;
    char *s; size_t slen;   /* number text or string bytes */
    J **items; char **keys; size_t *klens; size_t n, cap;
};

static J *jnew(int t) { J *j = calloc(1, sizeof(J)); j->t = t; return j; }
static void jpush(J *o, char *k, size_t kl, J *v) {
    if (o->n == o->cap) {
        o->cap = o->cap ? o->cap * 2 : 4;
        o->items = realloc(o->items, o->cap * sizeof(J *));
        o->keys = realloc(o->keys, o->cap * sizeof(char *));
        o->klens = realloc(o->klens, o->cap * sizeof(size_t));
    }
    o->items[o->n] = v; o->keys[o->n] = k; o->klens[o->n] = kl; o->n++;
}
static J *jstr(const char *s, size_t n) {
    J *j = jnew(JSTR); j->s = malloc(n + 1); memcpy(j->s, s, n); j->s[n] = 0; j->slen = n; return j;
}
static J *jget(J *o, const char *k) {
    if (!o || o->t != JOBJ) return NULL;
    size_t kl = strlen(k);
    for (size_t i = 0; i < o->n; i++)
        if (o->klens[i] == kl && memcmp(o->keys[i], k, kl) == 0) return o->items[i];
    return NULL;
}
static J *jgetn(J *o, const char *k, size_t kl) {
    if (!o || o->t != JOBJ) return NULL;
    for (size_t i = 0; i < o->n; i++)
        if (o->klens[i] == kl && memcmp(o->keys[i], k, kl) == 0) return o->items[i];
    return NULL;
}

typedef struct { const char *s; size_t i, n; } P;
static void ws(P *p) { while (p->i < p->n && (p->s[p->i] == ' ' || p->s[p->i] == '\t' || p->s[p->i] == '\n' || p->s[p->i] == '\r')) p->i++; }
static unsigned hex4(P *p) {
    unsigned v = 0;
    for (int k = 0; k < 4; k++) {
        char c = p->s[p->i++]; v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
    }
    return v;
}
static void utf8(Buf *b, unsigned cp) {
    char t[4];
    if (cp < 0x80) { t[0] = cp; bput(b, t, 1); }
    else if (cp < 0x800) { t[0] = 0xC0 | (cp >> 6); t[1] = 0x80 | (cp & 63); bput(b, t, 2); }
    else if (cp < 0x10000) { t[0] = 0xE0 | (cp >> 12); t[1] = 0x80 | ((cp >> 6) & 63); t[2] = 0x80 | (cp & 63); bput(b, t, 3); }
    else { t[0] = 0xF0 | (cp >> 18); t[1] = 0x80 | ((cp >> 12) & 63); t[2] = 0x80 | ((cp >> 6) & 63); t[3] = 0x80 | (cp & 63); bput(b, t, 4); }
}
static void pstr(P *p, Buf *b) {
    p->i++;
    while (p->i < p->n) {
        char c = p->s[p->i++];
        if (c == '"') return;
        if (c == '\\') {
            char e = p->s[p->i++];
            switch (e) {
                case 'b': bc(b, '\b'); break;
                case 'f': bc(b, '\f'); break;
                case 'n': bc(b, '\n'); break;
                case 'r': bc(b, '\r'); break;
                case 't': bc(b, '\t'); break;
                case 'u': {
                    unsigned cp = hex4(p);
                    if (cp >= 0xD800 && cp < 0xDC00 && p->i + 6 <= p->n && p->s[p->i] == '\\' && p->s[p->i + 1] == 'u') {
                        size_t save = p->i; p->i += 2;
                        unsigned lo = hex4(p);
                        if (lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        else p->i = save;
                    }
                    utf8(b, cp);
                    break;
                }
                default: bc(b, e);
            }
        } else bc(b, c);
    }
}
static J *pval(P *p) {
    ws(p);
    char c = p->s[p->i];
    if (c == '{') {
        J *o = jnew(JOBJ); p->i++; ws(p);
        if (p->s[p->i] == '}') { p->i++; return o; }
        for (;;) {
            ws(p);
            Buf k = {0}; bs(&k, ""); pstr(p, &k);
            ws(p); p->i++;
            J *v = pval(p);
            jpush(o, k.p, k.n, v);
            ws(p);
            char d = p->s[p->i++];
            if (d == '}') return o;
        }
    }
    if (c == '[') {
        J *o = jnew(JARR); p->i++; ws(p);
        if (p->s[p->i] == ']') { p->i++; return o; }
        for (;;) {
            jpush(o, NULL, 0, pval(p));
            ws(p);
            char d = p->s[p->i++];
            if (d == ']') return o;
        }
    }
    if (c == '"') {
        Buf b = {0}; bs(&b, ""); pstr(p, &b);
        J *j = jnew(JSTR); j->s = b.p; j->slen = b.n; return j;
    }
    if (c == 't') { p->i += 4; J *j = jnew(JBOOL); j->b = 1; return j; }
    if (c == 'f') { p->i += 5; J *j = jnew(JBOOL); j->b = 0; return j; }
    if (c == 'n') { p->i += 4; return jnew(JNULL); }
    size_t st = p->i;
    while (p->i < p->n && strchr("+-0123456789.eE", p->s[p->i]) && p->s[p->i]) p->i++;
    J *j = jnew(JNUM); j->slen = p->i - st; j->s = malloc(j->slen + 1); memcpy(j->s, p->s + st, j->slen); j->s[j->slen] = 0;
    return j;
}
static J *parse(const char *s, size_t n) { P p = { s, 0, n }; return pval(&p); }

static void esc(Buf *b, const char *s, size_t n) {
    bc(b, '"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = s[i];
        switch (c) {
            case '"': bs(b, "\\\""); break;
            case '\\': bs(b, "\\\\"); break;
            case '\b': bs(b, "\\b"); break;
            case '\f': bs(b, "\\f"); break;
            case '\n': bs(b, "\\n"); break;
            case '\r': bs(b, "\\r"); break;
            case '\t': bs(b, "\\t"); break;
            default:
                if (c < 0x20) { char t[8]; snprintf(t, sizeof t, "\\u%04x", c); bs(b, t); }
                else bc(b, c);
        }
    }
    bc(b, '"');
}
static void jwrite(Buf *b, J *v) {
    if (!v) { bs(b, "null"); return; }
    switch (v->t) {
        case JNULL: bs(b, "null"); break;
        case JBOOL: bs(b, v->b ? "true" : "false"); break;
        case JNUM: bput(b, v->s, v->slen); break;
        case JSTR: esc(b, v->s, v->slen); break;
        case JARR:
            bc(b, '[');
            for (size_t i = 0; i < v->n; i++) { if (i) bc(b, ','); jwrite(b, v->items[i]); }
            bc(b, ']'); break;
        case JOBJ:
            bc(b, '{');
            for (size_t i = 0; i < v->n; i++) {
                if (i) bc(b, ',');
                esc(b, v->keys[i], v->klens[i]); bc(b, ':'); jwrite(b, v->items[i]);
            }
            bc(b, '}'); break;
    }
}
static char *dupk(const char *s) { return strdup(s); }
static void oset(J *o, const char *k, J *v) { jpush(o, dupk(k), strlen(k), v); }

typedef __int128 i128;
static i128 toint(J *v) {
    if (!v || v->t != JNUM) return 0;
    i128 x = 0; int neg = 0; size_t i = 0;
    if (v->s[0] == '-') { neg = 1; i = 1; }
    for (; i < v->slen && isdigit((unsigned char)v->s[i]); i++) x = x * 10 + (v->s[i] - '0');
    return neg ? -x : x;
}
static void i128str(Buf *b, i128 x) {
    char t[64]; int k = 63; t[k] = 0;
    int neg = x < 0; if (neg) x = -x;
    if (x == 0) t[--k] = '0';
    while (x > 0) { t[--k] = '0' + (int)(x % 10); x /= 10; }
    if (neg) t[--k] = '-';
    bs(b, t + k);
}

/* ---------- files ---------- */
static char *readfile(const char *dir, const char *name, size_t *len) {
    char path[4096]; snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "rb");
    if (!f) { *len = 0; return calloc(1, 1); }
    Buf b = {0}; bs(&b, "");
    char t[65536]; size_t r;
    while ((r = fread(t, 1, sizeof t, f)) > 0) bput(&b, t, r);
    fclose(f);
    *len = b.n; return b.p;
}

/* ---------- CSV ---------- */
typedef struct { Buf f[5]; int nf; } Row;
static Row *rows; static size_t nrows;

static void csv(const char *s, size_t n) {
    size_t i = 0;
    if (n >= 3 && (unsigned char)s[0] == 0xEF && (unsigned char)s[1] == 0xBB && (unsigned char)s[2] == 0xBF) i = 3;
    size_t cap = 0;
    while (i < n) {
        Row r; memset(&r, 0, sizeof r);
        for (;;) {
            Buf f = {0}; bs(&f, "");
            if (i < n && s[i] == '"') {
                i++;
                while (i < n) {
                    if (s[i] == '"') {
                        if (i + 1 < n && s[i + 1] == '"') { bc(&f, '"'); i += 2; }
                        else { i++; break; }
                    } else { bc(&f, s[i]); i++; }
                }
            }
            while (i < n && s[i] != ',' && s[i] != '\r' && s[i] != '\n') { bc(&f, s[i]); i++; }
            if (r.nf < 5) r.f[r.nf] = f;
            r.nf++;
            if (i < n && s[i] == ',') { i++; continue; }
            if (i < n && s[i] == '\r') i++;
            if (i < n && s[i] == '\n') i++;
            break;
        }
        if (nrows == cap) { cap = cap ? cap * 2 : 64; rows = realloc(rows, cap * sizeof(Row)); }
        rows[nrows++] = r;
    }
}

static void normint(Buf *b, Buf *f) {
    const char *s = f->p; size_t n = f->n, i = 0;
    while (i < n && (s[i] == ' ')) i++;
    while (n > i && s[n - 1] == ' ') n--;
    while (i + 1 < n && s[i] == '0') i++;
    if (i >= n) bs(b, "0"); else bput(b, s + i, n - i);
}

/* ---------- HTTP ---------- */
static int post(const char *host, const char *port, const char *hosthdr, const char *path, const char *key, Buf *body, Buf *resp) {
    struct addrinfo hints, *res, *ai;
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, port, &hints, &res) != 0) return 0;
    int fd = -1;
    for (ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return 0;
    Buf req = {0}; bs(&req, "");
    char t[512];
    bs(&req, "POST "); bs(&req, path); bs(&req, " HTTP/1.1\r\nHost: "); bs(&req, hosthdr);
    bs(&req, "\r\nAuthorization: Bearer "); bs(&req, key);
    snprintf(t, sizeof t, "\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", body->n);
    bs(&req, t);
    bput(&req, body->p, body->n);
    size_t off = 0;
    while (off < req.n) { ssize_t w = write(fd, req.p + off, req.n - off); if (w <= 0) break; off += w; }
    free(req.p);
    Buf all = {0}; bs(&all, "");
    char rb[65536];
    for (;;) { ssize_t r = read(fd, rb, sizeof rb); if (r <= 0) break; bput(&all, rb, r); }
    close(fd);
    char *sep = strstr(all.p, "\r\n\r\n");
    if (!sep) return 0;
    size_t hl = sep - all.p;
    int status = 0;
    char *sp = strchr(all.p, ' ');
    if (sp) status = atoi(sp + 1);
    long cl = -1;
    char *line = all.p;
    while (line < all.p + hl) {
        char *eol = strstr(line, "\r\n");
        if (!eol || eol > all.p + hl) eol = all.p + hl;
        if ((size_t)(eol - line) > 15 && strncasecmp(line, "content-length:", 15) == 0) cl = atol(line + 15);
        line = eol + 2;
    }
    size_t start = hl + 4;
    size_t end = all.n;
    if (cl >= 0 && start + (size_t)cl < end) end = start + cl;
    bs(resp, ""); bput(resp, all.p + start, end - start);
    free(all.p);
    return status;
}

int main(int argc, char **argv) {
    if (argc < 3) return 0;
    const char *dir = argv[1], *base = argv[2];
    const char *rest = strstr(base, "://"); rest = rest ? rest + 3 : base;
    const char *sl = strchr(rest, '/');
    size_t hpl = sl ? (size_t)(sl - rest) : strlen(rest);
    char hp[512]; memcpy(hp, rest, hpl); hp[hpl] = 0;
    char prefix[2048]; snprintf(prefix, sizeof prefix, "%s/chat/completions", sl ? sl : "");
    char host[512], port[32];
    char *co = strrchr(hp, ':');
    if (co) { memcpy(host, hp, co - hp); host[co - hp] = 0; snprintf(port, sizeof port, "%s", co + 1); }
    else { snprintf(host, sizeof host, "%s", hp); strcpy(port, "80"); }

    size_t n;
    char *ts = readfile(dir, "task.json", &n); J *task = parse(ts, n);
    char *rs = readfile(dir, "rates.json", &n); J *rates = jget(parse(rs, n), "rates");
    char *cs = readfile(dir, "inventory.csv", &n); csv(cs, n);

    J *key = jget(task, "api_key");
    J *messages = jnew(JARR);
    J *m0 = jnew(JOBJ); oset(m0, "role", jstr("system", 6)); oset(m0, "content", jget(task, "system")); jpush(messages, NULL, 0, m0);
    J *m1 = jnew(JOBJ); oset(m1, "role", jstr("user", 4)); oset(m1, "content", jget(task, "user")); jpush(messages, NULL, 0, m1);
    J *req = jnew(JOBJ);
    oset(req, "model", jget(task, "model"));
    oset(req, "messages", messages);
    oset(req, "tools", jget(task, "tools"));
    oset(req, "tool_choice", jstr("auto", 4));

    Buf out = {0}; bs(&out, "");
    i128 pt = 0, ct = 0, tt = 0; long requests = 0;
    for (;;) {
        Buf body = {0}; bs(&body, ""); jwrite(&body, req);
        Buf resp = {0};
        int status = post(host, port, hp, prefix, key && key->t == JSTR ? key->s : "", &body, &resp);
        free(body.p);
        requests++;
        if (status != 200) {
            J *e = parse(resp.p, resp.n);
            J *m = jget(jget(e, "error"), "message");
            char t[32]; snprintf(t, sizeof t, "ERROR %d ", status); bs(&out, t);
            if (m && m->t == JSTR) bput(&out, m->s, m->slen);
            bs(&out, "\n");
            fwrite(out.p, 1, out.n, stdout);
            return 0;
        }
        J *r = parse(resp.p, resp.n);
        J *u = jget(r, "usage");
        if (u && u->t == JOBJ) { pt += toint(jget(u, "prompt_tokens")); ct += toint(jget(u, "completion_tokens")); tt += toint(jget(u, "total_tokens")); }
        J *choices = jget(r, "choices");
        J *choice = (choices && choices->t == JARR && choices->n) ? choices->items[0] : NULL;
        J *msg = jget(choice, "message");
        J *fr = jget(choice, "finish_reason");
        if (fr && fr->t == JSTR && strcmp(fr->s, "tool_calls") == 0) {
            jpush(messages, NULL, 0, msg);
            J *content = jget(msg, "content");
            if (content && content->t == JSTR) { bs(&out, "SAY "); bput(&out, content->s, content->slen); bs(&out, "\n"); }
            J *calls = jget(msg, "tool_calls");
            size_t nc = (calls && calls->t == JARR) ? calls->n : 0;
            Buf *results = calloc(nc + 1, sizeof(Buf));
            for (size_t k = 0; k < nc; k++) {
                J *call = calls->items[k];
                J *id = jget(call, "id");
                J *fn = jget(call, "function");
                J *name = jget(fn, "name");
                J *args = jget(fn, "arguments");
                const char *ids = id && id->t == JSTR ? id->s : "";
                const char *nm = name && name->t == JSTR ? name->s : "";
                bs(&out, "CALL "); bs(&out, ids); bs(&out, " "); bs(&out, nm); bs(&out, "\n");
                Buf *b = &results[k]; bs(b, "");
                if (strcmp(nm, "inventory_lookup") == 0) {
                    J *a = parse(args->s, args->slen);
                    J *sku = jget(a, "sku");
                    Row *found = NULL;
                    for (size_t q = 1; q < nrows; q++)
                        if (rows[q].nf >= 5 && rows[q].f[0].n == sku->slen && memcmp(rows[q].f[0].p, sku->s, sku->slen) == 0) { found = &rows[q]; break; }
                    if (!found) {
                        Buf m = {0}; bs(&m, "unknown sku: "); bput(&m, sku->s, sku->slen);
                        bs(b, "{\"error\":"); esc(b, m.p, m.n); bs(b, "}");
                    } else {
                        bs(b, "{\"sku\":"); esc(b, found->f[0].p, found->f[0].n);
                        bs(b, ",\"name\":"); esc(b, found->f[1].p, found->f[1].n);
                        bs(b, ",\"price_cents\":"); normint(b, &found->f[2]);
                        bs(b, ",\"currency\":"); esc(b, found->f[3].p, found->f[3].n);
                        bs(b, ",\"stock\":"); normint(b, &found->f[4]);
                        bs(b, "}");
                    }
                } else if (strcmp(nm, "convert_currency") == 0) {
                    J *a = parse(args->s, args->slen);
                    i128 amt = toint(jget(a, "amount_cents"));
                    J *from = jget(a, "from"), *to = jget(a, "to");
                    i128 rf = toint(jgetn(rates, from->s, from->slen)), rt = toint(jgetn(rates, to->s, to->slen));
                    i128 conv = rf ? (2 * amt * rt + rf) / (2 * rf) : 0;
                    bs(b, "{\"amount_cents\":"); i128str(b, conv);
                    bs(b, ",\"currency\":"); esc(b, to->s, to->slen); bs(b, "}");
                } else {
                    Buf m = {0}; bs(&m, "unknown tool: "); bput(&m, name ? name->s : "", name ? name->slen : 0);
                    bs(b, "{\"error\":"); esc(b, m.p, m.n); bs(b, "}");
                }
                J *tm = jnew(JOBJ);
                oset(tm, "role", jstr("tool", 4));
                oset(tm, "tool_call_id", id);
                oset(tm, "content", jstr(b->p, b->n));
                jpush(messages, NULL, 0, tm);
            }
            for (size_t k = 0; k < nc; k++) {
                J *id = jget(calls->items[k], "id");
                bs(&out, "RESULT "); if (id && id->t == JSTR) bput(&out, id->s, id->slen);
                bs(&out, " "); bput(&out, results[k].p, results[k].n); bs(&out, "\n");
            }
        } else {
            J *content = jget(msg, "content");
            bs(&out, "FINAL ");
            if (content && content->t == JSTR) bput(&out, content->s, content->slen);
            bs(&out, "\n");
            break;
        }
    }
    bs(&out, "USAGE prompt="); i128str(&out, pt);
    bs(&out, " completion="); i128str(&out, ct);
    bs(&out, " total="); i128str(&out, tt);
    char t[64]; snprintf(t, sizeof t, " requests=%ld\n", requests); bs(&out, t);
    fwrite(out.p, 1, out.n, stdout);
    return 0;
}
