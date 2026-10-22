#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>

typedef long long ll;

typedef struct { ll off; int isdst; char abbr[64]; } TType;

typedef struct {
    char name[256];
    int timecnt, typecnt;
    ll *times;
    unsigned char *idx;
    TType *types;
    int hasFooter;
    TType fstd, fdst;
    int hasDst;
    int sm, sw, sd; ll stime;
    int em, ew, ed; ll etime;
} Zone;

static Zone zones[256];
static int nzones = 0;
static char dirpath[4096];

static ll floordiv(ll a, ll b) { ll q = a / b; if ((a % b != 0) && ((a < 0) != (b < 0))) q--; return q; }
static ll floormod(ll a, ll b) { return a - floordiv(a, b) * b; }

static ll days_from_civil(ll y, int m, int d) {
    y -= m <= 2;
    ll era = floordiv(y, 400);
    ll yoe = y - era * 400;
    ll doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    ll doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}
static void civil_from_days(ll z, ll *py, int *pm, int *pd) {
    z += 719468;
    ll era = floordiv(z, 146097);
    ll doe = z - era * 146097;
    ll yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    ll y = yoe + era * 400;
    ll doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    ll mp = (5 * doy + 2) / 153;
    int d = (int)(doy - (153 * mp + 2) / 5 + 1);
    int m = (int)(mp < 10 ? mp + 3 : mp - 9);
    *py = y + (m <= 2);
    *pm = m; *pd = d;
}
static int is_leap(ll y) { return (y % 4 == 0 && y % 100 != 0) || y % 400 == 0; }
static int month_len(ll y, int m) {
    static const int ml[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    if (m == 2 && is_leap(y)) return 29;
    return ml[m - 1];
}

static ll rd64(const unsigned char *p) { uint64_t v = 0; for (int i = 0; i < 8; i++) v = (v << 8) | p[i]; return (ll)(int64_t)v; }
static ll rd32(const unsigned char *p) { uint32_t v = 0; for (int i = 0; i < 4; i++) v = (v << 8) | p[i]; return (ll)(int32_t)v; }

/* footer parsing */
static const char *parse_name(const char *s, char *out) {
    int n = 0;
    if (*s == '<') {
        s++;
        while (*s && *s != '>') { if (n < 63) out[n++] = *s; s++; }
        if (*s == '>') s++;
    } else {
        while ((*s >= 'A' && *s <= 'Z') || (*s >= 'a' && *s <= 'z')) { if (n < 63) out[n++] = *s; s++; }
    }
    out[n] = 0;
    return s;
}
static const char *parse_hms(const char *s, ll *out) {
    int sign = 1;
    if (*s == '+') s++; else if (*s == '-') { sign = -1; s++; }
    ll h = 0, m = 0, sec = 0;
    while (*s >= '0' && *s <= '9') { h = h * 10 + (*s - '0'); s++; }
    if (*s == ':') { s++; while (*s >= '0' && *s <= '9') { m = m * 10 + (*s - '0'); s++; }
        if (*s == ':') { s++; while (*s >= '0' && *s <= '9') { sec = sec * 10 + (*s - '0'); s++; } } }
    *out = sign * (h * 3600 + m * 60 + sec);
    return s;
}
static const char *parse_rule(const char *s, int *m, int *w, int *d, ll *t) {
    *t = 7200;
    if (*s == 'M') {
        s++;
        *m = (int)strtol(s, (char **)&s, 10);
        if (*s == '.') s++;
        *w = (int)strtol(s, (char **)&s, 10);
        if (*s == '.') s++;
        *d = (int)strtol(s, (char **)&s, 10);
    }
    if (*s == '/') { s++; s = parse_hms(s, t); }
    return s;
}

static void parse_footer(Zone *z, const char *s) {
    z->hasFooter = 0; z->hasDst = 0;
    if (!*s) return;
    z->hasFooter = 1;
    ll o;
    s = parse_name(s, z->fstd.abbr);
    s = parse_hms(s, &o);
    z->fstd.off = -o; z->fstd.isdst = 0;
    if (*s && *s != ',') {
        s = parse_name(s, z->fdst.abbr);
        z->hasDst = 1; z->fdst.isdst = 1;
        if (*s && *s != ',') { s = parse_hms(s, &o); z->fdst.off = -o; }
        else z->fdst.off = z->fstd.off + 3600;
        z->sm = 3; z->sw = 2; z->sd = 0; z->stime = 7200;
        z->em = 11; z->ew = 1; z->ed = 0; z->etime = 7200;
        if (*s == ',') {
            s++;
            s = parse_rule(s, &z->sm, &z->sw, &z->sd, &z->stime);
            if (*s == ',') { s++; s = parse_rule(s, &z->em, &z->ew, &z->ed, &z->etime); }
        }
    }
}

static ll rule_local(ll y, int m, int w, int d, ll t) {
    ll first = days_from_civil(y, m, 1);
    int dow = (int)floormod(first + 4, 7);
    int day = 1 + (d - dow + 7) % 7 + (w - 1) * 7;
    int ml = month_len(y, m);
    while (day > ml) day -= 7;
    return (first + day - 1) * 86400 + t;
}

static ll year_of(ll t) { ll y; int m, d; civil_from_days(floordiv(t, 86400), &y, &m, &d); return y; }

/* footer events: start (dst=1) and end (dst=0) UTC times for year y */
static void footer_events(Zone *z, ll y, ll *st, ll *en) {
    *st = rule_local(y, z->sm, z->sw, z->sd, z->stime) - z->fstd.off;
    *en = rule_local(y, z->em, z->ew, z->ed, z->etime) - z->fdst.off;
}

static TType *footer_type(Zone *z, ll t) {
    if (!z->hasDst) return &z->fstd;
    ll Y = year_of(t);
    ll best = 0; int have = 0, bestdst = 0;
    for (ll y = Y - 2; y <= Y + 2; y++) {
        ll st, en;
        footer_events(z, y, &st, &en);
        if (st <= t && (!have || st > best)) { best = st; bestdst = 1; have = 1; }
        if (en <= t && (!have || en > best)) { best = en; bestdst = 0; have = 1; }
    }
    return bestdst ? &z->fdst : &z->fstd;
}

static TType *type_at(Zone *z, ll t) {
    if (z->timecnt == 0) {
        if (z->hasFooter) return footer_type(z, t);
        return &z->types[0];
    }
    if (t < z->times[0]) return &z->types[0];
    if (t > z->times[z->timecnt - 1]) {
        if (z->hasFooter) return footer_type(z, t);
        return &z->types[z->idx[z->timecnt - 1]];
    }
    int lo = 0, hi = z->timecnt - 1;
    while (lo < hi) { int mid = (lo + hi + 1) / 2; if (z->times[mid] <= t) lo = mid; else hi = mid - 1; }
    return &z->types[z->idx[lo]];
}

static Zone *load_zone(const char *name) {
    for (int i = 0; i < nzones; i++) if (strcmp(zones[i].name, name) == 0) return &zones[i];
    char path[8192];
    snprintf(path, sizeof path, "%s/%s", dirpath, name);
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *b = malloc(sz + 1);
    if (fread(b, 1, sz, f) != (size_t)sz) { fclose(f); free(b); return NULL; }
    fclose(f);
    b[sz] = 0;
    Zone *z = &zones[nzones++];
    memset(z, 0, sizeof *z);
    snprintf(z->name, sizeof z->name, "%s", name);
    const unsigned char *p = b;
    ll c[6]; for (int i = 0; i < 6; i++) c[i] = rd32(p + 20 + 4 * i);
    ll v1 = c[3] * 4 + c[3] + c[4] * 6 + c[5] + c[2] * 8 + c[1] + c[0];
    p += 44 + v1;
    for (int i = 0; i < 6; i++) c[i] = rd32(p + 20 + 4 * i);
    ll isut = c[0], isstd = c[1], leap = c[2], tc = c[3], ty = c[4], ch = c[5];
    p += 44;
    z->timecnt = (int)tc; z->typecnt = (int)ty;
    z->times = malloc(sizeof(ll) * (tc + 1));
    z->idx = malloc(tc + 1);
    z->types = malloc(sizeof(TType) * (ty + 1));
    for (ll i = 0; i < tc; i++) z->times[i] = rd64(p + 8 * i);
    p += 8 * tc;
    for (ll i = 0; i < tc; i++) z->idx[i] = p[i];
    p += tc;
    const unsigned char *tp = p;
    p += 6 * ty;
    const unsigned char *chars = p;
    for (ll i = 0; i < ty; i++) {
        z->types[i].off = rd32(tp + 6 * i);
        z->types[i].isdst = tp[6 * i + 4];
        int ai = tp[6 * i + 5];
        int n = 0;
        while (ai + n < ch && chars[ai + n] && n < 63) { z->types[i].abbr[n] = chars[ai + n]; n++; }
        z->types[i].abbr[n] = 0;
    }
    p += ch + leap * 12 + isstd + isut;
    char foot[1024]; int n = 0;
    if (p < b + sz && *p == '\n') {
        p++;
        while (p < b + sz && *p != '\n' && n < 1023) foot[n++] = *p++;
    }
    foot[n] = 0;
    parse_footer(z, foot);
    free(b);
    return z;
}

static void fmt_off(ll off, char *out) {
    char sign = off < 0 ? '-' : '+';
    ll a = off < 0 ? -off : off;
    if (a % 60) sprintf(out, "%c%02lld:%02lld:%02lld", sign, a / 3600, (a / 60) % 60, a % 60);
    else sprintf(out, "%c%02lld:%02lld", sign, a / 3600, (a / 60) % 60);
}
static void fmt_local(ll t, ll off, char *out) {
    ll l = t + off;
    ll days = floordiv(l, 86400), secs = floormod(l, 86400);
    ll y; int m, d; civil_from_days(days, &y, &m, &d);
    char o[32]; fmt_off(off, o);
    sprintf(out, "%04lld-%02d-%02dT%02lld:%02lld:%02lld%s", y, m, d, secs / 3600, (secs / 60) % 60, secs % 60, o);
}

static int cmpll(const void *a, const void *b) { ll x = *(const ll *)a, y = *(const ll *)b; return x < y ? -1 : x > y; }

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    snprintf(dirpath, sizeof dirpath, "%s", argv[1]);
    char qp[8192]; snprintf(qp, sizeof qp, "%s/queries.txt", dirpath);
    FILE *qf = fopen(qp, "r");
    if (!qf) return 0;
    static char line[4096];
    while (fgets(line, sizeof line, qf)) {
        size_t L = strlen(line);
        while (L && (line[L - 1] == '\n' || line[L - 1] == '\r')) line[--L] = 0;
        if (L == 0) continue;
        char kind[16], fname[1024], arg[256];
        if (sscanf(line, "%15s %1023s %255s", kind, fname, arg) != 3) continue;
        Zone *z = load_zone(fname);
        if (!z) continue;
        if (strcmp(kind, "UTC") == 0) {
            ll t = strtoll(arg, NULL, 10);
            TType *tt = type_at(z, t);
            char loc[128]; fmt_local(t, tt->off, loc);
            printf("%s -> %s %s\n", line, loc, tt->abbr);
        } else {
            ll Y; int M, D, h, mi, s;
            sscanf(arg, "%lld-%d-%dT%d:%d:%d", &Y, &M, &D, &h, &mi, &s);
            ll W = days_from_civil(Y, M, D) * 86400 + h * 3600 + mi * 60 + s;
            /* candidate offsets */
            ll offs[600]; int no = 0;
            for (int i = 0; i < z->typecnt; i++) offs[no++] = z->types[i].off;
            if (z->hasFooter) { offs[no++] = z->fstd.off; if (z->hasDst) offs[no++] = z->fdst.off; }
            ll minoff = offs[0], maxoff = offs[0];
            for (int i = 0; i < no; i++) { if (offs[i] < minoff) minoff = offs[i]; if (offs[i] > maxoff) maxoff = offs[i]; }
            ll res[600]; int nr = 0;
            for (int i = 0; i < no; i++) {
                ll t = W - offs[i];
                if (type_at(z, t)->off != offs[i]) continue;
                int dup = 0; for (int j = 0; j < nr; j++) if (res[j] == t) dup = 1;
                if (!dup) res[nr++] = t;
            }
            if (nr > 0) {
                qsort(res, nr, sizeof(ll), cmpll);
                printf("%s ->", line);
                for (int i = 0; i < nr; i++) {
                    TType *tt = type_at(z, res[i]);
                    char o[32]; fmt_off(tt->off, o);
                    printf("%s %lld %s %s", i ? " |" : "", res[i], o, tt->abbr);
                }
                printf("\n");
            } else {
                /* candidate transition times in window */
                ll lo = W - maxoff - 2, hi = W - minoff + 2;
                ll cands[4096]; int nc = 0;
                for (int i = 0; i < z->timecnt && nc < 4000; i++) if (z->times[i] >= lo && z->times[i] <= hi) cands[nc++] = z->times[i];
                ll lastT = z->timecnt ? z->times[z->timecnt - 1] : LLONG_MIN / 2;
                if (z->timecnt && lastT + 1 >= lo && lastT + 1 <= hi) cands[nc++] = lastT + 1;
                if (z->hasDst) {
                    ll y0 = year_of(lo), y1 = year_of(hi);
                    for (ll y = y0 - 1; y <= y1 + 1; y++) {
                        ll st, en; footer_events(z, y, &st, &en);
                        if (st > lastT && st >= lo && st <= hi) cands[nc++] = st;
                        if (en > lastT && en >= lo && en <= hi) cands[nc++] = en;
                    }
                }
                qsort(cands, nc, sizeof(ll), cmpll);
                ll t = W - type_at(z, W)->off; int found = 0;
                for (int i = 0; i < nc && !found; i++) {
                    ll T = cands[i];
                    ll ob = type_at(z, T - 1)->off, oa = type_at(z, T)->off;
                    if (T + ob <= W && W < T + oa) { t = W - ob; found = 1; }
                }
                TType *tt = type_at(z, t);
                char loc[128]; fmt_local(t, tt->off, loc);
                printf("%s -> GAP %lld %s %s\n", line, t, loc, tt->abbr);
            }
        }
    }
    fclose(qf);
    return 0;
}
