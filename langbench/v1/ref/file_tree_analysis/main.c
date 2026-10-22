#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>

#define ROOT "__FIXTURE_DIR__/project_tree"

typedef struct {
    char rel[4096];
    char ext[256];
    long lines;
    long chars;
} FileInfo;

static FileInfo *files = NULL;
static size_t nfiles = 0, cap = 0;

static void add_file(const char *rel, const char *ext, long lines, long chars) {
    if (nfiles == cap) {
        cap = cap ? cap * 2 : 64;
        files = realloc(files, cap * sizeof(FileInfo));
    }
    snprintf(files[nfiles].rel, sizeof(files[nfiles].rel), "%s", rel);
    snprintf(files[nfiles].ext, sizeof(files[nfiles].ext), "%s", ext);
    files[nfiles].lines = lines;
    files[nfiles].chars = chars;
    nfiles++;
}

/* Compute byte count and splitlines-style line count for a file. */
static void measure(const char *full, long *out_lines, long *out_chars) {
    FILE *f = fopen(full, "rb");
    long bytes = 0;
    long lines = 0;
    int last = -1; /* last byte read */
    if (f) {
        int c;
        while ((c = fgetc(f)) != EOF) {
            bytes++;
            if (c == '\n') lines++;
            last = c;
        }
        fclose(f);
    }
    /* splitlines: a final segment without a trailing newline counts as a line. */
    if (bytes > 0 && last != '\n') lines++;
    *out_lines = lines;
    *out_chars = bytes;
}

static void walk(const char *dir, const char *rel_prefix) {
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
            continue;
        char full[4096];
        snprintf(full, sizeof(full), "%s/%s", dir, e->d_name);
        char rel[4096];
        if (rel_prefix[0])
            snprintf(rel, sizeof(rel), "%s/%s", rel_prefix, e->d_name);
        else
            snprintf(rel, sizeof(rel), "%s", e->d_name);

        struct stat st;
        if (stat(full, &st) != 0) continue;
        if (S_ISDIR(st.st_mode)) {
            walk(full, rel);
        } else if (S_ISREG(st.st_mode)) {
            const char *dot = strrchr(e->d_name, '.');
            char ext[256];
            if (dot && dot != e->d_name)
                snprintf(ext, sizeof(ext), "%s", dot);
            else
                ext[0] = '\0';
            long lines, chars;
            measure(full, &lines, &chars);
            add_file(rel, ext, lines, chars);
        }
    }
    closedir(d);
}

static int cmp_ext_rel(const void *a, const void *b) {
    const FileInfo *fa = a, *fb = b;
    int c = strcmp(fa->ext, fb->ext);
    if (c != 0) return c;
    return strcmp(fa->rel, fb->rel);
}

int main(void) {
    walk(ROOT, "");
    qsort(files, nfiles, sizeof(FileInfo), cmp_ext_rel);

    printf("=== File Tree Analysis ===\n\n");

    long grand_files = 0, grand_lines = 0, grand_chars = 0;
    size_t i = 0;
    while (i < nfiles) {
        size_t j = i;
        long ext_files = 0, ext_lines = 0, ext_chars = 0;
        while (j < nfiles && strcmp(files[j].ext, files[i].ext) == 0) {
            ext_files++;
            ext_lines += files[j].lines;
            ext_chars += files[j].chars;
            j++;
        }
        printf("Extension: %s\n", files[i].ext);
        printf("  Files: %ld\n", ext_files);
        printf("  Total Lines: %ld\n", ext_lines);
        printf("  Total Characters: %ld\n", ext_chars);
        for (size_t k = i; k < j; k++) {
            printf("    %s: %ld lines, %ld chars\n",
                   files[k].rel, files[k].lines, files[k].chars);
        }
        grand_files += ext_files;
        grand_lines += ext_lines;
        grand_chars += ext_chars;
        i = j;
    }

    printf("\nTotal: %ld files, %ld lines, %ld characters\n",
           grand_files, grand_lines, grand_chars);

    free(files);
    return 0;
}
