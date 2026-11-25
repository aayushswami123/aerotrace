# Housekeeping targets. The product itself is built with uv (see CLAUDE.md).

.PHONY: gate gate-baseline

# Run the gate cases against the AeroTrace code in this checkout (see bench/gate/README.md).
# Options: ONLY=<case id>, AT="<aerotrace command>", BEFORE="<older aerotrace command>".
gate:
	uv run python bench/gate/run_gate.py $(if $(ONLY),--only $(ONLY)) $(if $(AT),--at "$(AT)") $(if $(BEFORE),--before "$(BEFORE)")

# Same, and write the report to bench/gate/BASELINE.md.
gate-baseline:
	uv run python bench/gate/run_gate.py --markdown bench/gate/BASELINE.md $(if $(AT),--at "$(AT)") $(if $(BEFORE),--before "$(BEFORE)")
