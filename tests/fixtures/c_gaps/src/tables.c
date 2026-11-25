#include "gaps.h"

struct server server;
int level;
static int other;
struct inner copy_src = {1, 2};

/* #27: addresses stored in file-scope tables */
typedef struct { const char *name; int *addr; } int_config;
int_config configs[] = {
    {"hz", &(server.config_hz)},
    {"level", &level},
};
int *pipe_end = &server.pipe[1];
int *as_ptr = server.pipe;
int plain_value = 3;
int *not_an_address = 0;

/* #26: by position, with braces left out, nested, unions, anonymous members, strings */
struct cfg full = {10, 1, 2, 3, 4, 5, "srv", 6};
struct cfg braced = {10, {1, 2}, {3}, {4, 5}, "srv", 6};
struct cfg copied = {10, {0}, .last = 7};
struct cfg after_designator = {.hz = 1, 2, 3};
struct cfg zero = {0};
struct cfg tables[] = {{1, 2, 3}, 4, 5};

int use_int(int v) { return v; }
int use_ptr(const char *p) { return p != 0; }
int fill_pipe(int *p) { return p[0]; }
