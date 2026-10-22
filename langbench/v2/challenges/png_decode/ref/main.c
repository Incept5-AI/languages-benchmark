#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <dirent.h>

static uint32_t crctab[256];
static void crc_init(void) {
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crctab[n] = c;
    }
}
static uint32_t crc_update(uint32_t c, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) c = crctab[(c ^ b[i]) & 0xff] ^ (c >> 8);
    return c;
}
static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

/* ---------- inflate ---------- */
typedef struct {
    const uint8_t *in; size_t inlen; size_t pos; uint32_t bitbuf; int bitcnt;
    uint8_t *out; size_t outlen, outcap;
    int nstored, nfixed, ndynamic;
} Inf;

static int getbit(Inf *s) {
    if (s->bitcnt == 0) {
        s->bitbuf = s->pos < s->inlen ? s->in[s->pos] : 0;
        s->pos++;
        s->bitcnt = 8;
    }
    int b = s->bitbuf & 1; s->bitbuf >>= 1; s->bitcnt--;
    return b;
}
static uint32_t getbits(Inf *s, int n) {
    uint32_t v = 0;
    for (int i = 0; i < n; i++) v |= (uint32_t)getbit(s) << i;
    return v;
}
static void putb(Inf *s, uint8_t b) {
    if (s->outlen == s->outcap) {
        s->outcap = s->outcap ? s->outcap * 2 : 65536;
        s->out = realloc(s->out, s->outcap);
    }
    s->out[s->outlen++] = b;
}

typedef struct { short count[16]; short sym[320]; } Huff;
static void build(Huff *h, const uint8_t *len, int n) {
    memset(h->count, 0, sizeof h->count);
    for (int i = 0; i < n; i++) h->count[len[i]]++;
    h->count[0] = 0;
    short offs[16]; offs[1] = 0;
    for (int i = 1; i < 15; i++) offs[i + 1] = offs[i] + h->count[i];
    for (int i = 0; i < n; i++) if (len[i]) h->sym[offs[len[i]]++] = (short)i;
}
static int decode(Inf *s, Huff *h) {
    int code = 0, first = 0, index = 0;
    for (int len = 1; len < 16; len++) {
        code |= getbit(s);
        int count = h->count[len];
        if (code - count < first) return h->sym[index + (code - first)];
        index += count; first += count; first <<= 1; code <<= 1;
    }
    return -1;
}
static const short lbase[] = {3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
static const short lext[] = {0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
static const short dbase[] = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577};
static const short dext[] = {0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13};

static int codes(Inf *s, Huff *lc, Huff *dc) {
    for (;;) {
        int sym = decode(s, lc);
        if (sym < 0) return -1;
        if (sym < 256) putb(s, (uint8_t)sym);
        else if (sym == 256) return 0;
        else {
            sym -= 257;
            if (sym >= 29) return -1;
            int len = lbase[sym] + getbits(s, lext[sym]);
            int d = decode(s, dc);
            if (d < 0 || d >= 30) return -1;
            size_t dist = dbase[d] + getbits(s, dext[d]);
            if (dist > s->outlen) return -1;
            for (int i = 0; i < len; i++) putb(s, s->out[s->outlen - dist]);
        }
    }
}

static int inflate_stream(Inf *s) {
    int last;
    do {
        last = getbit(s);
        int type = getbits(s, 2);
        if (type == 0) {
            s->nstored++;
            s->bitcnt = 0;
            if (s->pos + 4 > s->inlen) return -1;
            unsigned len = s->in[s->pos] | (s->in[s->pos + 1] << 8);
            s->pos += 4;
            if (s->pos + len > s->inlen) return -1;
            for (unsigned i = 0; i < len; i++) putb(s, s->in[s->pos + i]);
            s->pos += len;
        } else if (type == 1) {
            s->nfixed++;
            uint8_t l[320];
            Huff lc, dc;
            int i;
            for (i = 0; i < 144; i++) l[i] = 8;
            for (; i < 256; i++) l[i] = 9;
            for (; i < 280; i++) l[i] = 7;
            for (; i < 288; i++) l[i] = 8;
            build(&lc, l, 288);
            for (i = 0; i < 30; i++) l[i] = 5;
            build(&dc, l, 30);
            if (codes(s, &lc, &dc)) return -1;
        } else if (type == 2) {
            s->ndynamic++;
            int nlen = getbits(s, 5) + 257, ndist = getbits(s, 5) + 1, ncode = getbits(s, 4) + 4;
            static const uint8_t ord[19] = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};
            uint8_t lens[320]; memset(lens, 0, sizeof lens);
            for (int i = 0; i < ncode; i++) lens[ord[i]] = (uint8_t)getbits(s, 3);
            Huff lc, dc;
            build(&lc, lens, 19);
            int idx = 0;
            while (idx < nlen + ndist) {
                int sym = decode(s, &lc);
                if (sym < 0) return -1;
                if (sym < 16) lens[idx++] = (uint8_t)sym;
                else {
                    int rep, val = 0;
                    if (sym == 16) { if (idx == 0) return -1; val = lens[idx - 1]; rep = 3 + getbits(s, 2); }
                    else if (sym == 17) rep = 3 + getbits(s, 3);
                    else rep = 11 + getbits(s, 7);
                    if (idx + rep > nlen + ndist) return -1;
                    while (rep--) lens[idx++] = (uint8_t)val;
                }
            }
            build(&lc, lens, nlen);
            build(&dc, lens + nlen, ndist);
            if (codes(s, &lc, &dc)) return -1;
        } else return -1;
    } while (!last);
    return 0;
}

