#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <ctype.h>

enum { INT32, INT64, UINT32, UINT64, SINT32, SINT64, BOOL_, STRING, BYTES, FIXED32, FIXED64, SFIXED32, SFIXED64, MSG, ENUM };
static const char *SCALAR_NAMES[] = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool", "string",
                                     "bytes", "fixed32", "fixed64", "sfixed32", "sfixed64"};
enum { SINGLE, OPTIONAL, REPEATED, MAP };

typedef struct { int kind; int ref; char *name; } Ty;

typedef struct {
    char *json;
    uint64_t num;
    Ty ty, keyTy;
    int label;
    int oneof;
} Field;

typedef struct {
    char *name;
    Field *fields;
    int nfields, capf;
    int oneofCount;
} MsgDesc;

typedef struct { int64_t num; char *name; } EnumVal;
typedef struct {
    char *name;
    EnumVal *vals;
    int nvals, cap;
} EnumDesc;

static MsgDesc *msgs; static int nmsgs, capmsgs;
static EnumDesc *enums; static int nenums, capenums;
static int root = -1;

/* ---------- tokenizer ---------- */
static char **toks; static int ntoks, captoks, tpos;

static void addtok(const char *s, size_t n) {
    if (ntoks == captoks) { captoks = captoks ? captoks * 2 : 256; toks = realloc(toks, captoks * sizeof(char *)); }
    char *t = malloc(n + 1); memcpy(t, s, n); t[n] = 0;
    toks[ntoks++] = t;
}

static int isword(unsigned char c) { return isalnum(c) || c == '_' || c == '.' || c == '+' || c == '-'; }

static void tokenize(const char *src, size_t n) {
    size_t i = 0;
    while (i < n) {
        unsigned char c = src[i];
        if (isspace(c)) { i++; continue; }
        if (c == '/' && i + 1 < n && src[i + 1] == '/') { while (i < n && src[i] != '\n') i++; continue; }
        if (c == '/' && i + 1 < n && src[i + 1] == '*') {
            size_t j = i + 2;
            while (j + 1 < n && !(src[j] == '*' && src[j + 1] == '/')) j++;
            i = (j + 1 < n) ? j + 2 : n;
            continue;
        }
        if (c == '"' || c == '\'') {
            size_t j = i + 1;
            while (j < n && (unsigned char)src[j] != c) { if (src[j] == '\\') j++; j++; }
            size_t e = j + 1 < n ? j + 1 : n;
            addtok(src + i, e - i);
            i = e;
            continue;
        }
        if (isword(c) || c >= 0x80) {
            size_t j = i;
            while (j < n && (isword((unsigned char)src[j]) || (unsigned char)src[j] >= 0x80)) j++;
            addtok(src + i, j - i);
            i = j;
            continue;
        }
        addtok(src + i, 1);
        i++;
    }
}

static const char *peek(void) { return tpos < ntoks ? toks[tpos] : ""; }
static const char *next(void) { return tpos < ntoks ? toks[tpos++] : ""; }
static int more(void) { return tpos < ntoks; }
static int eq(const char *a, const char *b) { return strcmp(a, b) == 0; }

static void skipStatement(void) {
    int depth = 0;
    while (more()) {
        const char *t = next();
        if (eq(t, "{") || eq(t, "[") || eq(t, "(")) depth++;
        else if (eq(t, "}") || eq(t, "]") || eq(t, ")")) depth--;
        else if (eq(t, ";") && depth <= 0) return;
    }
}

static void skipFieldOptions(void) {
    if (eq(peek(), "[")) {
        int depth = 0;
        while (more()) {
            const char *t = next();
            if (eq(t, "[")) depth++;
            else if (eq(t, "]")) { depth--; if (depth == 0) break; }
        }
    }
}

static int64_t parseInt(const char *s) {
    int neg = 0;
    if (*s == '-') { neg = 1; s++; } else if (*s == '+') s++;
    int64_t v;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) v = (int64_t)strtoull(s + 2, NULL, 16);
    else if (s[0] == '0' && s[1]) v = (int64_t)strtoull(s + 1, NULL, 8);
    else v = (int64_t)strtoull(s, NULL, 10);
    return neg ? -v : v;
}

