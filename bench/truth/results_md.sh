#!/bin/sh
# Rebuild docs/benchmark/RESULTS.md: the hand-written text, then the tables generated from results.json.
set -e
here=$(cd "$(dirname "$0")/../.." && pwd)
run=${1:-$here/bench/results/2026-11-24-c7df917}
{ cat "$here/docs/benchmark/RESULTS.head.md"; echo; python3 -I "$here/bench/truth/tables.py" "$run"; } > "$here/docs/benchmark/RESULTS.md"