static int cmpstr(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

static int paeth(int a, int b, int c) {
    int p = a + b - c, pa = abs(p - a), pb = abs(p - b), pc = abs(p - c);
    if (pa <= pb && pa <= pc) return a;
    if (pb <= pc) return b;
    return c;
}

static void process(const char *dir, const char *name) {
    char path[4096];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "rb");
    printf("== %s\n", name);
    if (!f) return;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc(sz + 1);
    fread(buf, 1, sz, f); fclose(f);

    /* pass 1: CRC */
    size_t p = 8; int n = 0;
    while (p + 12 <= (size_t)sz) {
        uint32_t len = be32(buf + p);
        n++;
        uint32_t c = crc_update(0xffffffffu, buf + p + 4, len + 4) ^ 0xffffffffu;
        if (c != be32(buf + p + 8 + len)) {
            printf("error: CRC mismatch in chunk %d %.4s\n", n, (char *)buf + p + 4);
            free(buf); return;
        }
        p += 12 + len;
    }

    uint32_t W = 0, H = 0; int depth = 0, ctype = 0;
    uint8_t pal[256 * 3]; int npal = 0;
    uint8_t trns[256]; int ntrns = 0; int hastrns = 0;
    uint8_t *idat = NULL; size_t idlen = 0;
    char chunkline[65536]; size_t cl = 0; chunkline[0] = 0;
    char prev[5] = ""; int run = 0;
    p = 8;
    while (p + 12 <= (size_t)sz) {
        uint32_t len = be32(buf + p);
        char type[5]; memcpy(type, buf + p + 4, 4); type[4] = 0;
        const uint8_t *d = buf + p + 8;
        if (run > 0 && strcmp(type, prev) == 0) run++;
        else {
            if (run > 0) {
                cl += snprintf(chunkline + cl, sizeof chunkline - cl, "%s%s", cl ? " " : "", prev);
                if (run > 1) cl += snprintf(chunkline + cl, sizeof chunkline - cl, "*%d", run);
            }
            strcpy(prev, type); run = 1;
        }
        if (!strcmp(type, "IHDR")) {
            W = be32(d); H = be32(d + 4); depth = d[8]; ctype = d[9];
        } else if (!strcmp(type, "PLTE")) {
            npal = len / 3; memcpy(pal, d, npal * 3);
        } else if (!strcmp(type, "tRNS")) {
            hastrns = 1; ntrns = len > 256 ? 256 : len; memcpy(trns, d, ntrns);
        } else if (!strcmp(type, "IDAT")) {
            idat = realloc(idat, idlen + len + 1);
            memcpy(idat + idlen, d, len); idlen += len;
        }
        p += 12 + len;
    }
    if (run > 0) {
        cl += snprintf(chunkline + cl, sizeof chunkline - cl, "%s%s", cl ? " " : "", prev);
        if (run > 1) cl += snprintf(chunkline + cl, sizeof chunkline - cl, "*%d", run);
    }

    Inf s; memset(&s, 0, sizeof s);
    s.in = idat; s.inlen = idlen; s.pos = 2;
    inflate_stream(&s);
    s.bitcnt = 0;
    uint32_t a1 = 1, a2 = 0;
    for (size_t i = 0; i < s.outlen; i++) { a1 = (a1 + s.out[i]) % 65521; a2 = (a2 + a1) % 65521; }
    uint32_t adl = (a2 << 16) | a1;
    uint32_t want = s.pos + 4 <= idlen ? be32(idat + s.pos) : ~adl;
    if (adl != want) {
        printf("error: Adler-32 mismatch\n");
        free(buf); free(idat); free(s.out); return;
    }

    int chans = ctype == 0 ? 1 : ctype == 2 ? 3 : ctype == 3 ? 1 : ctype == 4 ? 2 : 4;
    int bpp_bits = chans * depth;
    size_t stride = ((size_t)W * bpp_bits + 7) / 8;
    int bpp = (bpp_bits + 7) / 8;
    uint8_t *prow = calloc(stride + 1, 1), *cur = calloc(stride + 1, 1);
    long fc[5] = {0};
    uint64_t sum[4] = {0};
    uint32_t crc = 0xffffffffu;
    uint8_t *rgba = malloc((size_t)W * 4 + 4);
    size_t ip = 0;
    int maxv = (1 << depth) - 1;
    for (uint32_t y = 0; y < H; y++) {
        int ft = ip < s.outlen ? s.out[ip] : 0; ip++;
        if (ft >= 0 && ft < 5) fc[ft]++;
        for (size_t i = 0; i < stride; i++) cur[i] = ip + i < s.outlen ? s.out[ip + i] : 0;
        ip += stride;
        for (size_t i = 0; i < stride; i++) {
            int a = i >= (size_t)bpp ? cur[i - bpp] : 0;
            int b = prow[i];
            int c = i >= (size_t)bpp ? prow[i - bpp] : 0;
            int x = cur[i];
            switch (ft) {
                case 1: x += a; break;
                case 2: x += b; break;
                case 3: x += (a + b) / 2; break;
                case 4: x += paeth(a, b, c); break;
            }
            cur[i] = (uint8_t)x;
        }
        for (uint32_t x = 0; x < W; x++) {
            unsigned v[4];
            for (int ch = 0; ch < chans; ch++) {
                if (depth == 16) {
                    size_t o = ((size_t)x * chans + ch) * 2;
                    v[ch] = (cur[o] << 8) | cur[o + 1];
                } else if (depth == 8) {
                    v[ch] = cur[(size_t)x * chans + ch];
                } else {
                    size_t bit = ((size_t)x * chans + ch) * depth;
                    v[ch] = (cur[bit / 8] >> (8 - depth - bit % 8)) & maxv;
                }
            }
            #define CV(t) (depth == 16 ? (t) >> 8 : depth == 8 ? (t) : (t) * 255 / maxv)
            uint8_t R, G, B, A = 255;
            if (ctype == 3) {
                unsigned i = v[0];
                R = i < (unsigned)npal ? pal[i * 3] : 0;
                G = i < (unsigned)npal ? pal[i * 3 + 1] : 0;
                B = i < (unsigned)npal ? pal[i * 3 + 2] : 0;
                A = i < (unsigned)ntrns ? trns[i] : 255;
            } else if (ctype == 0) {
                R = G = B = CV(v[0]);
                if (hastrns && ntrns >= 2 && v[0] == (unsigned)((trns[0] << 8) | trns[1])) A = 0;
            } else if (ctype == 2) {
                R = CV(v[0]); G = CV(v[1]); B = CV(v[2]);
                if (hastrns && ntrns >= 6 &&
                    v[0] == (unsigned)((trns[0] << 8) | trns[1]) &&
                    v[1] == (unsigned)((trns[2] << 8) | trns[3]) &&
                    v[2] == (unsigned)((trns[4] << 8) | trns[5])) A = 0;
            } else if (ctype == 4) {
                R = G = B = CV(v[0]); A = CV(v[1]);
            } else {
                R = CV(v[0]); G = CV(v[1]); B = CV(v[2]); A = CV(v[3]);
            }
            uint8_t *q = rgba + (size_t)x * 4;
            q[0] = R; q[1] = G; q[2] = B; q[3] = A;
            sum[0] += R; sum[1] += G; sum[2] += B; sum[3] += A;
        }
        crc = crc_update(crc, rgba, (size_t)W * 4);
        uint8_t *t = prow; prow = cur; cur = t;
    }
    printf("IHDR %ux%u depth=%d color=%d\n", W, H, depth, ctype);
    printf("chunks %s\n", chunkline);
    printf("deflate stored=%d fixed=%d dynamic=%d\n", s.nstored, s.nfixed, s.ndynamic);
    printf("filters none=%ld sub=%ld up=%ld average=%ld paeth=%ld\n", fc[0], fc[1], fc[2], fc[3], fc[4]);
    printf("rgba sum=%llu,%llu,%llu,%llu crc32=%08x\n",
           (unsigned long long)sum[0], (unsigned long long)sum[1], (unsigned long long)sum[2],
           (unsigned long long)sum[3], crc ^ 0xffffffffu);
    free(buf); free(idat); free(s.out); free(prow); free(cur); free(rgba);
}

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    crc_init();
    DIR *d = opendir(argv[1]);
    if (!d) return 0;
    char **names = NULL; size_t nn = 0, cap = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        size_t l = strlen(e->d_name);
        if (l >= 4 && !strcmp(e->d_name + l - 4, ".png")) {
            if (nn == cap) { cap = cap ? cap * 2 : 64; names = realloc(names, cap * sizeof *names); }
            names[nn++] = strdup(e->d_name);
        }
    }
    closedir(d);
    qsort(names, nn, sizeof *names, cmpstr);
    for (size_t i = 0; i < nn; i++) process(argv[1], names[i]);
    return 0;
}
