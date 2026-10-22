#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Run a command via popen and capture its first line of output,
   stripping trailing whitespace/newlines. */
static void run_cmd(const char *cmd, char *out, size_t outsz) {
    out[0] = '\0';
    FILE *fp = popen(cmd, "r");
    if (!fp) return;
    size_t total = 0;
    int ch;
    while ((ch = fgetc(fp)) != EOF && total + 1 < outsz) {
        out[total++] = (char)ch;
    }
    out[total] = '\0';
    pclose(fp);
    /* strip trailing whitespace */
    while (total > 0) {
        char c = out[total - 1];
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t' || c == '\f' || c == '\v') {
            out[--total] = '\0';
        } else {
            break;
        }
    }
}

int main(void) {
    char os[1024], arch[1024], user[1024], host[1024];

    run_cmd("uname -s", os, sizeof(os));
    run_cmd("uname -m", arch, sizeof(arch));
    run_cmd("whoami", user, sizeof(user));
    run_cmd("hostname", host, sizeof(host));

    printf("{\n");
    printf("  \"hostname\": \"%s\",\n", host);
    printf("  \"os\": \"%s\",\n", os);
    printf("  \"architecture\": \"%s\",\n", arch);
    printf("  \"username\": \"%s\"\n", user);
    printf("}");

    return 0;
}
