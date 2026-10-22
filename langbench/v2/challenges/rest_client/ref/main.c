#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <unistd.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>

/* ---------- buffers ---------- */
typedef struct { char *p; size_t n, cap; } Buf;
static void bput(Buf *b, const void *d, size_t n) {
    if (b->n + n + 1 > b->cap) {
        b->cap = (b->n + n + 1) * 2;
        b->p = realloc(b->p, b->cap);
    }
    memcpy(b->p + b->n, d, n);
    b->n += n;
    b->p[b->n] = 0;
}
static void bstr(Buf *b, const char *s) { bput(b, s, strlen(s)); }

/* ---------- JSON ---------- */
enum { JNULL, JBOOL, JNUM, JSTR, JARR, JOBJ };
typedef struct J {
    int t;
    char *s; /* string / number text */
    int n;
    char **keys;
    struct J **vals;
} J;

typedef struct { const char *b; size_t i, n; } P;
static void ws(P *p) { while (p->i < p->n && strchr(" \t\r\n", p->b[p->i]) && p->b[p->i]) p->i++; }
static unsigned hex4(P *p) {
    unsigned v = 0;
    for (int k = 0; k < 4; k++) {
        char c = p->b[p->i++];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
    }
    return v;
}
static void pututf8(Buf *b, unsigned cp) {
    char o[4];
    if (cp < 0x80) { o[0] = cp; bput(b, o, 1); }
    else if (cp < 0x800) { o[0] = 0xC0 | (cp >> 6); o[1] = 0x80 | (cp & 63); bput(b, o, 2); }
    else if (cp < 0x10000) { o[0] = 0xE0 | (cp >> 12); o[1] = 0x80 | ((cp >> 6) & 63); o[2] = 0x80 | (cp & 63); bput(b, o, 3); }
    else { o[0] = 0xF0 | (cp >> 18); o[1] = 0x80 | ((cp >> 12) & 63); o[2] = 0x80 | ((cp >> 6) & 63); o[3] = 0x80 | (cp & 63); bput(b, o, 4); }
}
static char *pstr(P *p) {
    Buf b = {0};
    bput(&b, "", 0);
    p->i++;
    while (p->i < p->n) {
        char c = p->b[p->i++];
        if (c == '"') break;
        if (c == '\\') {
            char e = p->b[p->i++];
            switch (e) {
            case 'n': bput(&b, "\n", 1); break;
            case 't': bput(&b, "\t", 1); break;
            case 'r': bput(&b, "\r", 1); break;
            case 'b': bput(&b, "\b", 1); break;
            case 'f': bput(&b, "\f", 1); break;
            case 'u': {
                unsigned cp = hex4(p);
                if (cp >= 0xD800 && cp < 0xDC00 && p->i + 1 < p->n && p->b[p->i] == '\\' && p->b[p->i + 1] == 'u') {
                    size_t save = p->i;
                    p->i += 2;
                    unsigned lo = hex4(p);
                    if (lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    else p->i = save;
                }
                pututf8(&b, cp);
                break;
            }
            default: bput(&b, &e, 1);
            }
        } else bput(&b, &c, 1);
    }
    return b.p;
}
static J *pval(P *p) {
    ws(p);
    J *j = calloc(1, sizeof(J));
    char c = p->b[p->i];
    if (c == '{' || c == '[') {
        int obj = c == '{';
        j->t = obj ? JOBJ : JARR;
        p->i++;
        ws(p);
        if (p->b[p->i] == (obj ? '}' : ']')) { p->i++; return j; }
        int cap = 0;
        for (;;) {
            if (j->n == cap) {
                cap = cap ? cap * 2 : 8;
                j->vals = realloc(j->vals, cap * sizeof(J *));
                j->keys = realloc(j->keys, cap * sizeof(char *));
            }
            if (obj) {
                ws(p);
                j->keys[j->n] = pstr(p);
                ws(p);
                p->i++; /* : */
            } else j->keys[j->n] = NULL;
            j->vals[j->n++] = pval(p);
            ws(p);
            char d = p->b[p->i++];
            if (d != ',') break;
        }
        return j;
    }
    if (c == '"') { j->t = JSTR; j->s = pstr(p); return j; }
    if (c == 't') { j->t = JBOOL; p->i += 4; return j; }
    if (c == 'f') { j->t = JBOOL; p->i += 5; return j; }
    if (c == 'n') { j->t = JNULL; p->i += 4; return j; }
    size_t st = p->i;
    while (p->i < p->n && strchr("+-0123456789.eE", p->b[p->i]) && p->b[p->i]) p->i++;
    j->t = JNUM;
    j->s = strndup(p->b + st, p->i - st);
    return j;
}
static J *jparse(const char *s, size_t n) { P p = {s, 0, n}; return pval(&p); }
static J *jget(J *j, const char *k) {
    if (!j || j->t != JOBJ) return NULL;
    for (int i = 0; i < j->n; i++) if (!strcmp(j->keys[i], k)) return j->vals[i];
    return NULL;
}
static const char *jstr(J *j) { return (j && (j->t == JSTR || j->t == JNUM)) ? j->s : ""; }

/* ---------- HTTP ---------- */
static char g_host[256];
static char g_port[16];

typedef struct {
    int status;
    char *headers; /* raw header block, lowercase names not guaranteed */
    Buf body;
} Resp;

static char *memfind(const char *a, size_t n, const char *pat, size_t from) {
    size_t m = strlen(pat);
    for (size_t i = from; i + m <= n; i++) if (!memcmp(a + i, pat, m)) return (char *)a + i;
    return NULL;
}

static int hdr_get(const char *head, const char *name, char *out, size_t outsz) {
    const char *l = strstr(head, "\r\n");
    size_t nl = strlen(name);
    while (l) {
        l += 2;
        const char *e = strstr(l, "\r\n");
        size_t len = e ? (size_t)(e - l) : strlen(l);
        if (len > nl && !strncasecmp(l, name, nl) && l[nl] == ':') {
            const char *v = l + nl + 1;
            while (*v == ' ' || *v == '\t') v++;
            size_t vl = len - (v - l);
            while (vl && (v[vl - 1] == ' ' || v[vl - 1] == '\t')) vl--;
            if (vl >= outsz) vl = outsz - 1;
            memcpy(out, v, vl);
            out[vl] = 0;
            return 1;
        }
        l = e;
    }
    return 0;
}

/* returns 1 if complete (or eof), fills r */
static int try_parse(const char *a, size_t n, int eof, Resp *r) {
    char *he = memfind(a, n, "\r\n\r\n", 0);
    if (!he) return 0;
    size_t hl = he - a;
    free(r->headers);
    r->headers = strndup(a, hl);
    r->status = 0;
    const char *sp = strchr(r->headers, ' ');
    if (sp) r->status = atoi(sp + 1);
    free(r->body.p);
    memset(&r->body, 0, sizeof(Buf));
    bput(&r->body, "", 0);
    size_t p = hl + 4;
    char v[256];
    if (hdr_get(r->headers, "transfer-encoding", v, sizeof v)) {
        for (char *q = v; *q; q++) *q = tolower((unsigned char)*q);
        if (strstr(v, "chunked")) {
            for (;;) {
                char *le = memfind(a, n, "\r\n", p);
                if (!le) return eof;
                size_t sz = strtoul(a + p, NULL, 16);
                p = le - a + 2;
                if (sz == 0) {
                    for (;;) {
                        char *t = memfind(a, n, "\r\n", p);
                        if (!t) return eof;
                        if ((size_t)(t - a) == p) return 1;
                        p = t - a + 2;
                    }
                }
                if (p + sz > n) {
                    if (!eof) return 0;
                    bput(&r->body, a + p, n - p);
                    return 1;
                }
                bput(&r->body, a + p, sz);
                p += sz;
                if (p + 2 > n) return eof;
                p += 2;
            }
        }
    }
    if (hdr_get(r->headers, "content-length", v, sizeof v)) {
        size_t cl = strtoul(v, NULL, 10);
        if (n - p < cl) {
            if (!eof) return 0;
            cl = n - p;
        }
        bput(&r->body, a + p, cl);
        return 1;
    }
    if (!eof) return 0;
    bput(&r->body, a + p, n - p);
    return 1;
}

static Resp request(const char *method, const char *path, const char *hdrs, const char *body) {
    struct addrinfo hints = {0}, *res;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(g_host, g_port, &hints, &res) != 0) { fprintf(stderr, "resolve\n"); exit(1); }
    int fd = -1;
    for (struct addrinfo *ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) { fprintf(stderr, "connect\n"); exit(1); }
    Buf q = {0};
    bstr(&q, method); bstr(&q, " "); bstr(&q, path); bstr(&q, " HTTP/1.1\r\nHost: ");
    bstr(&q, g_host); bstr(&q, ":"); bstr(&q, g_port); bstr(&q, "\r\n");
    bstr(&q, hdrs);
    if (body) {
        char cl[64];
        snprintf(cl, sizeof cl, "Content-Length: %zu\r\n", strlen(body));
        bstr(&q, cl);
    }
    bstr(&q, "Connection: close\r\n\r\n");
    if (body) bstr(&q, body);
    size_t off = 0;
    while (off < q.n) {
        ssize_t w = write(fd, q.p + off, q.n - off);
        if (w <= 0) break;
        off += w;
    }
    free(q.p);
    Buf d = {0};
    Resp r = {0};
    char buf[65536];
    int done = 0;
    for (;;) {
        ssize_t k = read(fd, buf, sizeof buf);
        if (k <= 0) break;
        bput(&d, buf, k);
        if (try_parse(d.p, d.n, 0, &r)) { done = 1; break; }
    }
    if (!done) try_parse(d.p ? d.p : "", d.n, 1, &r);
    close(fd);
    free(d.p);
    if (!r.body.p) bput(&r.body, "", 0);
    return r;
}

