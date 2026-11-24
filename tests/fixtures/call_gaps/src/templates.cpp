// Calls inside templates that depend on the template type (#33), and overloaded functions used as a
// value in a template (#31).
struct Base {
    virtual int run(int x) { return x; }
    virtual ~Base() {}
};
struct D : Base {
    int run(int x) override { return x * 2; }
    static int sm(int a) { return a; }
};
int free_fn(D &d) { return d.run(3); }

template <class T> int tcall(T &t) { return t.run(5); }
template <class T> int tstatic() { return T::sm(1); }
template <class T> int tfree(T &t) { return free_fn(t); }
int inst(D &d) { return tcall(d) + tstatic<D>() + tfree(d); }
int direct(D &d) { return d.run(1); }
int lonely(int a) { return a; }

int ov(int a) { return a; }
int ov(int a, int b) { return a + b; }
template <class T> void g1() {
    T p = ov;
    (void)p;
}
void inst_ov() { g1<int (*)(int)>(); }
int ptrcall(int (*p)(int)) { return p(1); }
