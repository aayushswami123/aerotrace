/* Shapes of benchmark cases curl-05, redis-05 and curl-07 (see tests/test_bench_cases.py). */
typedef long timediff_t;

struct conf {
    int a;
    timediff_t upkeep_ms;
    int *counter;
};

struct filter {
    const char *name;
    void (*destroy)(int);
    int (*open)(int);
};

int get_keys(int n);
