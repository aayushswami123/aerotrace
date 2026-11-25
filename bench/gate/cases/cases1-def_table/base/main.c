enum op {
#define X(n) OP_##n,
#include "ops.def"
#undef X
};
int main(void) { return OP_SUB; }
