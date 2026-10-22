#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXLINE 4096
#define MAXITEMS 1024

/* Ordered-by-first-occurrence key/count maps to mirror Python dict + max() tie-break. */
typedef struct {
    char *keys[MAXITEMS];
    int counts[MAXITEMS];
    int n;
} OrderedMap;

static int om_index(OrderedMap *m, const char *key) {
    for (int i = 0; i < m->n; i++) {
        if (strcmp(m->keys[i], key) == 0) return i;
    }
    return -1;
}

static void om_inc(OrderedMap *m, const char *key) {
    int idx = om_index(m, key);
    if (idx < 0) {
        m->keys[m->n] = strdup(key);
        m->counts[m->n] = 1;
        m->n++;
    } else {
        m->counts[idx]++;
    }
}

/* Returns index of first key with the maximum count (Python max() semantics). */
static int om_argmax(OrderedMap *m) {
    int best = -1;
    for (int i = 0; i < m->n; i++) {
        if (best < 0 || m->counts[i] > m->counts[best]) best = i;
    }
    return best;
}

static int cmpstr(const void *a, const void *b) {
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

int main(void) {
    FILE *f = fopen("__FIXTURE_DIR__/app.log", "r");
    if (!f) {
        perror("fopen");
        return 1;
    }

    OrderedMap levels = {0};
    OrderedMap errComp = {0};
    OrderedMap hours = {0};

    char *errMsgs[MAXITEMS];
    int errMsgN = 0;

    char line[MAXLINE];
    while (fgets(line, sizeof(line), f)) {
        /* strip trailing newline(s) */
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
            line[--len] = '\0';
        }
        if (len == 0) continue;

        /* Format: [YYYY-MM-DD HH:MM:SS] LEVEL (component) - message */
        if (line[0] != '[') continue;
        char *rb = strchr(line, ']');
        if (!rb) continue;

        /* hour: characters at positions 12,13 inside the timestamp ([0]=='[') */
        /* timestamp = line+1 .. rb-1 ; "YYYY-MM-DD HH:MM:SS" -> HH at offset 11,12 */
        char hour[3] = {0};
        /* line+1 is 'Y'; HH starts at index 1+11 = 12 */
        hour[0] = line[12];
        hour[1] = line[13];
        hour[2] = '\0';

        /* parse after "] " */
        char *p = rb + 1;
        while (*p == ' ') p++;

        /* LEVEL = token up to next space */
        char *levelStart = p;
        char *sp = strchr(p, ' ');
        if (!sp) continue;
        char level[64];
        size_t llen = (size_t)(sp - levelStart);
        if (llen >= sizeof(level)) llen = sizeof(level) - 1;
        memcpy(level, levelStart, llen);
        level[llen] = '\0';

        /* component inside parentheses */
        char *lp = strchr(sp, '(');
        char *rp = lp ? strchr(lp, ')') : NULL;
        if (!lp || !rp) continue;
        char comp[256];
        size_t clen = (size_t)(rp - lp - 1);
        if (clen >= sizeof(comp)) clen = sizeof(comp) - 1;
        memcpy(comp, lp + 1, clen);
        comp[clen] = '\0';

        /* message: after first " - " following the component */
        char *dash = strstr(rp, " - ");
        char *msg = dash ? dash + 3 : NULL;

        om_inc(&levels, level);
        om_inc(&hours, hour);

        if (strcmp(level, "ERROR") == 0) {
            om_inc(&errComp, comp);
            if (msg) {
                /* dedupe error messages (set semantics) */
                int found = 0;
                for (int i = 0; i < errMsgN; i++) {
                    if (strcmp(errMsgs[i], msg) == 0) { found = 1; break; }
                }
                if (!found) errMsgs[errMsgN++] = strdup(msg);
            }
        }
    }
    fclose(f);

    /* Output */
    printf("=== Log Analysis ===\n\n");

    printf("Entries by Level:\n");
    /* sorted alphabetically by level name */
    int order[MAXITEMS];
    for (int i = 0; i < levels.n; i++) order[i] = i;
    for (int i = 0; i < levels.n; i++) {
        for (int j = i + 1; j < levels.n; j++) {
            if (strcmp(levels.keys[order[i]], levels.keys[order[j]]) > 0) {
                int t = order[i]; order[i] = order[j]; order[j] = t;
            }
        }
    }
    for (int i = 0; i < levels.n; i++) {
        int k = order[i];
        printf("  %s: %d\n", levels.keys[k], levels.counts[k]);
    }

    int ce = om_argmax(&errComp);
    printf("\nComponent with Most Errors: %s (%d errors)\n",
           errComp.keys[ce], errComp.counts[ce]);

    printf("\nUnique Error Messages:\n");
    qsort(errMsgs, errMsgN, sizeof(char *), cmpstr);
    for (int i = 0; i < errMsgN; i++) {
        printf("  - %s\n", errMsgs[i]);
    }

    int bh = om_argmax(&hours);
    printf("\nBusiest Hour: %s:00 (%d entries)", hours.keys[bh], hours.counts[bh]);

    return 0;
}
