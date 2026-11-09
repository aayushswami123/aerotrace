#ifndef CALC_H
#define CALC_H
#ifdef __cplusplus
extern "C" {
#endif
#define CALC_LIMIT 100
extern int calc_mode;
int add(int a, int b);
int sub(int a, int b);
int mul(int a, int b);
int divide(int a, int b);
int apply(int (*op)(int, int), int a, int b);
void set_mode(int m);
const char *mode_name(void);
int scale(int x);
#ifdef __cplusplus
}
#endif
#endif
