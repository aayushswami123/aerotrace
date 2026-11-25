#ifndef GAPS_H
#define GAPS_H
#include <stddef.h>
struct inner { int x; int y; };
struct cfg {
    int hz;
    int :3;                          /* unnamed bit-field: takes no value */
    struct inner in;
    union { int as_int; float as_float; } u;
    struct { int anon_a; int anon_b; };  /* C11 anonymous member */
    char name[8];
    int last;
};
struct server { int config_hz; int port; int pipe[2]; char names[2][8]; };
extern struct server server;
extern int level;
int use_int(int v);
int use_ptr(const char *p);
int fill_pipe(int *p);
#endif
