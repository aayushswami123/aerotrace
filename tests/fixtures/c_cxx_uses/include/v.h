struct V {
    int x;
};
V operator+(V a, V b);
struct W {
    int v;
    operator int() const;
};
struct X {
    X(int a);
    ~X();
    int k;
};
