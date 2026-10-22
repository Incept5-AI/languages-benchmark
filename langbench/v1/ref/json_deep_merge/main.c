#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ---------- JSON value model ---------- */

typedef enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ } JType;

typedef struct JValue JValue;

typedef struct {
    char *key;
    JValue *val;
} JMember;

struct JValue {
    JType type;
    /* bool */
    int b;
    /* number: keep the original textual form so integers stay integers */
    char *num;
    /* string: raw content (already unescaped is unnecessary; we keep the
       exact escaped source text and re-emit it verbatim) */
    char *str;       /* stored as raw escaped text WITHOUT surrounding quotes */
    /* array */
    JValue **items;
    size_t nitems;
    size_t capitems;
    /* object */
    JMember *members;
    size_t nmembers;
    size_t capmembers;
};

static void *xmalloc(size_t n) {
    void *p = malloc(n);
    if (!p) { fprintf(stderr, "oom\n"); exit(1); }
    return p;
}
static void *xrealloc(void *p, size_t n) {
    p = realloc(p, n);
    if (!p) { fprintf(stderr, "oom\n"); exit(1); }
    return p;
}

static JValue *new_val(JType t) {
    JValue *v = xmalloc(sizeof(JValue));
    memset(v, 0, sizeof(*v));
    v->type = t;
    return v;
}

/* ---------- Parser ---------- */

typedef struct {
    const char *s;
    size_t i;
    size_t n;
} Parser;

static void skip_ws(Parser *p) {
    while (p->i < p->n) {
        char c = p->s[p->i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p->i++;
        else break;
    }
}

static JValue *parse_value(Parser *p);

static void perr(const char *msg) {
    fprintf(stderr, "parse error: %s\n", msg);
    exit(1);
}

/* Parse a string token; returns the raw escaped content between quotes
   (so it can be re-emitted verbatim). Assumes current char is '"'. */
static char *parse_string_raw(Parser *p) {
    if (p->s[p->i] != '"') perr("expected string");
    p->i++; /* opening quote */
    size_t start = p->i;
    char *buf = NULL;
    size_t len = 0, cap = 0;
    while (p->i < p->n) {
        char c = p->s[p->i];
        if (c == '"') {
            p->i++; /* closing quote */
            char *out = xmalloc(len + 1);
            memcpy(out, buf ? buf : "", len);
            out[len] = '\0';
            free(buf);
            return out;
        }
        if (c == '\\') {
            /* copy escape sequence verbatim (two chars, or \uXXXX) */
            if (p->i + 1 >= p->n) perr("bad escape");
            char e = p->s[p->i + 1];
            size_t take = 2;
            if (e == 'u') take = 6;
            if (p->i + take > p->n) perr("bad escape");
            if (len + take + 1 > cap) {
                cap = (len + take + 1) * 2;
                buf = xrealloc(buf, cap);
            }
            memcpy(buf + len, p->s + p->i, take);
            len += take;
            p->i += take;
        } else {
            if (len + 2 > cap) {
                cap = (len + 2) * 2;
                buf = xrealloc(buf, cap);
            }
            buf[len++] = c;
            p->i++;
        }
    }
    (void)start;
    perr("unterminated string");
    return NULL;
}

static JValue *parse_number(Parser *p) {
    size_t start = p->i;
    if (p->s[p->i] == '-') p->i++;
    while (p->i < p->n) {
        char c = p->s[p->i];
        if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' ||
            c == '+' || c == '-') p->i++;
        else break;
    }
    size_t len = p->i - start;
    char *num = xmalloc(len + 1);
    memcpy(num, p->s + start, len);
    num[len] = '\0';
    JValue *v = new_val(J_NUM);
    v->num = num;
    return v;
}

static JValue *parse_array(Parser *p) {
    p->i++; /* '[' */
    JValue *v = new_val(J_ARR);
    skip_ws(p);
    if (p->i < p->n && p->s[p->i] == ']') { p->i++; return v; }
    while (1) {
        skip_ws(p);
        JValue *item = parse_value(p);
        if (v->nitems == v->capitems) {
            v->capitems = v->capitems ? v->capitems * 2 : 4;
            v->items = xrealloc(v->items, v->capitems * sizeof(JValue *));
        }
        v->items[v->nitems++] = item;
        skip_ws(p);
        if (p->i >= p->n) perr("unterminated array");
        char c = p->s[p->i];
        if (c == ',') { p->i++; continue; }
        if (c == ']') { p->i++; break; }
        perr("expected , or ] in array");
    }
    return v;
}