static char *camel(const char *name) {
    size_t n = strlen(name);
    char *out = malloc(n + 1);
    size_t k = 0; int up = 0;
    for (size_t i = 0; i < n; i++) {
        char ch = name[i];
        if (ch == '_') { up = 1; continue; }
        if (up && ch >= 'a' && ch <= 'z') out[k++] = ch - 32;
        else out[k++] = ch;
        up = 0;
    }
    out[k] = 0;
    return out;
}

static void parseEnum(void) {
    if (nenums == capenums) { capenums = capenums ? capenums * 2 : 16; enums = realloc(enums, capenums * sizeof(EnumDesc)); }
    int ei = nenums++;
    memset(&enums[ei], 0, sizeof(EnumDesc));
    enums[ei].name = strdup(next());
    next(); /* { */
    while (more() && !eq(peek(), "}")) {
        const char *t = peek();
        if (eq(t, ";")) { next(); continue; }
        if (eq(t, "option") || eq(t, "reserved")) { skipStatement(); continue; }
        const char *vname = next();
        next(); /* = */
        int64_t num = parseInt(next());
        skipFieldOptions();
        next(); /* ; */
        EnumDesc *ed = &enums[ei];
        int dup = 0;
        for (int i = 0; i < ed->nvals; i++) if (ed->vals[i].num == num) dup = 1;
        if (!dup) {
            if (ed->nvals == ed->cap) { ed->cap = ed->cap ? ed->cap * 2 : 8; ed->vals = realloc(ed->vals, ed->cap * sizeof(EnumVal)); }
            ed->vals[ed->nvals].num = num;
            ed->vals[ed->nvals].name = strdup(vname);
            ed->nvals++;
        }
    }
    next();
}

static void parseField(int mi, int oneof) {
    Field f; memset(&f, 0, sizeof f);
    f.label = SINGLE; f.oneof = oneof;
    const char *t = next();
    if (eq(t, "repeated")) { f.label = REPEATED; t = next(); }
    else if (eq(t, "optional")) { f.label = OPTIONAL; t = next(); }
    if (eq(t, "map") && eq(peek(), "<")) {
        next();
        f.keyTy.name = strdup(next());
        next(); /* , */
        t = next();
        next(); /* > */
        f.label = MAP;
    }
    f.ty.name = strdup(t);
    f.json = camel(next());
    next(); /* = */
    f.num = (uint64_t)parseInt(next());
    skipFieldOptions();
    next(); /* ; */
    MsgDesc *md = &msgs[mi];
    if (md->nfields == md->capf) { md->capf = md->capf ? md->capf * 2 : 8; md->fields = realloc(md->fields, md->capf * sizeof(Field)); }
    md->fields[md->nfields++] = f;
}

static int cmpField(const void *a, const void *b) {
    uint64_t x = ((const Field *)a)->num, y = ((const Field *)b)->num;
    return x < y ? -1 : x > y;
}

static int parseMessage(void) {
    if (nmsgs == capmsgs) { capmsgs = capmsgs ? capmsgs * 2 : 16; msgs = realloc(msgs, capmsgs * sizeof(MsgDesc)); }
    int mi = nmsgs++;
    memset(&msgs[mi], 0, sizeof(MsgDesc));
    msgs[mi].name = strdup(next());
    next(); /* { */
    while (more() && !eq(peek(), "}")) {
        const char *t = peek();
        if (eq(t, ";")) next();
        else if (eq(t, "message")) { next(); parseMessage(); }
        else if (eq(t, "enum")) { next(); parseEnum(); }
        else if (eq(t, "option") || eq(t, "reserved") || eq(t, "extensions")) skipStatement();
        else if (eq(t, "oneof")) {
            next(); next();
            int oi = msgs[mi].oneofCount++;
            next(); /* { */
            while (more() && !eq(peek(), "}")) {
                const char *u = peek();
                if (eq(u, ";")) { next(); continue; }
                if (eq(u, "option")) { skipStatement(); continue; }
                parseField(mi, oi);
            }
            next();
        } else parseField(mi, -1);
    }
    next();
    qsort(msgs[mi].fields, msgs[mi].nfields, sizeof(Field), cmpField);
    return mi;
}

static void resolve(Ty *t) {
    for (int i = 0; i < 13; i++) if (eq(SCALAR_NAMES[i], t->name)) { t->kind = i; return; }
    for (int i = 0; i < nmsgs; i++) if (eq(msgs[i].name, t->name)) { t->kind = MSG; t->ref = i; return; }
    for (int i = 0; i < nenums; i++) if (eq(enums[i].name, t->name)) { t->kind = ENUM; t->ref = i; return; }
    t->kind = BYTES;
}

