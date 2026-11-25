#include "a.h"
void (*p)() noexcept = f;
int main(){p();}
