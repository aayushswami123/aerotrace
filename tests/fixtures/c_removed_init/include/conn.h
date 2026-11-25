/* #57: conn::hz is removed in the tests; these file-scope uses of it stop compiling. */
struct conn {
    int a;
    int hz;
};
extern struct conn g;
extern int rate;