/* ---------- encoding ---------- */
static void form_enc(Buf *o, const char *s) {
    for (const unsigned char *c = (const unsigned char *)s; *c; c++) {
        if (isalnum(*c) && *c < 128) bput(o, c, 1);
        else if (*c == '-' || *c == '.' || *c == '_' || *c == '*') bput(o, c, 1);
        else if (*c == ' ') bput(o, "+", 1);
        else { char h[4]; snprintf(h, sizeof h, "%%%02X", *c); bput(o, h, 3); }
    }
}
static void pct_enc(Buf *o, const char *s) {
    for (const unsigned char *c = (const unsigned char *)s; *c; c++) {
        if ((isalnum(*c) && *c < 128) || *c == '-' || *c == '.' || *c == '_' || *c == '~') bput(o, c, 1);
        else { char h[4]; snprintf(h, sizeof h, "%%%02X", *c); bput(o, h, 3); }
    }
}
static void b64(Buf *o, const unsigned char *d, size_t n) {
    static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (size_t i = 0; i < n; i += 3) {
        unsigned b0 = d[i], b1 = i + 1 < n ? d[i + 1] : 0, b2 = i + 2 < n ? d[i + 2] : 0;
        unsigned v = (b0 << 16) | (b1 << 8) | b2;
        char q[4];
        q[0] = T[(v >> 18) & 63];
        q[1] = T[(v >> 12) & 63];
        q[2] = i + 1 < n ? T[(v >> 6) & 63] : '=';
        q[3] = i + 2 < n ? T[v & 63] : '=';
        bput(o, q, 4);
    }
}

