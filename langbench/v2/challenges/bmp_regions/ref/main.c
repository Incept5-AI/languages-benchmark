#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <dirent.h>

static int cmpstr(const void *a, const void *b) {
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

static uint32_t u32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint16_t u16(const unsigned char *p) { return (uint16_t)(p[0] | (p[1] << 8)); }

static int chan(uint32_t px, uint32_t mask) {
    if (mask == 0) return 0;
    int s = 0;
    while (!((mask >> s) & 1)) s++;
    int n = 0;
    while (s + n < 32 && ((mask >> (s + n)) & 1)) n++;
    uint64_t v = (px & mask) >> s;
    uint64_t m = (n >= 32) ? 0xFFFFFFFFull : ((1ull << n) - 1);
    return (int)((v * 255 + m / 2) / m);
}

static void process(const char *dir, const char *name) {
    char path[4096];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    printf("== %s\n", name);
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *d = malloc(len > 0 ? (size_t)len : 1);
    if (fread(d, 1, (size_t)len, f) != (size_t)len) { fclose(f); free(d); return; }
    fclose(f);

    uint32_t off = u32(d + 10);
    uint32_t hsz = u32(d + 14);
    int32_t w = (int32_t)u32(d + 18);
    int32_t hraw = (int32_t)u32(d + 22);
    int bpp = u16(d + 28);
    uint32_t comp = u32(d + 30);
    uint32_t clrUsed = u32(d + 46);

    if (comp != 0 && comp != 3) {
        printf("error: unsupported compression %u\n", comp);
        free(d);
        return;
    }
    int bottomUp = hraw > 0;
    long h = hraw < 0 ? -(long)hraw : hraw;
    long W = w;

    uint32_t rm = 0, gm = 0, bm = 0;
    if (comp == 3) {
        if (hsz >= 108) { rm = u32(d + 54); gm = u32(d + 58); bm = u32(d + 62); }
        else { const unsigned char *p = d + 14 + hsz; rm = u32(p); gm = u32(p + 4); bm = u32(p + 8); }
    }
    const unsigned char *pal = d + 14 + hsz;
    long ncol = 0;
    if (bpp <= 8) ncol = clrUsed ? (long)clrUsed : (1L << bpp);
    unsigned char *palInk = calloc(ncol > 0 ? ncol : 1, 1);
    for (long i = 0; i < ncol; i++) {
        const unsigned char *e = pal + 4 * i;
        int B = e[0], G = e[1], R = e[2];
        palInk[i] = (299 * R + 587 * G + 114 * B) / 1000 < 128;
    }

    long stride = ((W * bpp + 31) / 32) * 4;
    size_t N = (size_t)W * (size_t)h;
    unsigned char *ink = calloc(N ? N : 1, 1);
    long inkCount = 0;
    for (long y = 0; y < h; y++) {
        long srow = bottomUp ? (h - 1 - y) : y;
        const unsigned char *row = d + off + srow * stride;
        for (long x = 0; x < W; x++) {
            int is = 0;
            if (bpp <= 8) {
                int idx;
                if (bpp == 8) idx = row[x];
                else if (bpp == 4) idx = (row[x >> 1] >> ((x & 1) ? 0 : 4)) & 15;
                else idx = (row[x >> 3] >> (7 - (x & 7))) & 1;
                is = idx < ncol ? palInk[idx] : 0;
            } else {
                int R, G, B;
                if (bpp == 24) { const unsigned char *p = row + 3 * x; B = p[0]; G = p[1]; R = p[2]; }
                else if (comp == 0) { const unsigned char *p = row + 4 * x; B = p[0]; G = p[1]; R = p[2]; }
                else {
                    uint32_t px = u32(row + 4 * x);
                    R = chan(px, rm); G = chan(px, gm); B = chan(px, bm);
                }
                is = (299 * R + 587 * G + 114 * B) / 1000 < 128;
            }
            if (is) { ink[y * W + x] = 1; inkCount++; }
        }
    }
    printf("size %ldx%ld bpp %d header %u %s ink %ld\n", W, h, bpp, hsz, bottomUp ? "bottom-up" : "top-down", inkCount);

    /* components */
    long *stack = malloc((N ? N : 1) * sizeof(long));
    char *out = NULL; size_t outLen = 0, outCap = 0;
    long ncomp = 0;
    for (long y = 0; y < h; y++) for (long x = 0; x < W; x++) {
        size_t i0 = (size_t)(y * W + x);
        if (ink[i0] != 1) continue;
        ink[i0] = 2;
        long sp = 0; stack[sp++] = (long)i0;
        long minx = x, maxx = x, miny = y, maxy = y, cnt = 0;
        while (sp) {
            long i = stack[--sp];
            long cy = i / W, cx = i % W;
            cnt++;
            if (cx < minx) minx = cx; if (cx > maxx) maxx = cx;
            if (cy < miny) miny = cy; if (cy > maxy) maxy = cy;
            for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) {
                long nx = cx + dx, ny = cy + dy;
                if (nx < 0 || ny < 0 || nx >= W || ny >= h) continue;
                long j = ny * W + nx;
                if (ink[j] == 1) { ink[j] = 2; stack[sp++] = j; }
            }
        }
        ncomp++;
        char buf[160];
        int n = snprintf(buf, sizeof buf, "%ld,%ld bbox %ld,%ld-%ld,%ld pixels %ld\n", x, y, minx, miny, maxx, maxy, cnt);
        if (outLen + n + 1 > outCap) { outCap = (outCap + n + 1) * 2; out = realloc(out, outCap); }
        memcpy(out + outLen, buf, n); outLen += n;
    }
    printf("components %ld\n", ncomp);
    if (outLen) fwrite(out, 1, outLen, stdout);
    free(out); free(stack); free(ink); free(palInk); free(d);
}

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    DIR *dp = opendir(argv[1]);
    if (!dp) return 0;
    char **names = NULL; size_t n = 0, cap = 0;
    struct dirent *e;
    while ((e = readdir(dp))) {
        size_t l = strlen(e->d_name);
        if (l >= 4 && strcmp(e->d_name + l - 4, ".bmp") == 0) {
            if (n == cap) { cap = cap ? cap * 2 : 16; names = realloc(names, cap * sizeof *names); }
            names[n++] = strdup(e->d_name);
        }
    }
    closedir(dp);
    qsort(names, n, sizeof *names, cmpstr);
    for (size_t i = 0; i < n; i++) process(argv[1], names[i]);
    return 0;
}
