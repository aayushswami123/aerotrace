#include "rank.h"

/* Calls through pointers of f's type. dispatch2 has two such calls. */
int dispatch1(int i) { return t1[i](1, 2); }

int dispatch2(int i) {
    int r = t2[i](1, 2);
    return r + t2[i](3, 4);
}

/* reaches f through direct_caller (exact) and through dispatch1 (pointer): exact, in the direct group */
int top(void) { return direct_caller() + dispatch1(0); }

/* reaches f only through two pointer entries at the same distance: never exact */
int top2(void) { return dispatch1(0) + dispatch2(0); }

/* reaches f through both_ways, which calls f directly and through a pointer: exact */
int calls_both(void) { return both_ways(); }

int only_ptr1(void) { return dispatch2(1); }
int only_ptr2(void) { return dispatch2(2); }
int only_ptr3(void) { return dispatch2(3); }
int only_ptr4(void) { return dispatch2(4); }
int only_ptr5(void) { return dispatch2(5); }
int only_ptr6(void) { return dispatch2(6); }

/* distance 3, only through pointers */
int deep(void) { return top2(); }
