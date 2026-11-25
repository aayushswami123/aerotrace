int old_fn(int a);

int plain(void) {
    return old_fn(3);
}

int main(void) {
    int r = old_fn(1);
#ifdef FEATURE
    r += old_fn(2);
#endif
    return r;
}
