#include "ring.h"

int c_push(ring *r) { return ring_full(r) ? -1 : 0; }
