#include <stdatomic.h>
#include <string.h>

struct stats {
    _Atomic long long requests;
    long long bytes;
    char name[16];
    int slots[8];
};

struct stats st;
long long total;

static void take(long long *p) {
    *p = 0;
}

void on_request(void) {
    atomic_fetch_add_explicit(&st.requests, 1, memory_order_relaxed);
}

void on_bytes(long long n) {
    __atomic_add_fetch(&total, n, __ATOMIC_RELAXED);
    __sync_add_and_fetch(&st.bytes, n);
}

long long read_requests(void) {
    return atomic_load_explicit(&st.requests, memory_order_relaxed);
}

void set_requests(long long v) {
    atomic_store_explicit(&st.requests, v, memory_order_relaxed);
}

void reset(void) {
    memset(&st, 0, sizeof st);
}

void clear_slots(void) {
    memset(st.slots, 0, sizeof st.slots);
}

void rename_stats(const char *n) {
    strncpy(st.name, n, sizeof st.name - 1);
}

void hand_off(void) {
    take(&total);
}

long long *where_total(void) {
    return &total;
}
