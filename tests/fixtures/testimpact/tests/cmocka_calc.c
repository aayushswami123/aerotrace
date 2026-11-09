#include "calc.h"
#include "cmocka.h"

static int fast_mode(void **state) {
    (void)state;
    set_mode(1);
    return 0;
}

static void mul_works(void **state) {
    (void)state;
    assert_int_equal(6, mul(2, 3));
}

static void name_works(void **state) {
    (void)state;
    assert_int_equal(1, mode_name() != NULL);
}

int main(void) {
    const struct CMUnitTest tests[] = {
        cmocka_unit_test(mul_works),
        cmocka_unit_test_setup_teardown(name_works, fast_mode, NULL),
    };
    return cmocka_run_group_tests(tests, NULL, NULL);
}
