// C++ forms of a call that does not compile (#14). In C++ libclang keeps the return type of such a call
// and names the function as an overload set or a member.
int pair(int a, int b) { return a + b; }
int none() { return 0; }
int ov(int a) { return a; }
int ov(int a, int b) { return a + b; }
template <class T> int tf(T a, T b) { return a + b; }
namespace ns {
int nf(int a, int b) { return a * b; }
}

struct Box {
    int scale(int x, int y) { return x * y; }
    int mo(int a) { return a; }
    int mo(int a, int b) { return a + b; }
    static int sm(int a, int b) { return a - b; }
    virtual int area(int unit) { return unit; }
    virtual ~Box() {}
};
struct Square : Box {
    int area(int unit) override { return 2 * unit; }
};

#define CALL_PAIR(x) pair(x)

int bad_free() { return pair(1); }
int bad_zero_args() { return pair(); }
int bad_too_many() { return none(1); }
void bad_ignored() { pair(1, 2, 3); }
int bad_overload() { return ov(1, 2, 3); }
int bad_template() { return tf(1); }
int bad_qualified() { return ns::nf(1); }
int bad_method(Box &b) { return b.scale(3); }
int bad_method_ptr(Box *b) { return b->scale(1, 2, 3); }
int bad_overloaded_method(Box &b) { return b.mo(1, 2, 3); }
int bad_static() { return Box::sm(1); }
int bad_virtual(Box *b) { return b->area(); }
int bad_paren() { return (pair)(1); }
int bad_macro() { return CALL_PAIR(1); }
int bad_nested() { return pair(pair(1), 2, 3); }

// Functions passed as values, never calls. They must stay "address taken" or unresolved, never calls.
void take(int (*f)(int, int));
template <class T> void h(T t, int (*f)(int));
template <class T> T v_construct() { return T(pair); }
template <class T> T v_paren_init() {
    T x(pair, 1);
    return x;
}
template <class T> T v_overload_init() {
    T x(ov, 1);
    return x;
}
template <class T> T v_overload_one() {
    T x(ov);
    return x;
}
template <class T> void v_pass(T t) { h(t, ov); }
void good_take() { take(pair); }
