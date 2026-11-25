"""Count the Claude tokens in every tool answer an agent would read. Benchmark only, never the product.

Usage (needs Anthropic credentials, e.g. ANTHROPIC_API_KEY, and the network):
  uv run --with anthropic python bench/truth/count_tokens.py <run-dir>

Writes <run-dir>/tokens.json: {sha256 of the text: tokens}. Texts already in the file are not sent
again, so scoring and the results page work offline afterwards. The count is the `count_tokens`
input_tokens for one user message holding the text, minus the same count for a one-character message
plus one, which leaves the text's own tokens. An empty answer counts 0 and is not sent.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-5-5"


def main() -> None:
    run = Path(sys.argv[1]).resolve()
    out = run / "tokens.json"
    cache: dict[str, int] = json.loads(out.read_text()) if out.exists() else {}
    client = anthropic.Anthropic()

    def count(text: str) -> int:
        return client.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": text}]
        ).input_tokens

    overhead = count(".") - 1
    texts = {}
    for f in sorted((run / "raw").glob("*/*.json")):
        t = json.loads(f.read_text()).get("text", "")
        texts[hashlib.sha256(t.encode("utf-8", "surrogateescape")).hexdigest()] = t
    todo = [(h, t) for h, t in sorted(texts.items()) if h not in cache]
    for i, (h, t) in enumerate(todo, 1):
        cache[h] = 0 if not t.strip() else count(t) - overhead
        if i % 20 == 0:
            out.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    meta = {"_model": MODEL, "_overhead_tokens": overhead}
    out.write_text(json.dumps({**cache, **meta}, indent=1, sort_keys=True) + "\n")
    print(f"{len(todo)} counted, {len(texts)} texts, overhead {overhead}")


if __name__ == "__main__":
    main()
