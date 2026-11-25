#include "t.h"
id_t2 get(void);
int main(void) { id_t2 *p = 0; int *q = p; return get() + (q!=0); }
