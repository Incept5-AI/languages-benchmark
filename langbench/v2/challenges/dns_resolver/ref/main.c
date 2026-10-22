#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdint.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

static size_t read_name(const unsigned char *m, size_t len, size_t pos, char *out) {
    size_t end = 0; int have_end = 0, jumps = 0, o = 0;
    out[0] = 0;
    while (pos < len) {
        unsigned l = m[pos];
        if ((l & 0xC0) == 0xC0) {
            if (pos + 1 >= len) break;
            if (!have_end) { end = pos + 2; have_end = 1; }
            if (++jumps > 255) break;
            pos = ((l & 0x3F) << 8) | m[pos + 1];
            continue;
        }
        if (l == 0) { if (!have_end) { end = pos + 1; have_end = 1; } break; }
        if (o > 0) out[o++] = '.';
        for (unsigned i = 0; i < l && pos + 1 + i < len; i++) {
            if (o < 1000) out[o++] = (char)tolower(m[pos + 1 + i]);
        }
        pos += 1 + l;
    }
    if (o == 0) { out[o++] = '.'; }
    out[o] = 0;
    return have_end ? end : len;
}

static unsigned u16at(const unsigned char *m, size_t p) { return ((unsigned)m[p] << 8) | m[p + 1]; }

static void aaaa(const unsigned char *d, char *out) {
    unsigned g[8]; for (int i = 0; i < 8; i++) g[i] = u16at(d, 2 * i);
    int bs = -1, bl = 0;
    for (int i = 0; i < 8;) {
        if (g[i] == 0) { int j = i; while (j < 8 && g[j] == 0) j++; if (j - i > bl) { bl = j - i; bs = i; } i = j; }
        else i++;
    }
    out[0] = 0;
    char tmp[16];
    if (bl < 2) {
        for (int i = 0; i < 8; i++) { sprintf(tmp, i ? ":%x" : "%x", g[i]); strcat(out, tmp); }
        return;
    }
    for (int i = 0; i < bs; i++) { sprintf(tmp, i ? ":%x" : "%x", g[i]); strcat(out, tmp); }
    strcat(out, "::");
    for (int i = bs + bl; i < 8; i++) { sprintf(tmp, i > bs + bl ? ":%x" : "%x", g[i]); strcat(out, tmp); }
}

static const char *tname(unsigned t, char *buf) {
    switch (t) {
    case 1: return "A"; case 5: return "CNAME"; case 15: return "MX"; case 16: return "TXT"; case 28: return "AAAA";
    }
    sprintf(buf, "TYPE%u", t); return buf;
}

