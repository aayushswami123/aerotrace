#include "board.h"

volatile unsigned int led_reg;

void led_on(void) {
    led_reg = 1u;
}

void fpu_init(void) {
    led_reg |= 2u;
}
