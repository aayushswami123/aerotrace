// Not in compile_commands.json (the test fixtures list only .c and .cpp files): AeroTrace can't see it.
#include "calc.h"
#include "gtest/gtest.h"

TEST(Extra, Divides) { EXPECT_EQ(2, divide(4, 2)); }
