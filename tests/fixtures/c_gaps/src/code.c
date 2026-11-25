#include "gaps.h"

/* #52: x++ and x += n read and write */
void bump_level(void) { level++; }
void pre_bump_level(void) { ++level; }
void add_level(int n) { level += n; }
void or_port(void) { server.port |= 1; }
void set_level(void) { level = 1; }

/* #37 and #26 inside functions */
int local_designated(void) { struct inner i = {.y = 1}; return i.x; }
int static_designated(void) { static struct inner s = {.y = 2}; return s.x; }
int local_positional(void) { struct inner p = {1, 2}; return p.x; }
int local_zero(void) { struct inner z = {0}; return z.x; }
void literal_positional(struct inner *o) { *o = (struct inner){3, 4}; }

/* #24 */
size_t y_offset(void) { return offsetof(struct inner, y); }

/* #18: an element passed by value is not the array's address */
void pass_element(void) { use_int(server.pipe[0]); }
void pass_array(void) { fill_pipe(server.pipe); }
void pass_row(void) { use_ptr(server.names[0]); }
int *element_address(void) { return &server.pipe[0]; }