static void parseSchema(const char *src, size_t n) {
    tokenize(src, n);
    while (more()) {
        const char *t = peek();
        if (eq(t, "message")) { next(); int i = parseMessage(); if (root < 0) root = i; }
        else if (eq(t, "enum")) { next(); parseEnum(); }
        else if (eq(t, ";")) next();
        else skipStatement();
    }
    for (int m = 0; m < nmsgs; m++)
        for (int i = 0; i < msgs[m].nfields; i++) {
            Field *f = &msgs[m].fields[i];
            resolve(&f->ty);
            if (f->label == MAP) resolve(&f->keyTy);
        }
}

/* ---------- values ---------- */
struct MsgVal;
typedef struct {
    uint64_t n;
    const uint8_t *b; size_t blen;
    struct MsgVal *m;
} Val;

typedef struct {
    int isStr;
    int64_t ik;
    const uint8_t *sk; size_t sklen;
    Val v;
} MapEnt;

typedef struct {
    int present;
    Val v;
    Val *arr; size_t cnt, cap;
    MapEnt *ents; size_t ecnt, ecap;
} Slot;

typedef struct MsgVal {
    int mi;
    Slot *slots;
    int *oneof; /* field index set for each oneof, -1 none */
} MsgVal;

static MsgVal *newMsg(int mi) {
    MsgVal *m = calloc(1, sizeof(MsgVal));
    m->mi = mi;
    m->slots = calloc(msgs[mi].nfields ? msgs[mi].nfields : 1, sizeof(Slot));
    int oc = msgs[mi].oneofCount;
    m->oneof = malloc((oc ? oc : 1) * sizeof(int));
    for (int i = 0; i < oc; i++) m->oneof[i] = -1;
    return m;
}

static void freeMsg(MsgVal *m);
static void freeVal(Val *v) { if (v->m) { freeMsg(v->m); v->m = NULL; } }
static void clearSlot(Slot *s) {
    if (s->present) freeVal(&s->v);
    for (size_t i = 0; i < s->cnt; i++) freeVal(&s->arr[i]);
    free(s->arr);
    for (size_t i = 0; i < s->ecnt; i++) freeVal(&s->ents[i].v);
    free(s->ents);
    memset(s, 0, sizeof *s);
}
static void freeMsg(MsgVal *m) {
    if (!m) return;
    for (int i = 0; i < msgs[m->mi].nfields; i++) clearSlot(&m->slots[i]);
    free(m->slots); free(m->oneof); free(m);
}

typedef struct { const uint8_t *buf; size_t pos, end; int err; } Reader;

static uint64_t rvarint(Reader *r) {
    uint64_t v = 0; int shift = 0;
    for (;;) {
        if (r->pos >= r->end) { r->err = 1; return 0; }
        uint8_t b = r->buf[r->pos++];
        if (shift < 64) v |= (uint64_t)(b & 0x7f) << shift;
        shift += 7;
        if (!(b & 0x80)) break;
    }
    return v;
}
static uint64_t rfixed(Reader *r, int n) {
    if (r->pos + n > r->end) { r->err = 1; r->pos = r->end; return 0; }
    uint64_t v = 0;
    for (int i = 0; i < n; i++) v |= (uint64_t)r->buf[r->pos + i] << (8 * i);
    r->pos += n;
    return v;
}
static int rlen(Reader *r, size_t *s, size_t *e) {
    uint64_t l = rvarint(r);
    if (r->err || l > r->end - r->pos) { r->err = 1; r->pos = r->end; return 0; }
    *s = r->pos; r->pos += l; *e = r->pos;
    return 1;
}

static void skipField(Reader *r, int wt, uint64_t fnum) {
    size_t a, b;
    switch (wt) {
    case 0: rvarint(r); break;
    case 1: rfixed(r, 8); break;
    case 5: rfixed(r, 4); break;
    case 2: rlen(r, &a, &b); break;
    case 3:
        for (;;) {
            if (r->err) return;
            uint64_t key = rvarint(r);
            if (r->err) return;
            int w = key & 7; uint64_t n = key >> 3;
            if (w == 4) { if (n != fnum) r->err = 1; break; }
            skipField(r, w, n);
        }
        break;
    case 4: break;
    default: r->err = 1;
    }
}

