/* A tiny stand-in for GoogleTest: TEST and TEST_F expand to a class and its TestBody, as the real
   macros do (class <Suite>_<Name>_Test). */
#ifndef GTEST_H
#define GTEST_H
namespace testing {
class Test {
  public:
    virtual ~Test() {}
    virtual void SetUp() {}
    virtual void TearDown() {}
    virtual void TestBody() = 0;
};
}  // namespace testing
#define GTEST_CLASS_(suite, name) suite##_##name##_Test
#define GTEST_TEST_(suite, name, parent)              \
    class GTEST_CLASS_(suite, name) : public parent { \
      public:                                         \
        void TestBody() override;                     \
    };                                                \
    void GTEST_CLASS_(suite, name)::TestBody()
#define TEST(suite, name) GTEST_TEST_(suite, name, ::testing::Test)
#define TEST_F(fixture, name) GTEST_TEST_(fixture, name, fixture)
#define TEST_P(fixture, name) GTEST_TEST_(fixture, name, fixture)
/* Typed tests: a class template per test, as in the real header. */
#define TYPED_TEST(suite, name)                                                  \
    template <typename gtest_TypeParam_>                                         \
    class GTEST_CLASS_(suite, name) : public suite<gtest_TypeParam_> {           \
      public:                                                                    \
        void TestBody() override;                                                \
    };                                                                           \
    template <typename gtest_TypeParam_>                                         \
    void GTEST_CLASS_(suite, name)<gtest_TypeParam_>::TestBody()
#define EXPECT_EQ(a, b) ((void)((a) == (b)))
#endif
