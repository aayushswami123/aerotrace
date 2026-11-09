#include <stdlib.h>
#include <string.h>

#include "util.h"

/* Built with -O2: this static is inlined into main, but its counters still belong to it. */
static int pick(const char *arg) { return strcmp(arg, "b") == 0 ? fb(1) : fa(1); }

int main(int argc, char **argv) {
    if (argc < 2)
        return 2;
    int r = pick(argv[1]) + twice(1);
    if (strcmp(argv[1], "abort") == 0)
        abort(); /* the process dies without writing its coverage */
    return r > 0 ? 0 : 1;
}
