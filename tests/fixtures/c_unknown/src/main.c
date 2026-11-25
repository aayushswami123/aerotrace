#include "defs.h"

int user(void);
int lib_entry(void);
int still_here(int a);

int main(void) {
    return user() + lib_entry() + still_here(0);
}
