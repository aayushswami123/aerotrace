# Housekeeping targets. The product itself is built with uv (see CLAUDE.md).

.PHONY: gate gate-baseline

# Run the gate cases and compare with bench/gate/baseline.json. Exit 1 only if something got worse.
# Options: ONLY=<case id>, AT="<aerotrace command>", BEFORE="<older aerotrace command>".
gate:
	uv run python bench/gate/run_gate.py $(if $(ONLY),--only $(ONLY)) $(if $(AT),--at "$(AT)") $(if $(BEFORE),--before "$(BEFORE)")

# Record a new baseline (bench/gate/baseline.json and BASELINE.md). Do this on purpose, in a PR.
gate-baseline:
	uv run python bench/gate/run_gate.py --write-baseline $(if $(AT),--at "$(AT)") $(if $(BEFORE),--before "$(BEFORE)")
