# Screenshots

- `html-report-redis-*.png`, `terminal-redis-checkStringLength.txt`: REAL output of
  `aerotrace check --patch redis-checkStringLength.patch` on pinned Redis 8.10.3 (adds a parameter to
  `checkStringLength` without updating callers). The 2 breaks match grep (3 call sites in 2 functions).
- `html-report-sample-*.png`, `terminal-sample*.txt`: the UI's invented sample data (`tests/fixtures/ui/check-v1-sample.json`).
