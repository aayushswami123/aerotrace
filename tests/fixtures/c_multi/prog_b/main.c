#include "shared.h"

int util_run_b(void);

int main(void) {
    dispatch(0);
    calls_missing();
    return util_run_b() + get();
}
