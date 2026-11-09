#include "calc.h"

int calc_mode = 0;

int add(int a, int b) { return a + b; }

int sub(int a, int b) { return a - b; }

int mul(int a, int b) { return a * b; }

/* No indexed test calls divide: one test is never registered, one is inside #if 0, one is not indexed. */
int divide(int a, int b) { return b ? a / b : 0; }

/* Calls through a pointer: its link to sub is via_pointer. */
int apply(int (*op)(int, int), int a, int b) { return op(a, b); }

void set_mode(int m) { calc_mode = m; }

const char *mode_name(void) { return calc_mode ? "fast" : "slow"; }

/* Only scale calls twice, so its one test (tests/test_scale.c) is two call steps away. */
static int twice(int x) { return add(x, x); }

int scale(int x) { return mul(twice(x), 5); }
