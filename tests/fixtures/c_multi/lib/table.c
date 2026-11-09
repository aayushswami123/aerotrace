#include "shared.h"

typedef void (*handler)(void);

static void on_a(void) { add(1); }
static void on_b(void) { add(2); }

static handler table[] = {on_a, on_b};

void dispatch(int i) {
    table[i]();
}
