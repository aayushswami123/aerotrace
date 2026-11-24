// A virtual override whose parameters change stops overriding (#32).
struct Box {
    virtual int area(int u) { return u; }
    virtual ~Box() {}
};
struct Big : Box {
    int area(int u) override { return 3 * u; }
};
int use_area(Box *b) { return b->area(1); }
int top(Box *b) { return use_area(b); }
int on_box(Box &b) { return b.Box::area(2); }
