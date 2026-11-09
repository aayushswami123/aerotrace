/* CMocka layouts the line check alone would miss (review of #34). */
#include "calc.h"
#include "cmocka.h"

/* Same name as the helper in cmocka_other.c, which calls sub: reason paths must not mix them up. */
static int helper(void) {
    set_mode(2);
    return 4;
}

static void split_works(void **state) {
    (void)state;
    assert_int_equal(4, helper());
}

static int group_setup(void **state) {
    (void)state;
    return add(0, 0);
}

int main(void) {
    const struct CMUnitTest tests[] = {
        cmocka_unit_test(
            split_works),
    };
    return cmocka_run_group_tests(tests, group_setup, NULL);
}
