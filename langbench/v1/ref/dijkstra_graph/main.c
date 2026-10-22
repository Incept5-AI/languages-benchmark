#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXN 256
#define MAXE 1024

static char nodes[MAXN][64];
static int n_nodes = 0;

static int edge_from[MAXE], edge_to[MAXE], edge_w[MAXE];
static int n_edges = 0;

static int node_index(const char *name) {
    for (int i = 0; i < n_nodes; i++)
        if (strcmp(nodes[i], name) == 0)
            return i;
    return -1;
}

/* Read entire file into a malloc'd buffer */
static char *read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror("fopen"); exit(1); }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1);
    if (!buf) { exit(1); }
    if (fread(buf, 1, sz, f) != (size_t)sz) { exit(1); }
    buf[sz] = '\0';
    fclose(f);
    return buf;
}

/* Find the next double-quoted string starting at *p, copy into out, advance *p past closing quote. Returns 1 on success. */
static int next_string(const char **p, char *out, int outsz) {
    const char *s = *p;
    while (*s && *s != '"') s++;
    if (*s != '"') return 0;
    s++;
    int i = 0;
    while (*s && *s != '"') {
        if (i < outsz - 1) out[i++] = *s;
        s++;
    }
    if (*s != '"') return 0;
    out[i] = '\0';
    s++;
    *p = s;
    return 1;
}

int main(void) {
    char *json = read_file("__FIXTURE_DIR__/graph.json");

    /* Locate "nodes" array */
    char *nk = strstr(json, "\"nodes\"");
    char *ek = strstr(json, "\"edges\"");
    if (!nk || !ek) { fprintf(stderr, "bad json\n"); return 1; }

    /* Parse node names: from after "nodes" up to "edges" (nodes appears first). */
    const char *p = nk + 7; /* skip past "nodes" key text */
    /* skip to '[' */
    while (*p && *p != '[') p++;
    if (*p == '[') p++;
    char tmp[64];
    while (p < ek) {
        /* find next quote before reaching edges */
        const char *q = p;
        while (*q && *q != '"' && q < ek) q++;
        if (q >= ek || *q != '"') break;
        const char *pp = q;
        if (!next_string(&pp, tmp, sizeof(tmp))) break;
        if (n_nodes < MAXN) {
            strcpy(nodes[n_nodes], tmp);
            n_nodes++;
        }
        p = pp;
    }

    /* Parse edges: each object has "from","to","weight" */
    p = ek + 7;
    while (*p && *p != '[') p++;
    if (*p == '[') p++;
    while (1) {
        char fromName[64], toName[64];
        /* look for "from" */
        char *fk = strstr((char *)p, "\"from\"");
        if (!fk) break;
        const char *fp = fk + 6;
        if (!next_string(&fp, fromName, sizeof(fromName))) break;

        char *tk = strstr((char *)fp, "\"to\"");
        if (!tk) break;
        const char *tp = tk + 4;
        if (!next_string(&tp, toName, sizeof(toName))) break;

        char *wk = strstr((char *)tp, "\"weight\"");
        if (!wk) break;
        const char *wp = wk + 8;
        /* skip to ':' then number */
        while (*wp && *wp != ':') wp++;
        if (*wp == ':') wp++;
        while (*wp == ' ' || *wp == '\t' || *wp == '\n' || *wp == '\r') wp++;
        int w = atoi(wp);
        while (*wp && (*wp == '-' || (*wp >= '0' && *wp <= '9'))) wp++;

        int fi = node_index(fromName);
        int ti = node_index(toName);
        if (fi >= 0 && ti >= 0 && n_edges < MAXE) {
            edge_from[n_edges] = fi;
            edge_to[n_edges] = ti;
            edge_w[n_edges] = w;
            n_edges++;
        }
        p = wp;
    }

    /* Dijkstra from "A" (undirected). Distances as long, INF sentinel. */
    const long INF = 1000000000L;
    long *dist = malloc(sizeof(long) * n_nodes);
    int *done = malloc(sizeof(int) * n_nodes);
    for (int i = 0; i < n_nodes; i++) { dist[i] = INF; done[i] = 0; }

    int src = node_index("A");
    if (src < 0) { fprintf(stderr, "no node A\n"); return 1; }
    dist[src] = 0;

    for (int iter = 0; iter < n_nodes; iter++) {
        int u = -1;
        long best = INF;
        for (int i = 0; i < n_nodes; i++) {
            if (!done[i] && dist[i] < best) { best = dist[i]; u = i; }
        }
        if (u < 0) break;
        done[u] = 1;
        for (int e = 0; e < n_edges; e++) {
            int v = -1, ew = 0;
            if (edge_from[e] == u) { v = edge_to[e]; ew = edge_w[e]; }
            else if (edge_to[e] == u) { v = edge_from[e]; ew = edge_w[e]; }
            if (v >= 0 && dist[u] + ew < dist[v]) dist[v] = dist[u] + ew;
        }
    }

    /* Sort node indices alphabetically by name */
    int *order = malloc(sizeof(int) * n_nodes);
    for (int i = 0; i < n_nodes; i++) order[i] = i;
    for (int i = 0; i < n_nodes; i++) {
        for (int j = i + 1; j < n_nodes; j++) {
            if (strcmp(nodes[order[i]], nodes[order[j]]) > 0) {
                int t = order[i]; order[i] = order[j]; order[j] = t;
            }
        }
    }

    printf("=== Shortest Paths from A ===\n");
    printf("\n");
    for (int i = 0; i < n_nodes; i++) {
        int idx = order[i];
        printf("A -> %s: %ld\n", nodes[idx], dist[idx]);
    }

    free(json); free(dist); free(done); free(order);
    return 0;
}
