/* A comparison does not take a function's address (#12); (*f)(x) is a direct call (#22). */
typedef int (*op)(int);

int only_compared(int a) { return a; }
int stored(int a) { return a + 1; }
int two(int a, int b) { return a + b; }

op table[] = {stored};

int is_it(op p) { return p == only_compared; }
int checked(void) { return only_compared ? 1 : 0; }
int run(op p) { return p(1); }
int good_deref(void) { return (*two)(1, 2); }
int good_addr(void) { return (&two)(1, 2); }