static int read_full(int fd, unsigned char *b, size_t n) {
    size_t got = 0;
    while (got < n) { ssize_t r = read(fd, b + got, n - got); if (r <= 0) return -1; got += r; }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) return 0;
    char path[4096]; snprintf(path, sizeof path, "%s/queries.txt", argv[1]);
    int port = atoi(argv[2]);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    int us = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = htons(port); sa.sin_addr.s_addr = htonl(0x7f000001);
    char line[4096];
    static unsigned char buf[65536];
    static char nb[2048], nb2[2048];
    while (fgets(line, sizeof line, f)) {
        size_t L = strlen(line);
        while (L && (line[L - 1] == '\n' || line[L - 1] == '\r' || line[L - 1] == ' ' || line[L - 1] == '\t')) line[--L] = 0;
        char *s = line; while (*s == ' ' || *s == '\t') s++;
        if (!*s) continue;
        char name[2048], ty[64];
        if (sscanf(s, "%2047s %63s", name, ty) != 2) continue;
        unsigned qtype = 1;
        if (!strcmp(ty, "A")) qtype = 1; else if (!strcmp(ty, "CNAME")) qtype = 5; else if (!strcmp(ty, "MX")) qtype = 15;
        else if (!strcmp(ty, "TXT")) qtype = 16; else if (!strcmp(ty, "AAAA")) qtype = 28;
        size_t nl = strlen(name);
        if (nl && name[nl - 1] == '.') name[--nl] = 0;
        unsigned id = arc4random() & 0xffff;
        unsigned char q[600]; size_t ql = 0;
        q[ql++] = id >> 8; q[ql++] = id & 0xff; q[ql++] = 1; q[ql++] = 0; q[ql++] = 0; q[ql++] = 1;
        for (int i = 0; i < 6; i++) q[ql++] = 0;
        if (nl) {
            char *p = name;
            while (1) {
                char *dot = strchr(p, '.');
                size_t ll = dot ? (size_t)(dot - p) : strlen(p);
                q[ql++] = (unsigned char)ll; memcpy(q + ql, p, ll); ql += ll;
                if (!dot) break;
                p = dot + 1;
            }
        }
        q[ql++] = 0; q[ql++] = qtype >> 8; q[ql++] = qtype & 0xff; q[ql++] = 0; q[ql++] = 1;
        sendto(us, q, ql, 0, (struct sockaddr *)&sa, sizeof sa);
        unsigned char *msg = buf; size_t mlen;
        for (;;) {
            ssize_t n = recvfrom(us, buf, sizeof buf, 0, NULL, NULL);
            if (n < 12) continue;
            if (u16at(buf, 0) == id && (buf[2] & 0x80)) { mlen = n; break; }
        }
        if (buf[2] & 0x02) {
            int ts = socket(AF_INET, SOCK_STREAM, 0);
            connect(ts, (struct sockaddr *)&sa, sizeof sa);
            unsigned char fr[602]; fr[0] = ql >> 8; fr[1] = ql & 0xff; memcpy(fr + 2, q, ql);
            size_t w = 0; while (w < ql + 2) { ssize_t r = write(ts, fr + w, ql + 2 - w); if (r <= 0) break; w += r; }
            unsigned char lb[2]; read_full(ts, lb, 2);
            mlen = u16at(lb, 0);
            read_full(ts, buf, mlen);
            close(ts);
        }
        unsigned rc = msg[3] & 0x0f;
        const char *rn[] = {"NOERROR", "FORMERR", "SERVFAIL", "NXDOMAIN", "NOTIMP", "REFUSED"};
        for (size_t i = 0; i < nl; i++) name[i] = (char)tolower((unsigned char)name[i]);
        printf("%s %s ", nl ? name : ".", ty);
        if (rc <= 5) printf("%s\n", rn[rc]); else printf("RCODE%u\n", rc);
        unsigned qd = u16at(msg, 4), an = u16at(msg, 6);
        size_t pos = 12;
        for (unsigned i = 0; i < qd; i++) pos = read_name(msg, mlen, pos, nb) + 4;
        for (unsigned i = 0; i < an; i++) {
            pos = read_name(msg, mlen, pos, nb);
            unsigned t = u16at(msg, pos);
            uint32_t ttl = ((uint32_t)msg[pos + 4] << 24) | ((uint32_t)msg[pos + 5] << 16) | ((uint32_t)msg[pos + 6] << 8) | msg[pos + 7];
            unsigned rdl = u16at(msg, pos + 8);
            size_t rs = pos + 10;
            const unsigned char *rd = msg + rs;
            char tb[32];
            printf("  %s %u %s ", nb, ttl, tname(t, tb));
            if (t == 1 && rdl == 4) printf("%u.%u.%u.%u", rd[0], rd[1], rd[2], rd[3]);
            else if (t == 28 && rdl == 16) { char a[64]; aaaa(rd, a); printf("%s", a); }
            else if (t == 5) { read_name(msg, mlen, rs, nb2); printf("%s", nb2); }
            else if (t == 15) { read_name(msg, mlen, rs + 2, nb2); printf("%u %s", u16at(msg, rs), nb2); }
            else if (t == 16) {
                size_t p = 0; int first = 1;
                while (p < rdl) {
                    unsigned l = rd[p++];
                    if (!first) putchar(' ');
                    first = 0;
                    putchar('"');
                    for (unsigned k = 0; k < l && p < rdl; k++, p++) {
                        unsigned char b = rd[p];
                        if (b == '"') fputs("\\\"", stdout);
                        else if (b == '\\') fputs("\\\\", stdout);
                        else if (b >= 0x20 && b <= 0x7e) putchar(b);
                        else printf("\\%03u", b);
                    }
                    putchar('"');
                }
            } else {
                printf("\\# %u", rdl);
                if (rdl) { putchar(' '); for (unsigned k = 0; k < rdl; k++) printf("%02x", rd[k]); }
            }
            putchar('\n');
            pos = rs + rdl;
        }
    }
    fclose(f);
    return 0;
}
