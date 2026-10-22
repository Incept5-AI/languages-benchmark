#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>

typedef struct {
    char name[256];
    double price;
} Product;

/* Skip whitespace */
static const char *skip_ws(const char *p) {
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    return p;
}

/* Parse a JSON string starting at the opening quote; copy unescaped into out. */
static const char *parse_string(const char *p, char *out, size_t outsz) {
    size_t i = 0;
    if (*p != '"') return NULL;
    p++;
    while (*p && *p != '"') {
        char c = *p;
        if (c == '\\' && *(p + 1)) {
            p++;
            switch (*p) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case '"': c = '"';  break;
                case '\\': c = '\\'; break;
                case '/': c = '/';  break;
                default: c = *p;    break;
            }
        }
        if (i + 1 < outsz) out[i++] = c;
        p++;
    }
    if (*p == '"') p++;
    out[i] = '\0';
    return p;
}

int cmp_name(const void *a, const void *b) {
    return strcmp(((const Product *)a)->name, ((const Product *)b)->name);
}

int main(void) {
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) { perror("socket"); return 1; }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(__PORT__);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect");
        return 1;
    }

    const char *req =
        "GET /api/products HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Connection: close\r\n"
        "\r\n";
    if (write(sock, req, strlen(req)) < 0) { perror("write"); return 1; }

    /* Read entire response */
    size_t cap = 65536, len = 0;
    char *buf = malloc(cap);
    if (!buf) return 1;
    ssize_t n;
    while ((n = read(sock, buf + len, cap - len - 1)) > 0) {
        len += (size_t)n;
        if (len + 1 >= cap) {
            cap *= 2;
            char *nb = realloc(buf, cap);
            if (!nb) { free(buf); return 1; }
            buf = nb;
        }
    }
    buf[len] = '\0';
    close(sock);

    /* Find body: after \r\n\r\n */
    char *body = strstr(buf, "\r\n\r\n");
    if (!body) { free(buf); fprintf(stderr, "no body\n"); return 1; }
    body += 4;

    /* Parse JSON array of objects. We look for "name" and "price" keys. */
    Product products[256];
    int count = 0;

    const char *p = body;
    /* iterate over objects */
    while ((p = strchr(p, '{')) != NULL) {
        const char *objstart = p;
        const char *objend = strchr(p, '}');
        if (!objend) break;

        Product prod;
        prod.name[0] = '\0';
        prod.price = 0.0;
        int has_name = 0, has_price = 0;

        const char *q = objstart + 1;
        while (q < objend) {
            q = skip_ws(q);
            if (*q != '"') { q++; continue; }
            char key[64];
            q = parse_string(q, key, sizeof(key));
            if (!q) break;
            q = skip_ws(q);
            if (*q == ':') q++;
            q = skip_ws(q);

            if (strcmp(key, "name") == 0) {
                q = parse_string(q, prod.name, sizeof(prod.name));
                has_name = 1;
            } else if (strcmp(key, "price") == 0) {
                prod.price = strtod(q, (char **)&q);
                has_price = 1;
            } else {
                /* skip value: string, number, or simple token */
                if (*q == '"') {
                    char tmp[256];
                    q = parse_string(q, tmp, sizeof(tmp));
                } else {
                    while (q < objend && *q != ',' && *q != '}') q++;
                }
            }
            q = skip_ws(q);
            if (*q == ',') q++;
        }

        if (has_name && has_price && prod.price > 50.00) {
            products[count++] = prod;
        }
        p = objend + 1;
    }

    qsort(products, count, sizeof(Product), cmp_name);

    printf("=== Filtered Products (price > $50.00) ===\n\n");
    double total = 0.0;
    for (int i = 0; i < count; i++) {
        printf("%s: $%.2f\n", products[i].name, products[i].price);
        total += products[i].price;
    }
    printf("\nCount: %d\n", count);
    printf("Total: $%.2f\n", total);

    free(buf);
    return 0;
}
