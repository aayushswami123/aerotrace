#include "ops.h"
typedef int (*op_fn)(int);
op_fn table[] = { op_add, op_sub };
int main(void) { return table[0](1); }
