#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <pthread.h>

/* ---------- values ---------- */
enum { T_NIL, T_INT, T_BOOL, T_STR, T_LIST, T_FN, T_BUILTIN };

typedef struct Str { long rc; int64_t len; char data[]; } Str;
struct List; struct Closure; struct Scope; struct FnDef; struct Node;

typedef struct Value {
    int t;
    union { int64_t i; int b; Str *s; struct List *l; struct Closure *f; int bi; } u;
} Value;

typedef struct List { long rc; int64_t len, cap; Value *items; } List;
typedef struct FnDef { int nparams; int *params; struct Node **body; int nbody; } FnDef;
typedef struct Closure { long rc; FnDef *def; struct Scope *env; } Closure;
typedef struct Scope {
    long rc; struct Scope *parent; int n, cap; int *ids; Value *vals;
    int inl_ids[4]; Value inl_vals[4];
} Scope;

static Value NILV = { T_NIL, { 0 } };
static Value mkint(int64_t i) { Value v; v.t = T_INT; v.u.i = i; return v; }
static Value mkbool(int b) { Value v; v.t = T_BOOL; v.u.b = b ? 1 : 0; return v; }
static Value mkstr(Str *s) { Value v; v.t = T_STR; v.u.s = s; return v; }
static Value mklist(List *l) { Value v; v.t = T_LIST; v.u.l = l; return v; }

/* ---------- output ---------- */
typedef struct Buf { char *d; size_t n, cap; } Buf;
static void buf_put(Buf *b, const char *s, size_t n) {
    if (b->n + n > b->cap) {
        size_t c = b->cap ? b->cap : 256;
        while (c < b->n + n) c *= 2;
        b->d = realloc(b->d, c); b->cap = c;
    }
    memcpy(b->d + b->n, s, n); b->n += n;
}
static void buf_putc(Buf *b, char c) { buf_put(b, &c, 1); }
static void buf_puts(Buf *b, const char *s) { buf_put(b, s, strlen(s)); }
static Buf out;
static void flush_out(void) { if (out.n) { fwrite(out.d, 1, out.n, stdout); out.n = 0; } fflush(stdout); }

static void rt_error(int line, const char *fmt, ...) {
    char msg[512];
    va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
    char full[700];
    snprintf(full, sizeof full, "error at line %d: %s\n", line, msg);
    buf_puts(&out, full);
    flush_out();
    exit(0);
}

/* ---------- memory ---------- */
static Str *str_new(const char *d, int64_t len) {
    Str *s = malloc(sizeof(Str) + (size_t)len + 1);
    s->rc = 1; s->len = len;
    if (d) memcpy(s->data, d, (size_t)len);
    s->data[len] = 0;
    return s;
}
static List *list_new(int64_t cap) {
    List *l = malloc(sizeof(List));
    l->rc = 1; l->len = 0; l->cap = cap < 4 ? 4 : cap;
    l->items = malloc(sizeof(Value) * (size_t)l->cap);
    return l;
}
static void list_push(List *l, Value v) {
    if (l->len == l->cap) { l->cap *= 2; l->items = realloc(l->items, sizeof(Value) * (size_t)l->cap); }
    l->items[l->len++] = v;
}
static inline void incref(Value v) {
    switch (v.t) {
        case T_STR: v.u.s->rc++; break;
        case T_LIST: v.u.l->rc++; break;
        case T_FN: v.u.f->rc++; break;
        default: break;
    }
}
static void scope_decref(Scope *s);
static void decref(Value v) {
    switch (v.t) {
        case T_STR: if (--v.u.s->rc == 0) free(v.u.s); break;
        case T_LIST:
            if (--v.u.l->rc == 0) {
                List *l = v.u.l;
                for (int64_t i = 0; i < l->len; i++) decref(l->items[i]);
                free(l->items); free(l);
            }
            break;
        case T_FN:
            if (--v.u.f->rc == 0) { Scope *e = v.u.f->env; free(v.u.f); scope_decref(e); }
            break;
        default: break;
    }
}
static Scope *scope_new(Scope *parent) {
    Scope *s = malloc(sizeof(Scope));
    s->rc = 1; s->parent = parent; if (parent) parent->rc++;
    s->n = 0; s->cap = 4; s->ids = s->inl_ids; s->vals = s->inl_vals;
    return s;
}
static void scope_decref(Scope *s) {
    while (s && --s->rc == 0) {
        for (int i = 0; i < s->n; i++) decref(s->vals[i]);
        if (s->ids != s->inl_ids) { free(s->ids); free(s->vals); }
        Scope *p = s->parent;
        free(s);
        s = p;
    }
}
static void scope_declare(Scope *s, int id, Value v) {
    if (s->n == s->cap) {
        int nc = s->cap * 2;
        int *ni = malloc(sizeof(int) * (size_t)nc); Value *nv = malloc(sizeof(Value) * (size_t)nc);
        memcpy(ni, s->ids, sizeof(int) * (size_t)s->n); memcpy(nv, s->vals, sizeof(Value) * (size_t)s->n);
        if (s->ids != s->inl_ids) { free(s->ids); free(s->vals); }
        s->ids = ni; s->vals = nv; s->cap = nc;
    }
    s->ids[s->n] = id; s->vals[s->n] = v; s->n++;
}
static inline Value *scope_lookup(Scope *s, int id) {
    for (; s; s = s->parent) {
        int *ids = s->ids;
        for (int i = s->n - 1; i >= 0; i--) if (ids[i] == id) return &s->vals[i];
    }
    return NULL;
}

