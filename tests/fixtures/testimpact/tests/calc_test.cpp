#include "calc.h"
#include "gtest/gtest.h"

TEST(Calc, Adds) { EXPECT_EQ(3, add(1, 2)); }

class CalcFixture : public ::testing::Test {
  protected:
    void SetUp() override { set_mode(1); }
};

TEST_F(CalcFixture, AppliesSub) { EXPECT_EQ(1, apply(sub, 2, 1)); }

TEST_F(CalcFixture, KnowsMode) { EXPECT_EQ(0, mode_name() == nullptr); }

#if 0
TEST(Calc, DividesDisabled) { EXPECT_EQ(2, divide(4, 2)); }
#endif
