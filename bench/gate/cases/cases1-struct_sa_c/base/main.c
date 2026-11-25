struct s { int a; int b; };
_Static_assert(sizeof(struct s) == 8, "abi");
int f(struct s *p) { return p->a; }
int main(void){return 0;}
