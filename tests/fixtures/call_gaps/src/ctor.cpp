// Constructor and operator() calls that do not compile (#21, #39): libclang keeps no reference to them.
namespace ns {
struct K {
    K(int a) : v(a) {}
    K(const char *s) : v(*s) {}
    int v;
};
}  // namespace ns
struct F {
    int operator()(int a) { return a; }
};
struct G {};
void b6() { ns::K k(1, 2); }
void b7() { ns::K(1, 2); }
void b8() { ns::K *p = new ns::K(1, 2); (void)p; }
int use(F &f) { return f(1, 2); }
int ok(F &f) { return f(1); }
void good() { ns::K k(1); }
void none() { G g(1); }
