#include "lib.h"
int main(void){struct S s={0};
#ifdef X
return s.b;
#endif
return s.a;}
