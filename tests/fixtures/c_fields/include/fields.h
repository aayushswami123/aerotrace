#ifndef FIELDS_H
#define FIELDS_H
#include <stddef.h>
struct conn { int fd; int count; };
struct buf { int count; char *ptr; };
typedef struct { int count; int fd; } anon_t;
struct pair { int key; union { int i; double d; } val; union { int a; long b; }; };
extern int g_total;
extern struct conn g_main;
extern struct conn conns[4];
extern int fds[2];
void fill(int *p);
void close_fd(int fd);
void log_num(int n);
#define RESET(c) ((c)->count = 0)
#endif
