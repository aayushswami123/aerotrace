# Launch screenshots

Made by `bench/ui/launch_screenshots.py`; listed in `MANIFEST.json` with the sha256 of the data each one
shows. `tests/ui/test_design.py` fails if an image here is not in the manifest, names a mockup, or names
data that is not in the repo. Mockups (sample numbers) can't be screenshotted into this folder.

- `results-light.png`: the benchmark results page, `docs/benchmark/index.html`, generated from
  `bench/results/2026-11-24-c7df917/results.json`. The page has a light theme only.
- `overview-{light,dark}.png`, `graph-{light,dark}.png`: `aerotrace ui` on the Redis 8.10.3 demo. That
  is the pinned release (`tests/realrepo/repos.py`), built and indexed as in `bench/truth/README.md`,
  with three answers that `aerotrace check --patch` saved itself (#80) on benchmark patches (redis-07,
  redis-01, redis-04), with the code on `launch-candidate` after #77. The dates on screen are the real
  save times. The graph shows case redis-04. What the dashboard answered is in `data/`.
- The graph canvas is dark in both themes by design (`docs/design/README.md`); the frame follows the theme.