/* ---------- state ---------- */
static const char *cid, *csec, *cscope;
static long long page_size;
static char *base_path;
static Buf out;

static void fatal(Resp *r) {
    printf("ERROR %d %s\n", r->status, r->body.p);
    fflush(stdout);
    exit(0);
}

static char *get_token(void) {
    Buf raw = {0}, hb = {0}, body = {0};
    form_enc(&raw, cid);
    bput(&raw, ":", 1);
    form_enc(&raw, csec);
    bstr(&hb, "Authorization: Basic ");
    b64(&hb, (unsigned char *)raw.p, raw.n);
    bstr(&hb, "\r\nContent-Type: application/x-www-form-urlencoded\r\nAccept: application/json\r\n");
    bstr(&body, "grant_type=client_credentials&scope=");
    form_enc(&body, cscope);
    Buf path = {0};
    bstr(&path, base_path);
    bstr(&path, "/oauth/token");
    Resp r = request("POST", path.p, hb.p, body.p);
    if (r.status != 200) fatal(&r);
    J *j = jparse(r.body.p, r.body.n);
    return strdup(jstr(jget(j, "access_token")));
}

typedef struct { char *name, *cur; long long sum; } Tot;
static Tot *tots;
static int ntots, captots;
typedef struct { char *s; long long c; } St;
static St *sts;
static int nsts, capsts;

static long long cents(const char *a) {
    int neg = 0;
    if (*a == '-') { neg = 1; a++; }
    long long ip = 0, fp = 0;
    while (*a >= '0' && *a <= '9') ip = ip * 10 + (*a++ - '0');
    if (*a == '.') {
        a++;
        int k = 0;
        while (*a >= '0' && *a <= '9' && k < 2) { fp = fp * 10 + (*a++ - '0'); k++; }
        if (k == 1) fp *= 10;
    }
    long long v = ip * 100 + fp;
    return neg ? -v : v;
}

static int cmp_tot(const void *x, const void *y) {
    const Tot *a = x, *b = y;
    int c = strcmp(a->name, b->name);
    if (c) return c;
    return strcmp(a->cur, b->cur);
}
static int cmp_st(const void *x, const void *y) { return strcmp(((const St *)x)->s, ((const St *)y)->s); }

