// Layouts the line check alone would miss (review of #34), each calling mul directly.
#include "calc.h"
#include "gtest/gtest.h"

// The macro split over two lines, as clang-format does for long names.
TEST(Wrapped,
     LongName) { EXPECT_EQ(6, mul(2, 3)); }

// A project macro around TEST.
#define CALC_TEST(name) TEST(CalcMacro, name)
CALC_TEST(ViaMacro) { EXPECT_EQ(4, mul(2, 2)); }

// Value-parametrized: GoogleTest names it Prefix/Param.Squares/0.
class Param : public ::testing::Test {};
TEST_P(Param, Squares) { EXPECT_EQ(9, mul(3, 3)); }

// Typed: GoogleTest names it Typed/0.Doubles.
template <typename T>
class Typed : public ::testing::Test {};
TYPED_TEST(Typed, Doubles) { EXPECT_EQ(8, mul(2, 4)); }
