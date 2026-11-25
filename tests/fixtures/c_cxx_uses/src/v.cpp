#include "v.h"

V operator+(V a, V b) {
    return V{a.x + b.x};
}

W::operator int() const {
    return v;
}

X::X(int a) : k(a) {}

X::~X() {}
