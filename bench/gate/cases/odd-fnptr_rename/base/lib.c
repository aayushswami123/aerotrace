#include "lib.h"
static int id(int a){return a;}
int (*handler)(int) = id;
