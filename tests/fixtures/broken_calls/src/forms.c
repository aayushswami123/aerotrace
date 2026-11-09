/* Rarer C forms of a call that does not compile (#14). */
typedef int (*fn2)(int, int);

int two(int a, int b) { return a + b; }
int other2(int a, int b) { return a - b; }
int three(int a, int b, int c) { return a + b + c; }

/* the function, dereferenced: still a call to two, not a use of its address */
int bad_deref(void) { return (*two)(1); }
int bad_addr(void) { return (&three)(1); }

/* either function may be called: a call through a pointer; both have their address taken */
int bad_cond(int c) { return (c ? two : other2)(1); }

/* a bad call through a pointer variable: still a pointer call */
int bad_ptr(void) {
    fn2 p = other2;
    return p(1);
}

/* the outer call is broken too (three arguments for two parameters) */
int bad_nested(void) { return two(two(1), 2, 3); }
/* the outer call has the right count, but its argument does not compile */
int bad_inner(void) { return two(two(1), 2); }

/* a function passed as a value to a broken call is still address taken */
int passes_three(void) { return two(three); }
