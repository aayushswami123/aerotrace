/* A tiny stand-in for CMocka's header: tests are listed in an array of structs holding pointers. */
#ifndef CMOCKA_H
#define CMOCKA_H
#include <stddef.h>
typedef void (*CMUnitTestFunction)(void **state);
typedef int (*CMFixtureFunction)(void **state);
struct CMUnitTest {
    const char *name;
    CMUnitTestFunction test_func;
    CMFixtureFunction setup_func;
    CMFixtureFunction teardown_func;
    void *initial_state;
};
#define cmocka_unit_test(f) {#f, f, NULL, NULL, NULL}
#define cmocka_unit_test_setup_teardown(f, setup, teardown) {#f, f, setup, teardown, NULL}
int _cmocka_run_group_tests(const char *group_name, const struct CMUnitTest *const tests,
                            const size_t num_tests, CMFixtureFunction group_setup,
                            CMFixtureFunction group_teardown);
#define cmocka_run_group_tests(group_tests, group_setup, group_teardown)                               \
    _cmocka_run_group_tests(#group_tests, group_tests, sizeof(group_tests) / sizeof((group_tests)[0]), \
                            group_setup, group_teardown)
void _assert_int_equal(long a, long b, const char *file, int line);
#define assert_int_equal(a, b) _assert_int_equal((a), (b), __FILE__, __LINE__)
#endif
