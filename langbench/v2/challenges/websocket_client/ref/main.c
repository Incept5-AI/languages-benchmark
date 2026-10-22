#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <strings.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <signal.h>
#include <CommonCrypto/CommonDigest.h>

static int fd;
static unsigned char rbuf[65536];
static size_t rpos = 0, rlen = 0;

static int fill(void) {
    if (rpos < rlen) return 1;
    ssize_t n = read(fd, rbuf, sizeof rbuf);
    if (n <= 0) return 0;
    rpos = 0; rlen = (size_t)n;
    return 1;
}

static int get_byte(void) {
    if (!fill()) return -1;
    return rbuf[rpos++];
}

static int read_exact(unsigned char *dst, size_t n) {
    while (n > 0) {
        if (!fill()) return 0;
        size_t k = rlen - rpos;
        if (k > n) k = n;
        memcpy(dst, rbuf + rpos, k);
        rpos += k; dst += k; n -= k;
    }
    return 1;
}

static int write_all(const unsigned char *p, size_t n) {
    while (n > 0) {
        ssize_t w = write(fd, p, n);
        if (w <= 0) return 0;
        p += w; n -= (size_t)w;
    }
    return 1;
}

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static void b64(const unsigned char *in, size_t n, char *out) {
    size_t i = 0, o = 0;
    while (i + 2 < n) {
        uint32_t v = (in[i] << 16) | (in[i + 1] << 8) | in[i + 2];
        out[o++] = B64[(v >> 18) & 63]; out[o++] = B64[(v >> 12) & 63];
        out[o++] = B64[(v >> 6) & 63]; out[o++] = B64[v & 63];
        i += 3;
    }
    if (n - i == 1) {
        uint32_t v = in[i] << 16;
        out[o++] = B64[(v >> 18) & 63]; out[o++] = B64[(v >> 12) & 63];
        out[o++] = '='; out[o++] = '=';
    } else if (n - i == 2) {
        uint32_t v = (in[i] << 16) | (in[i + 1] << 8);
        out[o++] = B64[(v >> 18) & 63]; out[o++] = B64[(v >> 12) & 63];
        out[o++] = B64[(v >> 6) & 63]; out[o++] = '=';
    }
    out[o] = 0;
}

static int send_frame(int op, const unsigned char *payload, size_t len) {
    size_t hl = 2 + (len <= 125 ? 0 : len <= 65535 ? 2 : 8) + 4;
    unsigned char *f = malloc(hl + len + 1);
    size_t p;
    f[0] = (unsigned char)(0x80 | op);
    if (len <= 125) { f[1] = (unsigned char)(0x80 | len); p = 2; }
    else if (len <= 65535) { f[1] = 0x80 | 126; f[2] = (unsigned char)(len >> 8); f[3] = (unsigned char)len; p = 4; }
    else {
        f[1] = 0x80 | 127;
        uint64_t l = len;
        for (int i = 0; i < 8; i++) f[2 + i] = (unsigned char)(l >> (56 - 8 * i));
        p = 10;
    }
    unsigned char mk[4];
    do { arc4random_buf(mk, 4); } while (!mk[0] && !mk[1] && !mk[2] && !mk[3]);
    memcpy(f + p, mk, 4); p += 4;
    for (size_t i = 0; i < len; i++) f[p + i] = payload[i] ^ mk[i & 3];
    int ok = write_all(f, p + len);
    free(f);
    return ok;
}

static void sha1hex(const unsigned char *d, size_t n, char *out) {
    unsigned char h[20];
    CC_SHA1_CTX c;
    CC_SHA1_Init(&c);
    size_t off = 0;
    while (off < n) {
        size_t k = n - off; if (k > 0x40000000) k = 0x40000000;
        CC_SHA1_Update(&c, d + off, (CC_LONG)k);
        off += k;
    }
    CC_SHA1_Final(h, &c);
    for (int i = 0; i < 20; i++) sprintf(out + 2 * i, "%02x", h[i]);
    out[40] = 0;
}

/* decode UTF-8 to code points (lenient); returns count */
static size_t utf8_split(const unsigned char *s, size_t n, size_t *starts) {
    size_t i = 0, c = 0;
    while (i < n) {
        starts[c++] = i;
        unsigned char b = s[i];
        size_t l = 1;
        if (b >= 0xF0) l = 4; else if (b >= 0xE0) l = 3; else if (b >= 0xC0) l = 2;
        i += l;
        if (i > n) i = n;
    }
    starts[c] = n;
    return c;
}

