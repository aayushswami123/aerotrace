#include "filter.h"

static void filter_destroy(int x) { (void)x; }

static int filter_open(int x) { return x; }

/* curl-05: a filter table names the static functions */
const struct filter http_filter = {"http", filter_destroy, filter_open};
