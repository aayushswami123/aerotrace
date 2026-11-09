#ifndef DATA_H
#define DATA_H
struct conn { int fd; int count; };
struct buf { int count; char *ptr; };
typedef int (*parser_fn)(const char *s, int *out);
extern int g_total;
extern struct conn conns[4];
int parse_num(const char *s, int *out);
void log_num(int n);
void abort_now(void);
void register_parser(parser_fn fn);
void fill(int *p);
#define CHECK(x) do { if (!(x)) abort_now(); } while (0)
#endif
