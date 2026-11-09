#ifndef SHARED_H
#define SHARED_H
extern int g_total;
void add(int x);
int get(void);
int next_id(void);
void dispatch(int i);
void declared_only(void);
void calls_missing(void);
#endif
