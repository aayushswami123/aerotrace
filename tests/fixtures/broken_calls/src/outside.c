/* ext_lib.h lives outside the repo (the test passes -I to it), but ext_add is defined here. */
#include "ext_lib.h"

int good_call(void) { return ext_add(1, 2); }
int bad_call(void) { return ext_add(1); }

int ext_add(int a, int b) { return a + b; }
