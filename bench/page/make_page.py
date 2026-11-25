"""Write docs/benchmark/index.html from a benchmark run (dev-only).

Usage: uv run python bench/page/make_page.py [bench/results/<run>]
"""

from __future__ import annotations

import sys
from pathlib import Path

from aerotrace.ui import bench_page

ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "bench" / "results" / "2026-11-24-c7df917"
    print(bench_page.write(run, ROOT / "docs" / "benchmark" / "index.html"))
