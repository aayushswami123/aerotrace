/* A header function compiled as C (ring.c) and as C++ (ring_use.cpp): two copies in the index (#40). */
typedef struct ring {
    int head, tail, size;
} ring;

static inline int ring_full(const ring *r) {
    return (r->head + 1) % r->size == r->tail;
}
