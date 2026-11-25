"""Take the launch screenshots into docs/launch/screenshots/, with MANIFEST.json (dev-only).

Usage: uv run --with playwright python bench/ui/launch_screenshots.py <demo-repo>
<demo-repo>: a repo with an AeroTrace index and saved checks (the Redis 8.10.3 demo: pinned release,
indexed, three real `aerotrace check` answers on benchmark patches, see docs/launch/screenshots/README.md).

Refuses any page marked as sample data (`data-sample`), so no mockup can end up here. Every image in the
manifest names the data it shows by sha256: the results page's `results.json`, or a snapshot of what
the dashboard answered (saved next to the images in data/). tests/ui/test_design.py checks all of this.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from aerotrace.ui import api, server

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "launch" / "screenshots"
RUN = ROOT / "bench" / "results" / "2026-11-24-c7df917"
CHECK = "0003-redis-04"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(name: str, value: object) -> str:
    """Save what the dashboard answered for a screenshot; return its sha256."""
    body = (json.dumps(value, indent=1, sort_keys=True) + "\n").encode()
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "data" / name).write_bytes(body)
    return sha(body)


def refuse_samples(pg) -> None:
    if pg.evaluate("() => document.documentElement.hasAttribute('data-sample')"):
        raise SystemExit("refusing to screenshot a page marked as sample data")


def main() -> None:
    demo = Path(sys.argv[1]).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    images = []
    ov = snapshot("overview.json", {"overview": api.overview(demo), "runinfo": api.runinfo(demo)})
    gr = snapshot(f"graph-{CHECK}.json", api.graph(demo, CHECK))
    results_sha = sha((RUN / "results.json").read_bytes())
    srv = server.DashboardServer(demo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = srv.url.split("?")[0]
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        for theme in ("light", "dark"):
            ctx = b.new_context(
                viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme=theme
            )
            pg = ctx.new_page()
            pg.goto(srv.url)
            pg.wait_for_selector(".stat", timeout=20000)
            refuse_samples(pg)
            pg.wait_for_timeout(300)
            name = f"overview-{theme}.png"
            pg.screenshot(path=str(OUT / name))
            images.append(
                {
                    "file": name,
                    "page": "aerotrace ui #/overview",
                    "data": "data/overview.json",
                    "data_sha256": ov,
                }
            )
            pg.goto(base + "#/graph/" + CHECK)
            pg.wait_for_selector(".stage canvas", timeout=20000)
            pg.wait_for_timeout(600)
            refuse_samples(pg)
            name = f"graph-{theme}.png"
            pg.screenshot(path=str(OUT / name))
            images.append(
                {
                    "file": name,
                    "page": f"aerotrace ui #/graph/{CHECK}",
                    "data": f"data/graph-{CHECK}.json",
                    "data_sha256": gr,
                }
            )
            ctx.close()
        ctx = b.new_context(viewport={"width": 1280, "height": 1000}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto((ROOT / "docs" / "benchmark" / "index.html").as_uri())
        pg.wait_for_selector("#chart svg", timeout=20000)
        refuse_samples(pg)
        pg.wait_for_timeout(300)
        name = "results-light.png"
        pg.screenshot(path=str(OUT / name))
        images.append({"file": name, "page": "docs/benchmark/index.html", "data": "bench/results/2026-11-24-c7df917/results.json",
                       "data_sha256": results_sha})  # fmt: skip
        ctx.close()
        b.close()
    srv.shutdown()
    srv.server_close()
    manifest = {"about": "Launch screenshots. Each image names the data it shows (sha256). Made by bench/ui/launch_screenshots.py.",
                "images": sorted(images, key=lambda x: x["file"])}  # fmt: skip
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print("\n".join(i["file"] for i in images))


if __name__ == "__main__":
    main()
