#include "defs.h"

int user(void) {
    count_t n = SCALE(2);
    enum color c = BLUE;
    return n + (int)c + get(1);
}
