struct B { virtual int f(); virtual int g(); };
inline int B::f(){return 1;}
inline int B::g(){return 2;}