static void session(const char *url) {
    const char *rest = url;
    if (strncmp(rest, "ws://", 5) == 0) rest += 5;
    const char *slash = strchr(rest, '/');
    char hostport[256];
    size_t hl = slash ? (size_t)(slash - rest) : strlen(rest);
    if (hl >= sizeof hostport) hl = sizeof hostport - 1;
    memcpy(hostport, rest, hl); hostport[hl] = 0;
    const char *target = slash ? slash : "/";
    char host[256]; int port = 80;
    char *colon = strrchr(hostport, ':');
    if (colon) { memcpy(host, hostport, (size_t)(colon - hostport)); host[colon - hostport] = 0; port = atoi(colon + 1); }
    else strcpy(host, hostport);

    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return;
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)port);
    inet_pton(AF_INET, host, &sa.sin_addr);
    if (connect(fd, (struct sockaddr *)&sa, sizeof sa) < 0) { close(fd); return; }
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    rpos = rlen = 0;

    unsigned char kb[16]; arc4random_buf(kb, 16);
    char key[32]; b64(kb, 16, key);
    size_t reqcap = strlen(target) + strlen(hostport) + 512;
    char *req = malloc(reqcap);
    int rl = snprintf(req, reqcap,
        "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n", target, hostport, key);
    write_all((unsigned char *)req, (size_t)rl);
    free(req);

    size_t cap = 1024, hn = 0;
    char *head = malloc(cap);
    for (;;) {
        int c = get_byte();
        if (c < 0) { free(head); close(fd); return; }
        if (hn + 2 >= cap) { cap *= 2; head = realloc(head, cap); }
        head[hn++] = (char)c;
        if (hn >= 4 && memcmp(head + hn - 4, "\r\n\r\n", 4) == 0) break;
        if (hn >= 2 && head[hn - 2] == '\n' && head[hn - 1] == '\n') break;
    }
    head[hn] = 0;
    /* status line */
    char *eol = strchr(head, '\n');
    *eol = 0;
    if (eol > head && eol[-1] == '\r') eol[-1] = 0;
    char *sp1 = strchr(head, ' ');
    char code[32] = ""; const char *reason = "";
    if (sp1) {
        sp1++;
        char *sp2 = strchr(sp1, ' ');
        size_t cl = sp2 ? (size_t)(sp2 - sp1) : strlen(sp1);
        if (cl > 31) cl = 31;
        memcpy(code, sp1, cl); code[cl] = 0;
        if (sp2) reason = sp2 + 1;
    }
    if (strcmp(code, "101") != 0) {
        printf("handshake failed: %s %s\n", code, reason);
        free(head); close(fd); return;
    }
    char *accept = NULL;
    char *line = eol + 1;
    while (*line) {
        char *e = strchr(line, '\n');
        if (e) *e = 0;
        size_t ll = strlen(line);
        if (ll && line[ll - 1] == '\r') line[--ll] = 0;
        char *c = strchr(line, ':');
        if (c) {
            char *ne = c;
            while (ne > line && (ne[-1] == ' ' || ne[-1] == '\t')) ne--;
            if ((size_t)(ne - line) == 20 && strncasecmp(line, "Sec-WebSocket-Accept", 20) == 0) {
                char *v = c + 1;
                while (*v == ' ' || *v == '\t') v++;
                char *ve = v + strlen(v);
                while (ve > v && (ve[-1] == ' ' || ve[-1] == '\t')) *--ve = 0;
                accept = v;
            }
        }
        if (!e) break;
        line = e + 1;
    }
    char kg[128]; snprintf(kg, sizeof kg, "%s258EAFA5-E914-47DA-95CA-C5AB0DC85B11", key);
    unsigned char dg[20]; CC_SHA1(kg, (CC_LONG)strlen(kg), dg);
    char expect[64]; b64(dg, 20, expect);
    if (!accept || strcmp(accept, expect) != 0) {
        printf("handshake failed: bad Sec-WebSocket-Accept\n");
        free(head); close(fd); return;
    }
    free(head);

    int closeSent = 0, pendingSha = 0, msgOp = -1;
    unsigned char *msg = NULL; size_t mlen = 0, mcap = 0;
    for (;;) {
        int b0 = get_byte(); if (b0 < 0) break;
        int b1 = get_byte(); if (b1 < 0) break;
        int fin = b0 & 0x80, op = b0 & 0x0f;
        uint64_t len = (uint64_t)(b1 & 0x7f);
        if (len == 126) {
            unsigned char x[2]; if (!read_exact(x, 2)) break;
            len = ((uint64_t)x[0] << 8) | x[1];
        } else if (len == 127) {
            unsigned char x[8]; if (!read_exact(x, 8)) break;
            len = 0; for (int i = 0; i < 8; i++) len = (len << 8) | x[i];
        }
        unsigned char mk[4]; int masked = b1 & 0x80;
        if (masked && !read_exact(mk, 4)) break;
        unsigned char *pl = malloc(len + 1);
        if (!read_exact(pl, len)) { free(pl); break; }
        if (masked) for (uint64_t i = 0; i < len; i++) pl[i] ^= mk[i & 3];

        if (op == 9) { send_frame(10, pl, len); free(pl); continue; }
        if (op == 10) { free(pl); continue; }
        if (op == 8) {
            if (len >= 2) {
                int cc = (pl[0] << 8) | pl[1];
                if (len > 2) { printf("closed %d ", cc); fwrite(pl + 2, 1, len - 2, stdout); printf("\n"); }
                else printf("closed %d\n", cc);
            } else printf("closed 1005\n");
            fflush(stdout);
            if (!closeSent) send_frame(8, pl, len >= 2 ? 2 : 0);
            free(pl);
            break;
        }
        if (op == 1 || op == 2) { msgOp = op; mlen = 0; }
        else if (op != 0) { free(pl); continue; }
        if (mlen + len + 1 > mcap) { mcap = (mlen + len + 1) * 2; msg = realloc(msg, mcap); }
        memcpy(msg + mlen, pl, len); mlen += len;
        free(pl);
        if (!fin) continue;
        if (msgOp == 1) {
            msg[mlen] = 0;
            if (mlen >= 4 && memcmp(msg, "ADD ", 4) == 0) {
                long long a = 0, b = 0;
                sscanf((char *)msg + 4, "%lld %lld", &a, &b);
                char rep[64]; snprintf(rep, sizeof rep, "%lld", a + b);
                send_frame(1, (unsigned char *)rep, strlen(rep));
                printf("%s -> %s\n", (char *)msg, rep);
            } else if (mlen >= 3 && memcmp(msg, "REV", 3) == 0) {
                char *sp = memchr(msg, ' ', mlen);
                size_t ts = sp ? (size_t)((unsigned char *)sp - msg) + 1 : mlen;
                size_t tl = mlen - ts;
                size_t *st = malloc((tl + 2) * sizeof(size_t));
                size_t cnt = utf8_split(msg + ts, tl, st);
                unsigned char *rep = malloc(tl + 1); size_t ro = 0;
                for (size_t i = cnt; i-- > 0;) {
                    size_t a = st[i], e = st[i + 1];
                    memcpy(rep + ro, msg + ts + a, e - a); ro += e - a;
                }
                send_frame(1, rep, ro);
                fwrite(msg, 1, mlen, stdout); printf(" -> "); fwrite(rep, 1, ro, stdout); printf("\n");
                free(rep); free(st);
            } else if (mlen == 4 && memcmp(msg, "SHA1", 4) == 0) {
                pendingSha = 1;
            } else if (mlen == 3 && memcmp(msg, "BYE", 3) == 0) {
                unsigned char cp[2] = {0x03, 0xE8};
                send_frame(8, cp, 2);
                closeSent = 1;
            }
        } else if (msgOp == 2 && pendingSha) {
            pendingSha = 0;
            char hex[41]; sha1hex(msg, mlen, hex);
            send_frame(1, (unsigned char *)hex, 40);
            printf("SHA1 %zu -> %s\n", mlen, hex);
        }
        fflush(stdout);
    }
    free(msg);
    close(fd);
}

int main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    for (int i = 1; i < argc; i++) {
        printf("session %d\n", i);
        fflush(stdout);
        session(argv[i]);
        fflush(stdout);
    }
    return 0;
}
