#include "lib.h"

int main(void) {
#ifdef USE_F
    return f(1);
#else
    return g(0);
#endif
}
