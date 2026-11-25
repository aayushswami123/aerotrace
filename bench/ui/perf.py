"""Measure the dashboard graph: time to first frame and frame rate while panning and zooming (dev-only).

Usage: uv run --with playwright python bench/ui/perf.py [out.json]
Serves synthetic checks (tests/ui/samples.py big(n), 1,000 and 5,000 nodes) from a temp folder on
127.0.0.1 and drives Chrome. For each size it measures two views: the default (BREAKS and MAY BREAK
only) and every group switched on. Numbers are from this machine; they are printed and saved as JSON.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "ui"))
import samples  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from aerotrace.index import store  # noqa: E402
from aerotrace.ui import api, server  # noqa: E402

SIZES = (1000, 5000)
PAN_ZOOM = """async () => {
  const cv = document.querySelector('.stage canvas'), r = cv.getBoundingClientRect();
  let frames = 0, run = true;
  const g0 = window.__aerotraceGraph, f0 = g0.frames, ms0 = g0.totalMs || 0; g0.maxMs = 0;
  const tick = () => { frames++; if (run) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  const t0 = performance.now();
  for (let i = 0; i < 120; i++) {   // 2 s of input at 60 events a second: wheel zoom, then drag pan
    const x = r.left + r.width / 2 + Math.sin(i / 9) * 120, y = r.top + r.height / 2 + Math.cos(i / 7) * 80;
    if (i % 2) cv.dispatchEvent(new WheelEvent('wheel', { clientX: x, clientY: y, deltaY: (i % 40 < 20 ? -40 : 40), bubbles: true, cancelable: true }));
    else {
      cv.dispatchEvent(new PointerEvent('pointerdown', { clientX: x, clientY: y, pointerId: 1, bubbles: true }));
      cv.dispatchEvent(new PointerEvent('pointermove', { clientX: x + 40, clientY: y + 25, pointerId: 1, bubbles: true }));
      cv.dispatchEvent(new PointerEvent('pointerup', { clientX: x + 40, clientY: y + 25, pointerId: 1, bubbles: true }));
    }
    await new Promise(res => setTimeout(res, 1000 / 60));
  }
  run = false;
  const secs = (performance.now() - t0) / 1000;
  const g1 = window.__aerotraceGraph, draws = g1.frames - f0;
  return { fps: frames / secs, drawMs: draws ? (g1.totalMs - ms0) / draws : 0, maxMs: g1.maxMs, draws: draws, shown: g1.shown };
}"""


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        d = store.index_dir(root) / api.CHECKS_DIR
        d.mkdir(parents=True)
        for n in SIZES:
            (d / f"{n:04d}-aaaa{n:04d}.json").write_text(json.dumps(samples.big(n)))
        srv = server.DashboardServer(root)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome")
            for n in SIZES:
                for view in ("default", "all groups"):
                    ctx = b.new_context(viewport={"width": 1440, "height": 900})
                    pg = ctx.new_page()
                    pg.goto(srv.url)
                    pg.wait_for_selector("#main .page, #main .state")
                    t = pg.evaluate("() => performance.now()")
                    pg.evaluate(f"() => location.hash = '#/graph/{n:04d}-aaaa{n:04d}'")
                    pg.wait_for_function(
                        "() => window.__aerotraceGraph && window.__aerotraceGraph.frames > 0", timeout=60000
                    )
                    first = pg.evaluate("() => performance.now()") - t
                    if view == "all groups":
                        for g in ("affected", "unknown", "no_effect"):
                            if pg.query_selector(f'[data-group="{g}"]'):
                                pg.click(f'[data-group="{g}"]')
                        pg.wait_for_timeout(300)
                    m = pg.evaluate(PAN_ZOOM)
                    results.append({"nodes": n, "view": view, "first_frame_ms": round(first), "fps": round(m["fps"], 1),
                                    "draw_ms_avg": round(m["drawMs"], 1), "draw_ms_max": round(m["maxMs"], 1), "redraws": m["draws"], "drawn": m["shown"]})  # fmt: skip
                    print(results[-1])
                    ctx.close()
            b.close()
        srv.shutdown()
        srv.server_close()
    cpu = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
    ).stdout.strip()
    doc = {"machine": f"{cpu or platform.processor()}, {platform.system()} {platform.mac_ver()[0]}", "browser": "Chrome",
           "results": results}  # fmt: skip
    if out:
        out.write_text(json.dumps(doc, indent=1) + "\n")


if __name__ == "__main__":
    main()
