#include "shared.h"

int g_total;

void add(int x) {
    g_total += x;
}

int get(void) {
    return g_total;
}

int next_id(void) {
    static int id;
    return ++id;
}

void calls_missing(void) {
    declared_only();
}

int likely_positive(int x) {
    if (__builtin_expect(x > 0, 1)) {
        return 1;
    }
    return 0;
}
