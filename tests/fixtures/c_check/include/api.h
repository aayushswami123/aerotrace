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

#endif
