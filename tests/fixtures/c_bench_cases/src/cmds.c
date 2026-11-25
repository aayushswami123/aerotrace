#include "filter.h"

struct cmd {
    const char *name;
    int (*keys)(int);
};

struct cmd cmd_table[] = {
    {"get", get_keys},
};
