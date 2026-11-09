#ifndef API_H
#define API_H

struct conn {
    int fd;
    int count;
    unsigned flags : 3;
    long total;
};

int parse(int x);
int scale(int a, int b);
int status(void);
int old_style();
int log_msg(const char *fmt, ...);

extern int max_clients;
extern struct conn g_conn;

typedef int (*handler_fn)(int);
typedef long (*cmd_fn)(long);
long cmd_check_len(long n);
long cmd_append(long v);
long run_cmd(int i, long v);

struct msg {
    union {
        int i;
        long l;
    } v;
};
typedef struct conn conn_t;
int touch(conn_t *c);

#endif
