#include "g.h"

int counter = 0;

int bump(void) {
    return ++counter;
}

int old_helper(void) {
    return 1;
}

int old_pair(void) {
    return 2;
}
