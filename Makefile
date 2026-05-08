.PHONY: test test-unit uat uat-fast uat-comprehensive lint

# All non-UAT unit/integration tests.
test test-unit:
	python -m pytest --ignore=tests/uat -q

# Full UAT suite end-to-end against a live LLM (Codex by default).
# Override NUR_UAT_BACKEND for a different backend.
NUR_UAT_BACKEND ?= codex
uat:
	NUR_UAT_LIVE=1 NUR_UAT_BACKEND=$(NUR_UAT_BACKEND) python -m pytest tests/uat/ -m "uat and not comprehensive" --tb=short

# Single PASS/FAIL aggregator that runs the entire UAT suite via subprocess.
# Use this when you want one test result for "is the product green?".
uat-comprehensive:
	NUR_UAT_LIVE=1 NUR_UAT_BACKEND=$(NUR_UAT_BACKEND) python -m pytest tests/uat/ -m comprehensive -v

# UAT skipped (no live LLM) — useful as a smoke-collection check.
uat-fast:
	python -m pytest tests/uat/ --collect-only -q

lint:
	python -m pyflakes pipeline.py runtime/ interface/ core/ tests/ 2>&1 || true
