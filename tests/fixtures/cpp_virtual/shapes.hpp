#pragma once

struct Shape {
    virtual ~Shape() = default;
    virtual int area() const = 0;
    int calls_ = 0;
};

struct Square : Shape {
    explicit Square(int side) : side_(side) {}
    int area() const override;
    int side_;
};

struct Circle : Shape {
    int area() const override;
};

int total(const Shape& s);
int fixed(const Square& s);
