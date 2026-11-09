/* Same helper name as cmocka_more.c, but this one calls sub, not divide. */
#include "calc.h"
#include "cmocka.h"

static int helper(void) { return sub(3, 1); }

static void other_works(void **state) {
    (void)state;
    assert_int_equal(2, helper());
}

int main(void) {
    const struct CMUnitTest tests[] = {cmocka_unit_test(other_works)};
    return cmocka_run_group_tests(tests, NULL, NULL);
}
