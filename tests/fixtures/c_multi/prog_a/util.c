static int count;

static void bump(void) {
    count++;
}

int util_run(void) {
    bump();
    return count;
}
