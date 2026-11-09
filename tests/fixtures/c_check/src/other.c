static int helper(int x) {
    return x;
}

int other_user(void) {
    return helper(1);
}
