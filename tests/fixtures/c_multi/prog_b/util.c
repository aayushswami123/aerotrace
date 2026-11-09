static int count;

static void bump(void) {
    count += 2;
}

int util_run_b(void) {
    bump();
    return count;
}
