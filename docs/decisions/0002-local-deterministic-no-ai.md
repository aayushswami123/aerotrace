# 0002: The analysis is local, deterministic, and has no AI inside

- Status: accepted
- Date: 2026-11-08

## Context

Our users work on safety-critical code (aerospace, automotive, medical, embedded). Their code often can't
leave the machine. Change-impact evidence may end up in certification work, so it must be repeatable and
explainable.

## Decision

- No network calls at analysis time, and no telemetry.
- No LLMs, embeddings or ML models in `src/aerotrace/`. The agent using AeroTrace is the AI; we are the
  ground truth it checks against.
- The same inputs give byte-for-byte the same answers: sorted output, stable IDs, no timestamps in answers.

## Consequences

- We can't "fill gaps" with a model. Gaps are shown as blind spots or labelled links instead.
- Golden-file tests work, because answers don't change from run to run.
