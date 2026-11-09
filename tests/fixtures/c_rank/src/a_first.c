#include "rank.h"

/* f is defined here and again in dup/f.c (same name, both extern). Calls from this file see this one
   (exact); calls from files that only see the header could be either (ambiguous, probable). */
int f(int a, int b) { return a + b; }

void note(int x) { (void)x; }

binop t1[] = {f};
binop t2[] = {f};

void ignored_first(void) { f(1, 2); }

int stored_exact(void) {
    int v = f(3, 4);
    return v;
}

int direct_caller(void) { return f(5, 6); }

int both_ways(void) {
    int a = f(7, 8);
    return a + t1[0](9, 10);
}

void discards(void) { (void)f(1, 1); }

void many(void) {
    f(0, 0);
    f(0, 1);
    f(0, 2);
    f(0, 3);
    f(0, 4);
    f(0, 5);
    f(0, 6);
    f(0, 7);
    f(0, 8);
    f(0, 9);
    f(1, 0);
    f(1, 1);
    f(1, 2);
    f(1, 3);
    f(1, 4);
    f(1, 5);
    f(1, 6);
    f(1, 7);
    f(1, 8);
    f(1, 9);
}

void notes(void) {
    note(1);
    note(2);
}