static int wireOf(const Ty *t) {
    switch (t->kind) {
    case FIXED32: case SFIXED32: return 5;
    case FIXED64: case SFIXED64: return 1;
    case STRING: case BYTES: case MSG: return 2;
    default: return 0;
    }
}

static Val readScalar(Reader *r, int wt) {
    Val v; memset(&v, 0, sizeof v);
    if (wt == 0) v.n = rvarint(r);
    else if (wt == 5) v.n = rfixed(r, 4);
    else if (wt == 1) v.n = rfixed(r, 8);
    else {
        size_t a, b;
        if (rlen(r, &a, &b)) { v.b = r->buf + a; v.blen = b - a; }
    }
    return v;
}

static int64_t convInt(int kind, uint64_t v) {
    switch (kind) {
    case INT32: case SFIXED32: case ENUM: return (int32_t)(uint32_t)v;
    case UINT32: case FIXED32: return (int64_t)(uint32_t)v;
    case SINT32: { uint32_t u = (uint32_t)v; return (int32_t)((u >> 1) ^ (0u - (u & 1))); }
    case SINT64: return (int64_t)((v >> 1) ^ (0ull - (v & 1)));
    case BOOL_: return v != 0;
    default: return (int64_t)v;
    }
}

static void decodeInto(int mi, MsgVal *msg, const uint8_t *buf, size_t start, size_t end, int *err);

static void pushArr(Slot *s, Val v) {
    if (s->cnt == s->cap) { s->cap = s->cap ? s->cap * 2 : 4; s->arr = realloc(s->arr, s->cap * sizeof(Val)); }
    s->arr[s->cnt++] = v;
}

static void decodeInto(int mi, MsgVal *msg, const uint8_t *buf, size_t start, size_t end, int *err) {
    MsgDesc *md = &msgs[mi];
    Reader r = {buf, start, end, 0};
    while (r.pos < r.end && !r.err) {
        uint64_t key = rvarint(&r);
        if (r.err) break;
        int wt = key & 7; uint64_t fnum = key >> 3;
        int fi = -1;
        /* binary search on sorted fields */
        int lo = 0, hi = md->nfields - 1;
        while (lo <= hi) {
            int mid = (lo + hi) / 2;
            if (md->fields[mid].num == fnum) { fi = mid; break; }
            if (md->fields[mid].num < fnum) lo = mid + 1; else hi = mid - 1;
        }
        if (fi < 0) { skipField(&r, wt, fnum); continue; }
        Field *f = &md->fields[fi];
        Slot *slot = &msg->slots[fi];
        if (f->label == MAP) {
            if (wt != 2) { skipField(&r, wt, fnum); continue; }
            size_t a, b;
            if (!rlen(&r, &a, &b)) break;
            Val kv, vv; memset(&kv, 0, sizeof kv); memset(&vv, 0, sizeof vv);
            Reader er = {buf, a, b, 0};
            while (er.pos < er.end && !er.err) {
                uint64_t k2 = rvarint(&er);
                if (er.err) break;
                int w2 = k2 & 7; uint64_t n2 = k2 >> 3;
                Ty *t = n2 == 1 ? &f->keyTy : n2 == 2 ? &f->ty : NULL;
                if (t && wireOf(t) == w2) {
                    if (t->kind == MSG) {
                        size_t c, d;
                        if (!rlen(&er, &c, &d)) break;
                        if (!vv.m) vv.m = newMsg(t->ref);
                        decodeInto(t->ref, vv.m, buf, c, d, &er.err);
                    } else {
                        Val v = readScalar(&er, w2);
                        if (n2 == 1) kv = v; else vv = v;
                    }
                } else skipField(&er, w2, n2);
            }
            if (er.err) r.err = 1;
            if (f->ty.kind == MSG && !vv.m) vv.m = newMsg(f->ty.ref);
            MapEnt me; memset(&me, 0, sizeof me);
            if (f->keyTy.kind == STRING) { me.isStr = 1; me.sk = kv.b; me.sklen = kv.blen; }
            else me.ik = convInt(f->keyTy.kind, kv.n);
            me.v = vv;
            size_t k;
            for (k = 0; k < slot->ecnt; k++) {
                MapEnt *o = &slot->ents[k];
                if (me.isStr ? (o->sklen == me.sklen && (me.sklen == 0 || memcmp(o->sk, me.sk, me.sklen) == 0)) : o->ik == me.ik) break;
            }
            if (k < slot->ecnt) { freeVal(&slot->ents[k].v); slot->ents[k] = me; }
            else {
                if (slot->ecnt == slot->ecap) { slot->ecap = slot->ecap ? slot->ecap * 2 : 4; slot->ents = realloc(slot->ents, slot->ecap * sizeof(MapEnt)); }
                slot->ents[slot->ecnt++] = me;
            }
            continue;
        }
        int ewt = wireOf(&f->ty);
        if (f->label == REPEATED) {
            if (wt == 2 && ewt != 2) {
                size_t a, b;
                if (!rlen(&r, &a, &b)) break;
                Reader sr = {buf, a, b, 0};
                while (sr.pos < sr.end && !sr.err) {
                    Val v = readScalar(&sr, ewt);
                    if (sr.err) break;
                    pushArr(slot, v);
                }
                if (sr.err) r.err = 1;
                continue;
            }
            if (wt != ewt) { skipField(&r, wt, fnum); continue; }
            Val v; memset(&v, 0, sizeof v);
            if (f->ty.kind == MSG) {
                size_t a, b;
                if (!rlen(&r, &a, &b)) break;
                v.m = newMsg(f->ty.ref);
                decodeInto(f->ty.ref, v.m, buf, a, b, &r.err);
            } else {
                v = readScalar(&r, wt);
                if (r.err) break;
            }
            pushArr(slot, v);
            continue;
        }
        if (wt != ewt) { skipField(&r, wt, fnum); continue; }
        if (f->oneof >= 0) {
            int cur = msg->oneof[f->oneof];
            if (cur >= 0 && cur != fi) clearSlot(&msg->slots[cur]);
            msg->oneof[f->oneof] = fi;
        }
        if (f->ty.kind == MSG) {
            size_t a, b;
            if (!rlen(&r, &a, &b)) break;
            if (!slot->present || !slot->v.m) { slot->v.m = newMsg(f->ty.ref); slot->present = 1; }
            decodeInto(f->ty.ref, slot->v.m, buf, a, b, &r.err);
        } else {
            Val v = readScalar(&r, wt);
            if (r.err) break;
            slot->v = v;
            slot->present = 1;
        }
    }
    if (r.err) *err = 1;
}