static JValue *parse_object(Parser *p) {
    p->i++; /* '{' */
    JValue *v = new_val(J_OBJ);
    skip_ws(p);
    if (p->i < p->n && p->s[p->i] == '}') { p->i++; return v; }
    while (1) {
        skip_ws(p);
        char *key = parse_string_raw(p);
        skip_ws(p);
        if (p->i >= p->n || p->s[p->i] != ':') perr("expected : in object");
        p->i++;
        skip_ws(p);
        JValue *val = parse_value(p);
        if (v->nmembers == v->capmembers) {
            v->capmembers = v->capmembers ? v->capmembers * 2 : 4;
            v->members = xrealloc(v->members, v->capmembers * sizeof(JMember));
        }
        v->members[v->nmembers].key = key;
        v->members[v->nmembers].val = val;
        v->nmembers++;
        skip_ws(p);
        if (p->i >= p->n) perr("unterminated object");
        char c = p->s[p->i];
        if (c == ',') { p->i++; continue; }
        if (c == '}') { p->i++; break; }
        perr("expected , or } in object");
    }
    return v;
}

static JValue *parse_value(Parser *p) {
    skip_ws(p);
    if (p->i >= p->n) perr("unexpected end");
    char c = p->s[p->i];
    if (c == '{') return parse_object(p);
    if (c == '[') return parse_array(p);
    if (c == '"') {
        char *s = parse_string_raw(p);
        JValue *v = new_val(J_STR);
        v->str = s;
        return v;
    }
    if (c == '-' || (c >= '0' && c <= '9')) return parse_number(p);
    if (strncmp(p->s + p->i, "true", 4) == 0) {
        p->i += 4; JValue *v = new_val(J_BOOL); v->b = 1; return v;
    }
    if (strncmp(p->s + p->i, "false", 5) == 0) {
        p->i += 5; JValue *v = new_val(J_BOOL); v->b = 0; return v;
    }
    if (strncmp(p->s + p->i, "null", 4) == 0) {
        p->i += 4; return new_val(J_NULL);
    }
    perr("unexpected token");
    return NULL;
}

/* ---------- Deep merge ---------- */

/* Returns 1 if two string-typed scalar values are equal (raw text compare). */
static int val_equal(const JValue *a, const JValue *b) {
    if (a->type != b->type) return 0;
    switch (a->type) {
        case J_NULL: return 1;
        case J_BOOL: return a->b == b->b;
        case J_NUM:  return strcmp(a->num, b->num) == 0;
        case J_STR:  return strcmp(a->str, b->str) == 0;
        default:     return 0; /* arrays/objects not compared for dedup here */
    }
}

static JValue *deep_merge(JValue *a, JValue *b);

static JValue *clone_val(JValue *v); /* fwd */

static JValue *merge_arrays(JValue *a, JValue *b) {
    JValue *out = new_val(J_ARR);
    /* add all from A (dedup within combined output) */
    for (size_t i = 0; i < a->nitems; i++) {
        JValue *cand = a->items[i];
        int dup = 0;
        for (size_t k = 0; k < out->nitems; k++)
            if (val_equal(out->items[k], cand)) { dup = 1; break; }
        if (dup) continue;
        if (out->nitems == out->capitems) {
            out->capitems = out->capitems ? out->capitems * 2 : 4;
            out->items = xrealloc(out->items, out->capitems * sizeof(JValue *));
        }
        out->items[out->nitems++] = clone_val(cand);
    }
    for (size_t i = 0; i < b->nitems; i++) {
        JValue *cand = b->items[i];
        int dup = 0;
        for (size_t k = 0; k < out->nitems; k++)
            if (val_equal(out->items[k], cand)) { dup = 1; break; }
        if (dup) continue;
        if (out->nitems == out->capitems) {
            out->capitems = out->capitems ? out->capitems * 2 : 4;
            out->items = xrealloc(out->items, out->capitems * sizeof(JValue *));
        }
        out->items[out->nitems++] = clone_val(cand);
    }
    return out;
}

static JValue *clone_val(JValue *v) {
    JValue *c = new_val(v->type);
    switch (v->type) {
        case J_NULL: break;
        case J_BOOL: c->b = v->b; break;
        case J_NUM:  c->num = strdup(v->num); break;
        case J_STR:  c->str = strdup(v->str); break;
        case J_ARR:
            for (size_t i = 0; i < v->nitems; i++) {
                if (c->nitems == c->capitems) {
                    c->capitems = c->capitems ? c->capitems * 2 : 4;
                    c->items = xrealloc(c->items, c->capitems * sizeof(JValue *));
                }
                c->items[c->nitems++] = clone_val(v->items[i]);
            }
            break;
        case J_OBJ:
            for (size_t i = 0; i < v->nmembers; i++) {
                if (c->nmembers == c->capmembers) {
                    c->capmembers = c->capmembers ? c->capmembers * 2 : 4;
                    c->members = xrealloc(c->members, c->capmembers * sizeof(JMember));
                }
                c->members[c->nmembers].key = strdup(v->members[i].key);
                c->members[c->nmembers].val = clone_val(v->members[i].val);
                c->nmembers++;
            }
            break;
    }
    return c;
}