int main(int argc, char **argv) {
    if (argc < 3) return 0;
    char fn[4096];
    snprintf(fn, sizeof fn, "%s/client.json", argv[1]);
    FILE *f = fopen(fn, "rb");
    Buf cfg = {0};
    char tmp[4096];
    size_t k;
    while ((k = fread(tmp, 1, sizeof tmp, f)) > 0) bput(&cfg, tmp, k);
    fclose(f);
    J *cj = jparse(cfg.p, cfg.n);
    cid = jstr(jget(cj, "client_id"));
    csec = jstr(jget(cj, "client_secret"));
    cscope = jstr(jget(cj, "scope"));
    page_size = (long long)strtod(jstr(jget(cj, "page_size")), NULL);

    const char *u = argv[2];
    if (!strncmp(u, "http://", 7)) u += 7;
    const char *sl = strchr(u, '/');
    size_t hpl = sl ? (size_t)(sl - u) : strlen(u);
    char hp[300];
    snprintf(hp, sizeof hp, "%.*s", (int)hpl, u);
    char *colon = strrchr(hp, ':');
    if (colon) {
        *colon = 0;
        snprintf(g_port, sizeof g_port, "%s", colon + 1);
    } else snprintf(g_port, sizeof g_port, "80");
    snprintf(g_host, sizeof g_host, "%s", hp);
    base_path = strdup(sl ? sl : "");
    size_t bl = strlen(base_path);
    if (bl && base_path[bl - 1] == '/') base_path[bl - 1] = 0;

    long long n = 0, pages = 0, retries = 0, reauth = 0;
    char *token = get_token();
    int fresh = 1;
    char *cursor = NULL;
    for (;;) {
        Buf path = {0};
        char lim[64];
        snprintf(lim, sizeof lim, "/orders?limit=%lld", page_size);
        bstr(&path, base_path);
        bstr(&path, lim);
        if (cursor) {
            bstr(&path, "&cursor=");
            pct_enc(&path, cursor);
        }
        Buf hb = {0};
        bstr(&hb, "Authorization: Bearer ");
        bstr(&hb, token);
        bstr(&hb, "\r\nAccept: application/json\r\n");
        Resp r = request("GET", path.p, hb.p, NULL);
        free(path.p);
        free(hb.p);
        int was_fresh = fresh;
        fresh = 0;
        if (r.status == 200) {
            pages++;
            J *m = jparse(r.body.p, r.body.n);
            J *data = jget(m, "data");
            if (data && data->t == JARR) {
                for (int i = 0; i < data->n; i++) {
                    J *o = data->vals[i];
                    n++;
                    const char *st = jstr(jget(o, "status"));
                    int si;
                    for (si = 0; si < nsts; si++) if (!strcmp(sts[si].s, st)) break;
                    if (si == nsts) {
                        if (nsts == capsts) { capsts = capsts ? capsts * 2 : 16; sts = realloc(sts, capsts * sizeof(St)); }
                        sts[nsts].s = strdup(st);
                        sts[nsts].c = 0;
                        nsts++;
                    }
                    sts[si].c++;
                    if (strcmp(st, "cancelled")) {
                        const char *name = jstr(jget(jget(o, "customer"), "name"));
                        const char *cur = jstr(jget(o, "currency"));
                        int ti;
                        for (ti = 0; ti < ntots; ti++) if (!strcmp(tots[ti].name, name) && !strcmp(tots[ti].cur, cur)) break;
                        if (ti == ntots) {
                            if (ntots == captots) { captots = captots ? captots * 2 : 16; tots = realloc(tots, captots * sizeof(Tot)); }
                            tots[ntots].name = strdup(name);
                            tots[ntots].cur = strdup(cur);
                            tots[ntots].sum = 0;
                            ntots++;
                        }
                        tots[ti].sum += cents(jstr(jget(o, "amount")));
                    }
                }
            }
            J *nc = jget(m, "next_cursor");
            if (!nc || nc->t != JSTR) break;
            cursor = strdup(nc->s);
        } else if (r.status == 429) {
            retries++;
            char v[64];
            long secs = 1;
            if (hdr_get(r.headers, "retry-after", v, sizeof v)) secs = strtol(v, NULL, 10);
            usleep((useconds_t)(secs * 1000000 + 50000));
        } else if (r.status == 503) {
            retries++;
            usleep(100000);
        } else if (r.status == 401 && !was_fresh) {
            token = get_token();
            reauth++;
            fresh = 1;
        } else {
            fatal(&r);
        }
    }
    qsort(tots, ntots, sizeof(Tot), cmp_tot);
    qsort(sts, nsts, sizeof(St), cmp_st);
    for (int i = 0; i < ntots; i++) {
        long long c = tots[i].sum;
        unsigned long long a = c < 0 ? (unsigned long long)(-c) : (unsigned long long)c;
        printf("total %s %s%llu.%02llu %s\n", tots[i].cur, c < 0 ? "-" : "", a / 100, a % 100, tots[i].name);
    }
    for (int i = 0; i < nsts; i++) printf("status %s %lld\n", sts[i].s, sts[i].c);
    printf("orders %lld pages %lld retries %lld reauth %lld\n", n, pages, retries, reauth);
    return 0;
}
