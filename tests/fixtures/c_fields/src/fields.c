#include <string.h>
#include "fields.h"

int g_total;
struct conn g_main;
struct conn conns[4];
int fds[2];
static struct conn table[] = {{.fd = 1, .count = 2}, {.count = 3}};
static struct conn pos = {5, 6};
static anon_t anon_default = {.count = 1};

void fill(int *p) { *p = 7; }
void close_fd(int fd) { (void)fd; }
void log_num(int n) { g_total += n; }
struct conn *conn_at(int i) { return i ? &table[i] : &pos; }
int anon_default_count(void) { return anon_default.count; }

void touch(struct conn *c) { c->count++; }
void touch_array(void) { conns[1].count = 0; }
int buf_count(struct buf *b) { return b->count; }
void close_all(struct conn *tab, int n) {
    for (int i = 0; i < n; i++)
        tab[i].fd = 0;
}
void reset_conn(struct conn *c) { *c = (struct conn){.count = 0}; }
int *leak_count(struct conn *c) {
    fill(&c->count);
    int *p = &c->fd;
    (void)p;
    return &c->count;
}
void close_first(void) { close_fd(fds[0]); }
void reset_all(struct conn *c) { RESET(c); }
void wipe(void *p) { memset(p, 0, sizeof(struct conn)); }
void bump_any(void *p) { ((struct conn *)p)->count++; }
size_t count_offset(void) { return offsetof(struct conn, count); }
void other_config(struct conn *c) {
#ifdef NEVER_DEFINED
    c->count = 9;
#endif
    (void)c;
}
int anon_count(anon_t *a) { return a->count; }
void put(struct conn *a) { a->count = 1; }
int get(struct conn *b) { return b->count; }
int pair_int(struct pair *p) { return p->val.i; }
long pair_anon(struct pair *p) { return p->a; }
void main_fd(void) { g_main.fd = 3; }
int total(void) { return g_total; }
void caller_of_log(void) { log_num(2); }
void caller_of_caller(void) { caller_of_log(); }
