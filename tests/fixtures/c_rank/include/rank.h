#ifndef RANK_H
#define RANK_H
typedef int (*binop)(int, int);
extern binop t1[];
extern binop t2[];
int f(int a, int b);
void note(int x);
int direct_caller(void);
int both_ways(void);
int dispatch1(int i);
int dispatch2(int i);
#endif
