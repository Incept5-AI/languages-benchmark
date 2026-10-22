#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <dirent.h>
#include <stdint.h>

static int cmpstr(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
static unsigned u16(const unsigned char *p) { return p[0] | (p[1] << 8); }
static uint32_t u32(const unsigned char *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }

static const double LOWF[4] = {697, 770, 852, 941};
static const double HIGHF[4] = {1209, 1336, 1477, 1633};
static const char KEYS[4][4] = {{'1','2','3','A'},{'4','5','6','B'},{'7','8','9','C'},{'*','0','#','D'}};

static void process(const char *dir, const char *name) {
    char path[4096];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc(len > 0 ? len : 1);
    if (len > 0 && fread(buf, 1, len, f) != (size_t)len) { fclose(f); free(buf); return; }
    fclose(f);
    const unsigned char *fmt = NULL, *data = NULL;
    size_t fmtn = 0, datan = 0;
    size_t pos = 12;
    while (pos + 8 <= (size_t)len) {
        uint32_t n = u32(buf + pos + 4);
        const unsigned char *body = buf + pos + 8;
        size_t avail = (size_t)len - pos - 8;
        size_t bn = n < avail ? n : avail;
        if (!memcmp(buf + pos, "fmt ", 4)) { fmt = body; fmtn = bn; }
        else if (!memcmp(buf + pos, "data", 4)) { data = body; datan = bn; }
        pos += 8 + (size_t)n + (n & 1);
    }
    if (!fmt || fmtn < 16) { printf("%s: unsupported\n", name); free(buf); return; }
    unsigned tag = u16(fmt), ch = u16(fmt + 2), bits = u16(fmt + 14);
    uint32_t rate = u32(fmt + 4);
    unsigned eff = tag;
    if (tag == 0xFFFE) eff = fmtn >= 26 ? u16(fmt + 24) : 0;
    if (eff != 1 || bits != 16) { printf("%s: unsupported\n", name); free(buf); return; }
    size_t frames = datan / (2 * ch);
    int N = rate / 50;
    size_t nb = frames / N;
    printf("%s: %u Hz, %u ch, %zu blocks\n", name, rate, ch, nb);
    char *lab = calloc(nb + 1, 1), *br = calloc(nb + 1, 1);
    double *x = malloc(sizeof(double) * N);
    for (size_t b = 0; b < nb; b++) {
        double E = 0;
        for (int i = 0; i < N; i++) {
            size_t fr = b * N + i;
            int16_t s = (int16_t)u16(data + fr * 2 * ch);
            x[i] = s;
            E += x[i] * x[i];
        }
        lab[b] = 0;
        if (E < 1000000.0 * N) continue;
        double rl[4], rh[4];
        for (int k = 0; k < 8; k++) {
            double fq = k < 4 ? LOWF[k] : HIGHF[k - 4];
            double c = 2 * cos(2 * M_PI * fq / rate), s1 = 0, s2 = 0;
            for (int i = 0; i < N; i++) { double s = x[i] + c * s1 - s2; s2 = s1; s1 = s; }
            double P = s1 * s1 + s2 * s2 - c * s1 * s2;
            double r = 2 * P / (N * E);
            if (k < 4) rl[k] = r; else rh[k - 4] = r;
        }
        int bl = 0, bh = 0;
        for (int k = 1; k < 4; k++) { if (rl[k] > rl[bl]) bl = k; if (rh[k] > rh[bh]) bh = k; }
        if (rl[bl] >= 0.1 && rh[bh] >= 0.1 && rl[bl] + rh[bh] >= 0.6) lab[b] = KEYS[bl][bh];
    }
    for (size_t b = 0; b < nb; b++) {
        br[b] = lab[b];
        if (!lab[b] && b > 0 && b + 1 < nb && lab[b - 1] && lab[b - 1] == lab[b + 1]) br[b] = lab[b - 1];
    }
    char digits[100000]; size_t nd = 0;
    size_t i = 0;
    while (i < nb) {
        size_t j = i;
        while (j + 1 < nb && br[j + 1] == br[i]) j++;
        if (br[i] && j - i + 1 >= 2) {
            printf("%c %zu %zu\n", br[i], i * 20, (j + 1) * 20);
            if (nd < sizeof digits - 1) digits[nd++] = br[i];
        }
        i = j + 1;
    }
    digits[nd] = 0;
    printf("digits: %s\n", digits);
    free(lab); free(br); free(x); free(buf);
}

int main(int argc, char **argv) {
    if (argc < 2) return 0;
    DIR *d = opendir(argv[1]);
    if (!d) return 0;
    char **names = NULL; size_t cnt = 0, cap = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        size_t l = strlen(e->d_name);
        if (l >= 4 && !strcmp(e->d_name + l - 4, ".wav")) {
            if (cnt == cap) { cap = cap ? cap * 2 : 16; names = realloc(names, cap * sizeof *names); }
            names[cnt++] = strdup(e->d_name);
        }
    }
    closedir(d);
    qsort(names, cnt, sizeof *names, cmpstr);
    for (size_t k = 0; k < cnt; k++) {
        if (k) printf("\n");
        process(argv[1], names[k]);
    }
    return 0;
}
