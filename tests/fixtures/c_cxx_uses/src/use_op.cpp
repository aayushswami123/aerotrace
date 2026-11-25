#include "v.h"

int use_op() {
    V a{1}, b{2};
    V c = a + b;
    return c.x;
}