static JValue *obj_find(JValue *o, const char *key) {
    for (size_t i = 0; i < o->nmembers; i++)
        if (strcmp(o->members[i].key, key) == 0) return o->members[i].val;
    return NULL;
}

static void obj_add(JValue *o, const char *key, JValue *val) {
    if (o->nmembers == o->capmembers) {
        o->capmembers = o->capmembers ? o->capmembers * 2 : 4;
        o->members = xrealloc(o->members, o->capmembers * sizeof(JMember));
    }
    o->members[o->nmembers].key = strdup(key);
    o->members[o->nmembers].val = val;
    o->nmembers++;
}

static JValue *deep_merge(JValue *a, JValue *b) {
    if (a->type == J_OBJ && b->type == J_OBJ) {
        JValue *out = new_val(J_OBJ);
        /* keys from A */
        for (size_t i = 0; i < a->nmembers; i++) {
            const char *k = a->members[i].key;
            JValue *bv = obj_find(b, k);
            if (bv) obj_add(out, k, deep_merge(a->members[i].val, bv));
            else    obj_add(out, k, clone_val(a->members[i].val));
        }
        /* keys only in B */
        for (size_t i = 0; i < b->nmembers; i++) {
            const char *k = b->members[i].key;
            if (!obj_find(a, k)) obj_add(out, k, clone_val(b->members[i].val));
        }
        return out;
    }
    if (a->type == J_ARR && b->type == J_ARR) {
        return merge_arrays(a, b);
    }
    /* scalar conflict or differing types: B overrides */
    return clone_val(b);
}

/* ---------- Output (sorted keys, 2-space indent) ---------- */

static int cmp_member(const void *x, const void *y) {
    const JMember *a = x, *b = y;
    return strcmp(a->key, b->key);
}

static void indent(FILE *f, int depth) {
    for (int i = 0; i < depth * 2; i++) fputc(' ', f);
}

static void emit(FILE *f, JValue *v, int depth) {
    switch (v->type) {
        case J_NULL: fputs("null", f); break;
        case J_BOOL: fputs(v->b ? "true" : "false", f); break;
        case J_NUM:  fputs(v->num, f); break;
        case J_STR:  fputc('"', f); fputs(v->str, f); fputc('"', f); break;
        case J_ARR:
            if (v->nitems == 0) { fputs("[]", f); break; }
            fputs("[\n", f);
            for (size_t i = 0; i < v->nitems; i++) {
                indent(f, depth + 1);
                emit(f, v->items[i], depth + 1);
                if (i + 1 < v->nitems) fputc(',', f);
                fputc('\n', f);
            }
            indent(f, depth);
            fputc(']', f);
            break;
        case J_OBJ: {
            if (v->nmembers == 0) { fputs("{}", f); break; }
            /* sort members by key */
            JMember *sorted = xmalloc(v->nmembers * sizeof(JMember));
            memcpy(sorted, v->members, v->nmembers * sizeof(JMember));
            qsort(sorted, v->nmembers, sizeof(JMember), cmp_member);
            fputs("{\n", f);
            for (size_t i = 0; i < v->nmembers; i++) {
                indent(f, depth + 1);
                fputc('"', f); fputs(sorted[i].key, f); fputs("\": ", f);
                emit(f, sorted[i].val, depth + 1);
                if (i + 1 < v->nmembers) fputc(',', f);
                fputc('\n', f);
            }
            free(sorted);
            indent(f, depth);
            fputc('}', f);
            break;
        }
    }
}

/* ---------- File reading ---------- */

static char *read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = xmalloc(sz + 1);
    size_t rd = fread(buf, 1, sz, f);
    buf[rd] = '\0';
    fclose(f);
    return buf;
}

int main(void) {
    const char *path_a = "__FIXTURE_DIR__/config_a.json";
    const char *path_b = "__FIXTURE_DIR__/config_b.json";

    char *ta = read_file(path_a);
    char *tb = read_file(path_b);

    Parser pa = { ta, 0, strlen(ta) };
    Parser pb = { tb, 0, strlen(tb) };

    JValue *va = parse_value(&pa);
    JValue *vb = parse_value(&pb);

    JValue *merged = deep_merge(va, vb);

    emit(stdout, merged, 0);
    fputc('\n', stdout);
    return 0;
}
