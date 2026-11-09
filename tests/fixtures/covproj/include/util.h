#ifndef UTIL_H
#define UTIL_H
/* Inline in a header, used from both .c files: gcov reports it once per file; the counts add up. */
static inline int twice(int x) { return x + x; }
int fa(int x);
int fb(int x);
int fx(int x);
#endif
