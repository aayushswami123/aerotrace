#include "shapes.hpp"

int Square::area() const { return side_ * side_; }

int Circle::area() const { return 3; }

int total(const Shape& s) {
    return s.area();
}

int fixed(const Square& s) {
    return s.Square::area();
}
