#include "b.h"
struct D : B { int f() override { return 3; } };
int main() { D d; return d.f(); }
