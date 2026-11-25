#include "lib.h"
int main(void) {
#ifdef USE_F
  return f(1);
#else
  return 0;
#endif
}
