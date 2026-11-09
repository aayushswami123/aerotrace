## What

<!-- One or two sentences. Link the issue: "Closes #12". -->

## Why

<!-- The problem this solves. Which build item, if any. -->

## How I tested it

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run pytest`
- [ ] New or changed behaviour has a fixture test
- [ ] Tried it by hand (say how):

## Accuracy impact

<!-- Does this change any analysis answer? Which goldens changed and why?
     Could it make AeroTrace miss a caller, a write, or a test it used to find? -->

- [ ] No analysis answers change
- [ ] Answers change, explained above

## Checklist

- [ ] No network calls, AI calls or telemetry added to the analysis
- [ ] Output stays deterministic (sorted, no timestamps or machine paths)
- [ ] `CHANGELOG.md` updated if users will notice
- [ ] Docs or tool descriptions updated
- [ ] New dependency? Reason given above
