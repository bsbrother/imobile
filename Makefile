# iMobile Makefile — common dev tasks.
# Usage: make <target>   (see `make help`)

PYTHON := .venv/bin/python
PYTEST := .venv/bin/python -m pytest

.PHONY: help test test-integration test-fast backtest lint clean

help:
	@echo "iMobile Makefile targets:"
	@echo "  test             Run unit tests (skips integration, ~45s)"
	@echo "  test-integration Run all tests including network-dependent ones"
	@echo "  test-fast        Run only fast unit tests (no integration, no freeride)"
	@echo "  backtest         Run ts_7AZ baseline backtest (20260101-20260619, ~44min)"
	@echo "  lint             Run ruff + pyright on backtest/ and tests/"
	@echo "  clean            Remove __pycache__, .pytest_cache, .ruff_cache"

test:
	$(PYTEST) tests/ -v --timeout=30

test-integration:
	$(PYTEST) tests/ -v --timeout=60 --run-integration

test-fast:
	$(PYTEST) tests/ -v --timeout=10 -k "baseline_regression or freeride"

backtest:
	$(PYTHON) backtest/engine.py 20260101 20260619 ts_7AZ --no-search --no-ai

lint:
	@command -v ruff >/dev/null 2>&1 && ruff check backtest/ tests/ || echo "ruff not installed, skipping"
	@command -v pyright >/dev/null 2>&1 && pyright backtest/ tests/ || echo "pyright not installed, skipping"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
