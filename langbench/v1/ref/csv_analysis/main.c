#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXE 1024
#define MAXD 256

struct Emp { char name[128]; char dept[128]; long salary; };

static struct Emp emps[MAXE];
static int nemp = 0;

struct Dept { char name[128]; long total; int count; };
static struct Dept depts[MAXD];
static int ndept = 0;

static int find_dept(const char *name) {
    for (int i = 0; i < ndept; i++)
        if (strcmp(depts[i].name, name) == 0) return i;
    strcpy(depts[ndept].name, name);
    depts[ndept].total = 0;
    depts[ndept].count = 0;
    return ndept++;
}

/* format a non-negative integer with thousands commas into buf */
static void comma_int(long v, char *buf) {
    char tmp[32];
    int n = sprintf(tmp, "%ld", v);
    int out = 0;
    for (int i = 0; i < n; i++) {
        if (i > 0 && (n - i) % 3 == 0) buf[out++] = ',';
        buf[out++] = tmp[i];
    }
    buf[out] = '\0';
}

/* format an average with comma-grouped integer part and 2 decimals */
static void comma_avg(double v, char *buf) {
    /* round to 2 decimals */
    long cents = (long)(v * 100.0 + 0.5);
    long ipart = cents / 100;
    long frac = cents % 100;
    char ibuf[32];
    comma_int(ipart, ibuf);
    sprintf(buf, "%s.%02ld", ibuf, frac);
}

static int cmp_str(const void *a, const void *b) {
    return strcmp(((const struct Dept *)a)->name, ((const struct Dept *)b)->name);
}

int main(void) {
    FILE *f = fopen("__FIXTURE_DIR__/employees.csv", "r");
    if (!f) { perror("open"); return 1; }
    char line[1024];
    int first = 1;
    while (fgets(line, sizeof(line), f)) {
        /* strip newline */
        size_t L = strlen(line);
        while (L > 0 && (line[L-1] == '\n' || line[L-1] == '\r')) line[--L] = '\0';
        if (first) { first = 0; continue; }
        if (L == 0) continue;
        /* columns: employee_id,name,department,salary,start_date,rating */
        char *cols[6];
        int c = 0;
        char *p = line;
        cols[c++] = p;
        while (*p && c < 6) {
            if (*p == ',') { *p = '\0'; cols[c++] = p + 1; }
            p++;
        }
        if (c < 6) continue;
        strcpy(emps[nemp].name, cols[1]);
        strcpy(emps[nemp].dept, cols[2]);
        emps[nemp].salary = atol(cols[3]);
        int di = find_dept(cols[2]);
        depts[di].total += emps[nemp].salary;
        depts[di].count++;
        nemp++;
    }
    fclose(f);

    /* sort departments alphabetically */
    qsort(depts, ndept, sizeof(struct Dept), cmp_str);

    printf("=== Salary Report ===\n\n");
    printf("Average Salary by Department:\n");
    for (int i = 0; i < ndept; i++) {
        double avg = (double)depts[i].total / (double)depts[i].count;
        char ab[64];
        comma_avg(avg, ab);
        printf("  %s: $%s\n", depts[i].name, ab);
    }

    /* highest paid (first max, matching python max stability) */
    int top = 0;
    for (int i = 1; i < nemp; i++)
        if (emps[i].salary > emps[top].salary) top = i;
    char sb[32];
    comma_int(emps[top].salary, sb);
    printf("\nHighest Paid: %s ($%s)\n", emps[top].name, sb);

    printf("\nDepartments with >2 employees:\n");
    for (int i = 0; i < ndept; i++)
        if (depts[i].count > 2)
            printf("  %s (%d employees)\n", depts[i].name, depts[i].count);

    long total = 0;
    for (int i = 0; i < nemp; i++) total += emps[i].salary;
    char tb[32];
    comma_int(total, tb);
    printf("\nTotal Budget: $%s\n", tb);

    return 0;
}
