#include "board.h"

int main(void) {
#if defined(__ARM_ARCH_7EM__) && defined(__ARM_FP)
    fpu_init(); /* only in the real Cortex-M4F build */
#endif
    led_on();
    return 0;
}
