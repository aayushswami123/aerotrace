/* A tiny stand-in for Unity's header: RUN_TEST passes the test function as a pointer. */
#ifndef UNITY_H
#define UNITY_H
void UnityDefaultTestRun(void (*func)(void), const char *name, int line);
void UnityBegin(const char *file);
int UnityEnd(void);
#define UNITY_BEGIN() UnityBegin(__FILE__)
#define UNITY_END() UnityEnd()
#define RUN_TEST(func) UnityDefaultTestRun(func, #func, __LINE__)
#define TEST_ASSERT_EQUAL(a, b) ((void)((a) == (b)))
#endif