/* ---------- output ---------- */
static char *ob; static size_t olen, ocap;
static void oput(const void *p, size_t n) {
    if (olen + n > ocap) { while (olen + n > ocap) ocap = ocap ? ocap * 2 : 1 << 16; ob = realloc(ob, ocap); }
    memcpy(ob + olen, p, n); olen += n;
}
static void oc(char c) { oput(&c, 1); }
static void os_(const char *s) { oput(s, strlen(s)); }

static void esc(const uint8_t *b, size_t n) {
    oc('"');
    for (size_t i = 0; i < n; i++) {
        uint8_t c = b[i];
        switch (c) {
        case '"': os_("\\\""); break;
        case '\\': os_("\\\\"); break;
        case 8: os_("\\b"); break;
        case 9: os_("\\t"); break;
        case 10: os_("\\n"); break;
        case 12: os_("\\f"); break;
        case 13: os_("\\r"); break;
        default:
            if (c < 0x20) { char tmp[8]; snprintf(tmp, sizeof tmp, "\\u%04x", c); os_(tmp); }
            else oc((char)c);
        }
    }
    oc('"');
}

static void base64(const uint8_t *b, size_t n) {
    static const char A[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    oc('"');
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)b[i] << 16;
        if (i + 1 < n) v |= (uint32_t)b[i + 1] << 8;
        if (i + 2 < n) v |= b[i + 2];
        oc(A[(v >> 18) & 63]);
        oc(A[(v >> 12) & 63]);
        oc(i + 1 < n ? A[(v >> 6) & 63] : '=');
        oc(i + 2 < n ? A[v & 63] : '=');
    }
    oc('"');
}

static void fmtMsg(MsgVal *m);

