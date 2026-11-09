/* Unity registrations the line check alone would miss (review of #34), each test calling scale. */
#include "calc.h"
#include "unity.h"

#define MY_RUN(t) RUN_TEST(t)

void test_split(void) { TEST_ASSERT_EQUAL(20, scale(2)); }
void test_wrapped(void) { TEST_ASSERT_EQUAL(30, scale(3)); }
void test_generated(void) { TEST_ASSERT_EQUAL(40, scale(4)); }
void test_table(void) { TEST_ASSERT_EQUAL(50, scale(5)); }

/* Like Unity's generate_test_runner.rb output. */
static void run_test(void (*func)(void), const char *name, int line) { UnityDefaultTestRun(func, name, line); }

/* Like a Ceedling runner's parameter table. */
struct run_test_params { void (*func)(void); };
static struct run_test_params run_test_params_arr[1];

/* Only used through a pointer, and not a test: it must be named in the limits, not listed. */
static int pick_op(int (*op)(int, int)) { return op(1, 2); }

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(
        test_split);
    MY_RUN(test_wrapped);
    run_test(test_generated, "test_generated", 12);
    run_test_params_arr[0].func = test_table;
    run_test_params_arr[0].func();
    pick_op(sub);
    return UNITY_END();
}
