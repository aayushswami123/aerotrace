#include <dlfcn.h>
int main(void){void*p=dlsym(RTLD_DEFAULT,"helper");return p!=0;}
