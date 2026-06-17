# Local CI — replaces the disabled GitHub Actions workflows. `make preflight` = full gate;
# existing pre-commit pre-push hooks auto-gate every push.
.PHONY: preflight ci
preflight ci:
	@tools/preflight.sh