/* ---------- names ---------- */
static char **names; static int nnames, capnames;
static int intern(const char *s) {
    for (int i = 0; i < nnames; i++) if (strcmp(names[i], s) == 0) return i;
    if (nnames == capnames) { capnames = capnames ? capnames * 2 : 64; names = realloc(names, sizeof(char *) * (size_t)capnames); }
    names[nnames] = strdup(s);
    return nnames++;
}

/* ---------- lexer ---------- */
enum { K_INT, K_STR, K_ID, K_KW, K_OP, K_EOF };
typedef struct Tok { int kind; char *text; int64_t ival; Str *sval; int line; } Tok;
static Tok *toks; static int ntoks, captoks;
static const char *KWS[] = { "let", "fn", "if", "else", "while", "for", "in", "return", "break", "continue", "and", "or", "not", "true", "false", "nil", NULL };

static void addtok(int kind, const char *s, size_t n, int line) {
    if (ntoks == captoks) { captoks = captoks ? captoks * 2 : 256; toks = realloc(toks, sizeof(Tok) * (size_t)captoks); }
    Tok *t = &toks[ntoks++];
    t->kind = kind; t->text = malloc(n + 1); memcpy(t->text, s, n); t->text[n] = 0; t->line = line; t->ival = 0; t->sval = NULL;
}
static int isdig(int c) { return c >= '0' && c <= '9'; }
static int isalph(int c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_'; }

static void lex(const char *s, size_t n) {
    size_t i = 0; int line = 1;
    while (i < n) {
        char c = s[i];
        if (c == '\n') { line++; i++; continue; }
        if (c == ' ' || c == '\t' || c == '\r') { i++; continue; }
        if (c == '#') { while (i < n && s[i] != '\n') i++; continue; }
        if (isdig(c)) {
            size_t j = i; while (j < n && isdig(s[j])) j++;
            addtok(K_INT, s + i, j - i, line);
            toks[ntoks - 1].ival = (int64_t)strtoull(toks[ntoks - 1].text, NULL, 10);
            i = j; continue;
        }
        if (isalph(c)) {
            size_t j = i; while (j < n && (isalph(s[j]) || isdig(s[j]))) j++;
            int kw = 0; size_t len = j - i;
            for (int k = 0; KWS[k]; k++) if (strlen(KWS[k]) == len && memcmp(KWS[k], s + i, len) == 0) { kw = 1; break; }
            addtok(kw ? K_KW : K_ID, s + i, len, line);
            i = j; continue;
        }
        if (c == '"') {
            Buf b = { 0 };
            i++;
            while (i < n && s[i] != '"') {
                if (s[i] == '\\' && i + 1 < n) {
                    char e = s[i + 1];
                    buf_putc(&b, e == 'n' ? '\n' : e == 't' ? '\t' : e);
                    i += 2;
                } else { buf_putc(&b, s[i]); i++; }
            }
            i++;
            addtok(K_STR, "", 0, line);
            toks[ntoks - 1].sval = str_new(b.d, (int64_t)b.n);
            free(b.d);
            continue;
        }
        if (i + 1 < n && s[i + 1] == '=' && (c == '=' || c == '!' || c == '<' || c == '>')) {
            addtok(K_OP, s + i, 2, line); i += 2; continue;
        }
        addtok(K_OP, s + i, 1, line); i++;
    }
    addtok(K_EOF, "", 0, line);
}

/* ---------- AST ---------- */
enum {
    E_LIT, E_VAR, E_LIST, E_FN, E_CALL, E_INDEX, E_AND, E_OR, E_NOT, E_NEG, E_BIN,
    S_LET, S_FNDECL, S_IF, S_BLOCK, S_WHILE, S_FOR, S_RETURN, S_BREAK, S_CONT, S_ASSIGN, S_IDXASSIGN, S_EXPR
};
enum { OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_EQ, OP_NE, OP_LT, OP_LE, OP_GT, OP_GE };
static const char *OPS[] = { "+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=" };

typedef struct Node {
    int kind, line, op, id;
    Value lit;
    struct Node *a, *b, *c;
    struct Node **list; int nlist;
    FnDef *fn;
} Node;

static Node *node(int kind, int line) { Node *n = calloc(1, sizeof(Node)); n->kind = kind; n->line = line; return n; }

typedef struct NVec { Node **d; int n, cap; } NVec;
static void nv_push(NVec *v, Node *x) {
    if (v->n == v->cap) { v->cap = v->cap ? v->cap * 2 : 4; v->d = realloc(v->d, sizeof(Node *) * (size_t)v->cap); }
    v->d[v->n++] = x;
}

/* ---------- parser ---------- */
static int pos;
static Tok *peek(void) { return &toks[pos]; }
static Tok *next(void) { return &toks[pos++]; }
static int is_op(const char *t) { return toks[pos].kind == K_OP && strcmp(toks[pos].text, t) == 0; }
static int is_kw(const char *t) { return toks[pos].kind == K_KW && strcmp(toks[pos].text, t) == 0; }
static void expect(const char *t) { (void)t; pos++; }

static Node *parse_expr(void);
static Node *parse_stmt(void);

static Node *parse_block(void) {
    Node *b = node(S_BLOCK, peek()->line);
    NVec v = { 0 };
    expect("{");
    while (!is_op("}")) nv_push(&v, parse_stmt());
    expect("}");
    b->list = v.d; b->nlist = v.n;
    return b;
}
static FnDef *parse_fn_rest(void) {
    expect("(");
    FnDef *d = calloc(1, sizeof(FnDef));
    int cap = 4; d->params = malloc(sizeof(int) * (size_t)cap);
    if (!is_op(")")) {
        d->params[d->nparams++] = intern(next()->text);
        while (is_op(",")) {
            next();
            if (d->nparams == cap) { cap *= 2; d->params = realloc(d->params, sizeof(int) * (size_t)cap); }
            d->params[d->nparams++] = intern(next()->text);
        }
    }
    expect(")");
    Node *b = parse_block();
    d->body = b->list; d->nbody = b->nlist;
    return d;
}
static Node *parse_if(void) {
    Node *n = node(S_IF, next()->line);
    n->a = parse_expr();
    n->b = parse_block();
    if (is_kw("else")) {
        next();
        n->c = is_kw("if") ? parse_if() : parse_block();
    }
    return n;
}
static Node *parse_stmt(void) {
    Tok *t = peek();
    if (t->kind == K_KW) {
        const char *k = t->text;
        if (!strcmp(k, "let")) {
            next(); Node *n = node(S_LET, t->line); n->id = intern(next()->text); expect("="); n->a = parse_expr(); expect(";"); return n;
        }
        if (!strcmp(k, "fn") && toks[pos + 1].kind == K_ID) {
            next(); Node *n = node(S_FNDECL, t->line); n->id = intern(next()->text); n->fn = parse_fn_rest(); return n;
        }
        if (!strcmp(k, "if")) return parse_if();
        if (!strcmp(k, "while")) { next(); Node *n = node(S_WHILE, t->line); n->a = parse_expr(); n->b = parse_block(); return n; }
        if (!strcmp(k, "for")) {
            next(); Node *n = node(S_FOR, t->line); n->id = intern(next()->text); expect("in"); n->a = parse_expr(); n->b = parse_block(); return n;
        }
        if (!strcmp(k, "return")) { next(); Node *n = node(S_RETURN, t->line); if (!is_op(";")) n->a = parse_expr(); expect(";"); return n; }
        if (!strcmp(k, "break")) { next(); expect(";"); return node(S_BREAK, t->line); }
        if (!strcmp(k, "continue")) { next(); expect(";"); return node(S_CONT, t->line); }
    }
    Node *e = parse_expr();
    if (is_op("=")) {
        next();
        Node *v = parse_expr(); expect(";");
        if (e->kind == E_VAR) { Node *n = node(S_ASSIGN, e->line); n->id = e->id; n->a = v; return n; }
        Node *n = node(S_IDXASSIGN, e->line); n->a = e->a; n->b = e->b; n->c = v; return n;
    }
    expect(";");
    Node *n = node(S_EXPR, e->line); n->a = e; return n;
}
static int op_index(Tok *t) {
    if (t->kind != K_OP) return -1;
    for (int i = 0; i < 11; i++) if (!strcmp(OPS[i], t->text)) return i;
    return -1;
}
static Node *bin(int op, Node *l, Node *r, int line) { Node *n = node(E_BIN, line); n->op = op; n->a = l; n->b = r; return n; }
static Node *parse_unary(void);
static Node *parse_mul(void) {
    Node *l = parse_unary();
    for (;;) { Tok *t = peek(); int o = op_index(t); if (o != OP_MUL && o != OP_DIV && o != OP_MOD) break; next(); l = bin(o, l, parse_unary(), t->line); }
    return l;
}
static Node *parse_add(void) {
    Node *l = parse_mul();
    for (;;) { Tok *t = peek(); int o = op_index(t); if (o != OP_ADD && o != OP_SUB) break; next(); l = bin(o, l, parse_mul(), t->line); }
    return l;
}
static Node *parse_cmp(void) {
    Node *l = parse_add();
    for (;;) { Tok *t = peek(); int o = op_index(t); if (o < OP_EQ) break; next(); l = bin(o, l, parse_add(), t->line); }
    return l;
}
static Node *parse_not(void) {
    if (is_kw("not")) { Tok *t = next(); Node *n = node(E_NOT, t->line); n->a = parse_not(); return n; }
    return parse_cmp();
}
static Node *parse_and(void) {
    Node *l = parse_not();
    while (is_kw("and")) { Tok *t = next(); Node *n = node(E_AND, t->line); n->a = l; n->b = parse_not(); l = n; }
    return l;
}
static Node *parse_expr(void) {
    Node *l = parse_and();
    while (is_kw("or")) { Tok *t = next(); Node *n = node(E_OR, t->line); n->a = l; n->b = parse_and(); l = n; }
    return l;
}
static void parse_list(const char *close, Node *n) {
    NVec v = { 0 };
    if (!is_op(close)) { nv_push(&v, parse_expr()); while (is_op(",")) { next(); nv_push(&v, parse_expr()); } }
    expect(close);
    n->list = v.d; n->nlist = v.n;
}
static Node *parse_primary(void) {
    Tok *t = next();
    Node *n;
    switch (t->kind) {
        case K_INT: n = node(E_LIT, t->line); n->lit = mkint(t->ival); return n;
        case K_STR: n = node(E_LIT, t->line); n->lit = mkstr(t->sval); return n;
        case K_ID: n = node(E_VAR, t->line); n->id = intern(t->text); return n;
        case K_KW:
            if (!strcmp(t->text, "true")) { n = node(E_LIT, t->line); n->lit = mkbool(1); return n; }
            if (!strcmp(t->text, "false")) { n = node(E_LIT, t->line); n->lit = mkbool(0); return n; }
            if (!strcmp(t->text, "nil")) { n = node(E_LIT, t->line); n->lit = NILV; return n; }
            if (!strcmp(t->text, "fn")) { n = node(E_FN, t->line); n->fn = parse_fn_rest(); return n; }
            break;
        case K_OP:
            if (!strcmp(t->text, "(")) { Node *e = parse_expr(); expect(")"); return e; }
            if (!strcmp(t->text, "[")) { n = node(E_LIST, t->line); parse_list("]", n); return n; }
            break;
    }
    fprintf(stderr, "parse error line %d at %s\n", t->line, t->text);
    exit(1);
}
static Node *parse_postfix(void) {
    Node *e = parse_primary();
    for (;;) {
        if (is_op("(")) { Tok *t = next(); Node *n = node(E_CALL, t->line); n->a = e; parse_list(")", n); e = n; }
        else if (is_op("[")) { Tok *t = next(); Node *n = node(E_INDEX, t->line); n->a = e; n->b = parse_expr(); expect("]"); e = n; }
        else break;
    }
    return e;
}
static Node *parse_unary(void) {
    if (is_op("-")) { Tok *t = next(); Node *n = node(E_NEG, t->line); n->a = parse_unary(); return n; }
    return parse_postfix();
}

/* ---------- runtime helpers ---------- */
static const char *type_name(Value v) {
    switch (v.t) {
        case T_NIL: return "nil"; case T_INT: return "int"; case T_BOOL: return "bool";
        case T_STR: return "string"; case T_LIST: return "list"; default: return "function";
    }
}
static inline int truthy(Value v) {
    switch (v.t) {
        case T_NIL: return 0; case T_BOOL: return v.u.b; case T_INT: return v.u.i != 0;
        case T_STR: return v.u.s->len != 0; case T_LIST: return v.u.l->len != 0; default: return 1;
    }
}
static int val_eq(Value a, Value b) {
    if (a.t != b.t) return 0;
    switch (a.t) {
        case T_NIL: return 1;
        case T_INT: return a.u.i == b.u.i;
        case T_BOOL: return a.u.b == b.u.b;
        case T_STR: return a.u.s->len == b.u.s->len && memcmp(a.u.s->data, b.u.s->data, (size_t)a.u.s->len) == 0;
        case T_LIST: {
            if (a.u.l == b.u.l) return 1;
            if (a.u.l->len != b.u.l->len) return 0;
            for (int64_t i = 0; i < a.u.l->len; i++) if (!val_eq(a.u.l->items[i], b.u.l->items[i])) return 0;
            return 1;
        }
        case T_FN: return a.u.f == b.u.f;
        case T_BUILTIN: return a.u.bi == b.u.bi;
    }
    return 0;
}
static List **dstack; static int dn, dcap;
static void display(Value v, Buf *b, int in_list) {
    char tmp[32];
    switch (v.t) {
        case T_NIL: buf_puts(b, "nil"); break;
        case T_INT: snprintf(tmp, sizeof tmp, "%lld", (long long)v.u.i); buf_puts(b, tmp); break;
        case T_BOOL: buf_puts(b, v.u.b ? "true" : "false"); break;
        case T_STR:
            if (!in_list) { buf_put(b, v.u.s->data, (size_t)v.u.s->len); break; }
            buf_putc(b, '"');
            for (int64_t i = 0; i < v.u.s->len; i++) {
                char c = v.u.s->data[i];
                if (c == '\\') buf_puts(b, "\\\\");
                else if (c == '"') buf_puts(b, "\\\"");
                else if (c == '\n') buf_puts(b, "\\n");
                else if (c == '\t') buf_puts(b, "\\t");
                else buf_putc(b, c);
            }
            buf_putc(b, '"');
            break;
        case T_LIST: {
            List *l = v.u.l;
            for (int i = 0; i < dn; i++) if (dstack[i] == l) { buf_puts(b, "[...]"); return; }
            if (dn == dcap) { dcap = dcap ? dcap * 2 : 16; dstack = realloc(dstack, sizeof(List *) * (size_t)dcap); }
            dstack[dn++] = l;
            buf_putc(b, '[');
            for (int64_t i = 0; i < l->len; i++) { if (i) buf_puts(b, ", "); display(l->items[i], b, 1); }
            buf_putc(b, ']');
            dn--;
            break;
        }
        default: buf_puts(b, "<fn>");
    }
}

static Str *chars[256];
static const char *BUILTINS[] = { "print", "str", "type", "len", "push", "pop", "range", "slice" };
static const int ARITY[] = { -1, 1, 1, 1, 2, 1, 2, 3 };
enum { NORMAL, BRK, CONT, RET };
static Value ret_val;

static Value eval(Node *e, Scope *s);
static int exec(Node *st, Scope *s);

static int run_in(Node **body, int n, Scope *sc) {
    for (int i = 0; i < n; i++) { int r = exec(body[i], sc); if (r != NORMAL) return r; }
    return NORMAL;
}

static Value call_value(Value f, Value *av, int n, int line) {
    if (f.t == T_FN) {
        Closure *c = f.u.f;
        FnDef *d = c->def;
        if (d->nparams != n) rt_error(line, "expected %d arguments, got %d", d->nparams, n);
        Scope *sc = scope_new(c->env);
        for (int i = 0; i < n; i++) scope_declare(sc, d->params[i], av[i]);
        Value result = NILV;
        for (int i = 0; i < d->nbody; i++) {
            if (exec(d->body[i], sc) == RET) { result = ret_val; ret_val = NILV; break; }
        }
        scope_decref(sc);
        return result;
    }
    if (f.t == T_BUILTIN) {
        int id = f.u.bi;
        if (ARITY[id] >= 0 && n != ARITY[id]) rt_error(line, "expected %d arguments, got %d", ARITY[id], n);
        switch (id) {
            case 0:
                for (int i = 0; i < n; i++) { if (i) buf_putc(&out, ' '); display(av[i], &out, 0); decref(av[i]); }
                buf_putc(&out, '\n');
                if (out.n > (1 << 16)) { fwrite(out.d, 1, out.n, stdout); out.n = 0; }
                return NILV;
            case 1: {
                Buf b = { 0 };
                display(av[0], &b, 0);
                Str *s = str_new(b.d, (int64_t)b.n); free(b.d);
                decref(av[0]);
                return mkstr(s);
            }
            case 2: {
                const char *t = type_name(av[0]);
                decref(av[0]);
                return mkstr(str_new(t, (int64_t)strlen(t)));
            }
            case 3:
                if (av[0].t == T_STR) { int64_t k = av[0].u.s->len; decref(av[0]); return mkint(k); }
                if (av[0].t == T_LIST) { int64_t k = av[0].u.l->len; decref(av[0]); return mkint(k); }
                break;
            case 4:
                if (av[0].t == T_LIST) { list_push(av[0].u.l, av[1]); decref(av[0]); return NILV; }
                break;
            case 5:
                if (av[0].t == T_LIST && av[0].u.l->len > 0) {
                    List *l = av[0].u.l;
                    Value v = l->items[--l->len];
                    decref(av[0]);
                    return v;
                }
                break;
            case 6:
                if (av[0].t == T_INT && av[1].t == T_INT) {
                    int64_t a = av[0].u.i, b = av[1].u.i;
                    List *l = list_new(b > a ? b - a : 0);
                    for (int64_t k = a; k < b; k++) list_push(l, mkint(k));
                    return mklist(l);
                }
                break;
            case 7:
                if (av[1].t == T_INT && av[2].t == T_INT) {
                    int64_t a = av[1].u.i, b = av[2].u.i;
                    if (av[0].t == T_STR && 0 <= a && a <= b && b <= av[0].u.s->len) {
                        Str *s = str_new(av[0].u.s->data + a, b - a);
                        decref(av[0]);
                        return mkstr(s);
                    }
                    if (av[0].t == T_LIST && 0 <= a && a <= b && b <= av[0].u.l->len) {
                        List *src = av[0].u.l;
                        List *l = list_new(b - a);
                        for (int64_t k = a; k < b; k++) { incref(src->items[k]); list_push(l, src->items[k]); }
                        decref(av[0]);
                        return mklist(l);
                    }
                }
                break;
        }
        rt_error(line, "bad argument to %s", BUILTINS[id]);
    }
    rt_error(line, "cannot call %s", type_name(f));
    return NILV;
}

static Value eval(Node *e, Scope *s) {
    switch (e->kind) {
        case E_LIT: incref(e->lit); return e->lit;
        case E_VAR: {
            Value *p = scope_lookup(s, e->id);
            if (!p) rt_error(e->line, "undefined variable %s", names[e->id]);
            incref(*p);
            return *p;
        }
        case E_LIST: {
            List *l = list_new(e->nlist);
            for (int i = 0; i < e->nlist; i++) list_push(l, eval(e->list[i], s));
            return mklist(l);
        }
        case E_FN: {
            Closure *c = malloc(sizeof(Closure));
            c->rc = 1; c->def = e->fn; c->env = s; s->rc++;
            Value v; v.t = T_FN; v.u.f = c; return v;
        }
        case E_CALL: {
            Value f = eval(e->a, s);
            int n = e->nlist;
            Value avs[8]; Value *av = n <= 8 ? avs : malloc(sizeof(Value) * (size_t)n);
            for (int i = 0; i < n; i++) av[i] = eval(e->list[i], s);
            Value r = call_value(f, av, n, e->line);
            if (av != avs) free(av);
            decref(f);
            return r;
        }
        case E_INDEX: {
            Value o = eval(e->a, s), i = eval(e->b, s);
            if (i.t == T_INT) {
                int64_t k = i.u.i;
                if (o.t == T_LIST) {
                    List *l = o.u.l;
                    if (k < 0 || k >= l->len) rt_error(e->line, "index %lld out of range for length %lld", (long long)k, (long long)l->len);
                    Value r = l->items[k]; incref(r); decref(o); return r;
                }
                if (o.t == T_STR) {
                    Str *st = o.u.s;
                    if (k < 0 || k >= st->len) rt_error(e->line, "index %lld out of range for length %lld", (long long)k, (long long)st->len);
                    Str *ch = chars[(unsigned char)st->data[k]];
                    ch->rc++;
                    decref(o);
                    return mkstr(ch);
                }
            }
            rt_error(e->line, "cannot index %s with %s", type_name(o), type_name(i));
            return NILV;
        }
        case E_AND: { Value a = eval(e->a, s); if (!truthy(a)) return a; decref(a); return eval(e->b, s); }
        case E_OR: { Value a = eval(e->a, s); if (truthy(a)) return a; decref(a); return eval(e->b, s); }
        case E_NOT: { Value a = eval(e->a, s); int t = truthy(a); decref(a); return mkbool(!t); }
        case E_NEG: {
            Value a = eval(e->a, s);
            if (a.t != T_INT) rt_error(e->line, "cannot apply - to %s", type_name(a));
            if (a.u.i == INT64_MIN) rt_error(e->line, "integer overflow");
            return mkint(-a.u.i);
        }
        case E_BIN: {
            Value a = eval(e->a, s), b = eval(e->b, s);
            int op = e->op;
            if (a.t == T_INT && b.t == T_INT) {
                int64_t x = a.u.i, y = b.u.i, z;
                switch (op) {
                    case OP_ADD: if (__builtin_add_overflow(x, y, &z)) rt_error(e->line, "integer overflow"); return mkint(z);
                    case OP_SUB: if (__builtin_sub_overflow(x, y, &z)) rt_error(e->line, "integer overflow"); return mkint(z);
                    case OP_MUL: if (__builtin_mul_overflow(x, y, &z)) rt_error(e->line, "integer overflow"); return mkint(z);
                    case OP_DIV:
                        if (y == 0) rt_error(e->line, "division by zero");
                        if (x == INT64_MIN && y == -1) rt_error(e->line, "integer overflow");
                        return mkint(x / y);
                    case OP_MOD:
                        if (y == 0) rt_error(e->line, "division by zero");
                        if (y == -1) return mkint(0);
                        return mkint(x % y);
                    case OP_EQ: return mkbool(x == y);
                    case OP_NE: return mkbool(x != y);
                    case OP_LT: return mkbool(x < y);
                    case OP_LE: return mkbool(x <= y);
                    case OP_GT: return mkbool(x > y);
                    default: return mkbool(x >= y);
                }
            }
            Value r;
            if (op == OP_EQ || op == OP_NE) {
                int eq = val_eq(a, b);
                r = mkbool(op == OP_EQ ? eq : !eq);
            } else if (op == OP_ADD && a.t == T_STR && b.t == T_STR) {
                Str *ns = str_new(NULL, a.u.s->len + b.u.s->len);
                memcpy(ns->data, a.u.s->data, (size_t)a.u.s->len);
                memcpy(ns->data + a.u.s->len, b.u.s->data, (size_t)b.u.s->len);
                r = mkstr(ns);
            } else if (op == OP_ADD && a.t == T_LIST && b.t == T_LIST) {
                List *x = a.u.l, *y = b.u.l;
                List *l = list_new(x->len + y->len);
                for (int64_t i = 0; i < x->len; i++) { incref(x->items[i]); list_push(l, x->items[i]); }
                for (int64_t i = 0; i < y->len; i++) { incref(y->items[i]); list_push(l, y->items[i]); }
                r = mklist(l);
            } else if (op >= OP_LT && a.t == T_STR && b.t == T_STR) {
                Str *x = a.u.s, *y = b.u.s;
                int64_t m = x->len < y->len ? x->len : y->len;
                int c = memcmp(x->data, y->data, (size_t)m);
                if (c == 0) c = x->len < y->len ? -1 : x->len > y->len ? 1 : 0;
                switch (op) {
                    case OP_LT: r = mkbool(c < 0); break;
                    case OP_LE: r = mkbool(c <= 0); break;
                    case OP_GT: r = mkbool(c > 0); break;
                    default: r = mkbool(c >= 0); break;
                }
            } else {
                rt_error(e->line, "cannot apply %s to %s and %s", OPS[op], type_name(a), type_name(b));
                return NILV;
            }
            decref(a); decref(b);
            return r;
        }
    }
    return NILV;
}

static int exec(Node *st, Scope *s) {
    switch (st->kind) {
        case S_LET: { Value v = eval(st->a, s); scope_declare(s, st->id, v); return NORMAL; }
        case S_FNDECL: {
            Closure *c = malloc(sizeof(Closure));
            c->rc = 1; c->def = st->fn; c->env = s; s->rc++;
            Value v; v.t = T_FN; v.u.f = c;
            scope_declare(s, st->id, v);
            return NORMAL;
        }
        case S_IF: {
            Value c = eval(st->a, s);
            int t = truthy(c); decref(c);
            if (t) return exec(st->b, s);
            if (st->c) return exec(st->c, s);
            return NORMAL;
        }
        case S_BLOCK: {
            Scope *sc = scope_new(s);
            int r = run_in(st->list, st->nlist, sc);
            scope_decref(sc);
            return r;
        }
        case S_WHILE:
            for (;;) {
                Value c = eval(st->a, s);
                int t = truthy(c); decref(c);
                if (!t) break;
                int r = exec(st->b, s);
                if (r == BRK) break;
                if (r == RET) return r;
            }
            return NORMAL;
        case S_FOR: {
            Value v = eval(st->a, s);
            if (v.t != T_LIST) rt_error(st->line, "cannot iterate %s", type_name(v));
            List *l = v.u.l;
            Node *body = st->b;
            int res = NORMAL;
            for (int64_t i = 0; i < l->len; i++) {
                Scope *sc = scope_new(s);
                Value item = l->items[i]; incref(item);
                scope_declare(sc, st->id, item);
                int r = run_in(body->list, body->nlist, sc);
                scope_decref(sc);
                if (r == BRK) break;
                if (r == RET) { res = RET; break; }
            }
            decref(v);
            return res;
        }
        case S_RETURN: ret_val = st->a ? eval(st->a, s) : NILV; return RET;
        case S_BREAK: return BRK;
        case S_CONT: return CONT;
        case S_ASSIGN: {
            Value v = eval(st->a, s);
            Value *p = scope_lookup(s, st->id);
            if (!p) rt_error(st->line, "undefined variable %s", names[st->id]);
            Value old = *p; *p = v; decref(old);
            return NORMAL;
        }
        case S_IDXASSIGN: {
            Value o = eval(st->a, s), i = eval(st->b, s), v = eval(st->c, s);
            if (o.t != T_LIST) rt_error(st->line, "cannot assign into %s", type_name(o));
            if (i.t != T_INT) rt_error(st->line, "cannot index list with %s", type_name(i));
            List *l = o.u.l; int64_t k = i.u.i;
            if (k < 0 || k >= l->len) rt_error(st->line, "index %lld out of range for length %lld", (long long)k, (long long)l->len);
            Value old = l->items[k]; l->items[k] = v; decref(old);
            decref(o);
            return NORMAL;
        }
        case S_EXPR: { Value v = eval(st->a, s); decref(v); return NORMAL; }
    }
    return NORMAL;
}

static char *g_dir;
static void *run(void *arg) {
    (void)arg;
    char path[4096];
    snprintf(path, sizeof path, "%s/program.tern", g_dir);
    FILE *f = fopen(path, "rb");
    Buf src = { 0 };
    if (f) {
        char tmp[65536]; size_t r;
        while ((r = fread(tmp, 1, sizeof tmp, f)) > 0) buf_put(&src, tmp, r);
        fclose(f);
    }
    lex(src.d ? src.d : "", src.n);
    for (int c = 0; c < 256; c++) { char ch = (char)c; chars[c] = str_new(&ch, 1); chars[c]->rc = 1L << 40; }
    Scope *builtins = scope_new(NULL);
    for (int i = 0; i < 8; i++) { Value v; v.t = T_BUILTIN; v.u.bi = i; scope_declare(builtins, intern(BUILTINS[i]), v); }
    NVec prog = { 0 };
    pos = 0;
    while (peek()->kind != K_EOF) nv_push(&prog, parse_stmt());
    Scope *global = scope_new(builtins);
    for (int i = 0; i < prog.n; i++) exec(prog.d[i], global);
    flush_out();
    exit(0);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    g_dir = argv[1];
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, (size_t)1 << 29);
    pthread_t th;
    pthread_create(&th, &attr, run, NULL);
    pthread_join(th, NULL);
    return 0;
}
