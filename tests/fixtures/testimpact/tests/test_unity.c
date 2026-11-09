#include "calc.h"
#include "unity.h"

void test_add(void) { TEST_ASSERT_EQUAL(3, add(1, 2)); }

/* Defined but never registered: it never runs. */
void test_unused(void) { TEST_ASSERT_EQUAL(1, divide(2, 2)); }

void test_sub_disabled(void) { TEST_ASSERT_EQUAL(1, sub(2, 1)); }

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_add);
    /* RUN_TEST(test_unused); */
#if 0
    RUN_TEST(test_sub_disabled);
#endif
    return UNITY_END();
}
