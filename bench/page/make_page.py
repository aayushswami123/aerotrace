"""Write docs/benchmark/index.html and the packaged src/aerotrace/ui/web/benchmark.json from a run (dev-only).

Usage: uv run python bench/page/make_page.py [bench/results/<run>]
"""

from __future__ import annotations

import sys
from pathlib import Path

from aerotrace.ui import bench_page

ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "bench" / "results" / "2026-11-24-c7df917"
    pkg = ROOT / "src" / "aerotrace" / "ui" / "web" / "benchmark.json"  # the dashboard's Benchmark page
    print(bench_page.write(run, ROOT / "docs" / "benchmark" / "index.html", pkg))