static void fmtVal(const Ty *t, const Val *v) {
    char tmp[48];
    switch (t->kind) {
    case MSG: fmtMsg(v->m); return;
    case STRING: esc(v->b, v->blen); return;
    case BYTES: base64(v->b, v->blen); return;
    case BOOL_: os_(v->n ? "true" : "false"); return;
    case UINT64: case FIXED64: snprintf(tmp, sizeof tmp, "\"%llu\"", (unsigned long long)v->n); os_(tmp); return;
    case INT64: case SINT64: case SFIXED64:
        snprintf(tmp, sizeof tmp, "\"%lld\"", (long long)convInt(t->kind, v->n)); os_(tmp); return;
    case ENUM: {
        int64_t x = convInt(ENUM, v->n);
        EnumDesc *ed = &enums[t->ref];
        for (int i = 0; i < ed->nvals; i++)
            if (ed->vals[i].num == x) { esc((const uint8_t *)ed->vals[i].name, strlen(ed->vals[i].name)); return; }
        snprintf(tmp, sizeof tmp, "%lld", (long long)x); os_(tmp); return;
    }
    default:
        snprintf(tmp, sizeof tmp, "%lld", (long long)convInt(t->kind, v->n)); os_(tmp); return;
    }
}

static int cmpEnt(const void *a, const void *b) {
    const MapEnt *x = a, *y = b;
    if (x->isStr) {
        size_t n = x->sklen < y->sklen ? x->sklen : y->sklen;
        int c = n ? memcmp(x->sk, y->sk, n) : 0;
        if (c) return c;
        return x->sklen < y->sklen ? -1 : x->sklen > y->sklen;
    }
    return x->ik < y->ik ? -1 : x->ik > y->ik;
}

static void fmtMsg(MsgVal *m) {
    MsgDesc *md = &msgs[m->mi];
    oc('{');
    int first = 1;
    for (int i = 0; i < md->nfields; i++) {
        Field *f = &md->fields[i];
        Slot *s = &m->slots[i];
        if (f->label == MAP) { if (!s->ecnt) continue; }
        else if (f->label == REPEATED) { if (!s->cnt) continue; }
        else {
            if (!s->present) continue;
            if (f->ty.kind != MSG && f->label != OPTIONAL && f->oneof < 0) {
                int def;
                if (f->ty.kind == STRING || f->ty.kind == BYTES) def = s->v.blen == 0;
                else def = convInt(f->ty.kind, s->v.n) == 0;
                if (def) continue;
            }
        }
        if (!first) oc(',');
        first = 0;
        esc((const uint8_t *)f->json, strlen(f->json));
        oc(':');
        if (f->label == MAP) {
            qsort(s->ents, s->ecnt, sizeof(MapEnt), cmpEnt);
            oc('{');
            for (size_t k = 0; k < s->ecnt; k++) {
                if (k) oc(',');
                MapEnt *e = &s->ents[k];
                if (e->isStr) esc(e->sk, e->sklen);
                else { char tmp[32]; snprintf(tmp, sizeof tmp, "\"%lld\"", (long long)e->ik); os_(tmp); }
                oc(':');
                fmtVal(&f->ty, &e->v);
            }
            oc('}');
        } else if (f->label == REPEATED) {
            oc('[');
            for (size_t k = 0; k < s->cnt; k++) {
                if (k) oc(',');
                fmtVal(&f->ty, &s->arr[k]);
            }
            oc(']');
        } else fmtVal(&f->ty, &s->v);
    }
    oc('}');
}

static uint8_t *readFile(const char *path, size_t *len) {
    FILE *fp = fopen(path, "rb");
    if (!fp) { *len = 0; return calloc(1, 1); }
    size_t cap = 1 << 16, n = 0;
    uint8_t *b = malloc(cap);
    for (;;) {
        if (n == cap) { cap *= 2; b = realloc(b, cap); }
        size_t k = fread(b + n, 1, cap - n, fp);
        if (k == 0) break;
        n += k;
    }
    fclose(fp);
    *len = n;
    return b;
}

int main(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : ".";
    char path[4096];
    size_t slen, dlen;
    snprintf(path, sizeof path, "%s/schema.proto", dir);
    uint8_t *src = readFile(path, &slen);
    snprintf(path, sizeof path, "%s/messages.bin", dir);
    uint8_t *data = readFile(path, &dlen);
    parseSchema((const char *)src, slen);
    if (root < 0) return 0;
    Reader r = {data, 0, dlen, 0};
    while (r.pos < r.end) {
        size_t a, b;
        if (!rlen(&r, &a, &b)) break;
        MsgVal *m = newMsg(root);
        int err = 0;
        decodeInto(root, m, data, a, b, &err);
        fmtMsg(m);
        oc('\n');
        freeMsg(m);
    }
    fwrite(ob, 1, olen, stdout);
    return 0;
}
