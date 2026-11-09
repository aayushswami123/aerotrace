#include "ring.h"

int cpp_push(ring *r) { return ring_full(r) ? -1 : 0; }
