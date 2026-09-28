.PHONY: install test lint format check audit mutation flaky-rate clean

# Every target runs the tools pinned in uv.lock.
install:
	uv sync --locked --all-extras

# Tests run under a throwaway HOME, with no XDG or cubby variable, so neither a
# test nor the code under test can reach the real cubby state (it did once), and
# a cubby agent running on this machine cannot fail the suite's guard
# (tests/real_state_guard.py). uv keeps its own cache.
ISOLATED = env -u XDG_STATE_HOME -u XDG_CONFIG_HOME -u XDG_DATA_HOME -u CUBBY_STATE_DIR \
	-u CUBBY_CONFIG HOME="$$home" UV_CACHE_DIR="$$(uv cache dir)"

test:
	home=$$(mktemp -d) && trap 'rm -rf "$$home"' EXIT && \
		$(ISOLATED) uv run --locked pytest --cov

lint:
	uv run --locked ruff format --check src tests benchmarks scripts
	uv run --locked ruff check src tests benchmarks scripts
	uv run --locked mypy src
	uv run --locked lint-imports

format:
	uv run --locked ruff format src tests benchmarks scripts

check: lint test

audit:
	uv export --locked --all-extras --no-emit-project --format requirements-txt > .locked.txt
	uv run --locked pip-audit --strict --disable-pip --no-deps -r .locked.txt
	uv run --locked bandit -c pyproject.toml -r src -q

mutation:
	@# mutmut counts a session the guard failed as a killed mutant: the guard
	@# also appends to CUBBY_TEST_GUARD_REPORT, and a non-empty report fails the
	@# target. Run mutation through this target, never `mutmut run` directly.
	home=$$(mktemp -d) && trap 'rm -rf "$$home"' EXIT && \
		$(ISOLATED) CUBBY_TEST_GUARD_REPORT="$$home/guard-violations.txt" \
		uv run --locked mutmut run && \
		if [ -s "$$home/guard-violations.txt" ]; then \
			cat "$$home/guard-violations.txt" >&2; \
			echo "mutation: a mutant touched the real home's cubby state (above); mutmut counted it as killed" >&2; \
			exit 1; \
		fi
	@# Every mutant must end killed, timed out or survived (then reviewed in
	@# docs/audits): an interrupted run leaves some "not checked", and "no tests",
	@# "skipped" or "suspicious" are mutants nothing checked. Fails closed: an
	@# error from `mutmut results` fails the target too.
	uv run --locked mutmut results --all true > .mutmut-results.txt
	@if grep ': ' .mutmut-results.txt | grep -Ev ': (killed|timeout|survived)$$'; then \
		echo "mutation: the mutants above were not checked" >&2; exit 1; \
	fi
	@grep -q ': killed$$' .mutmut-results.txt || { echo "mutation: no mutant was killed" >&2; exit 1; }

# How often CI fails on a commit that later passes unchanged (needs gh).
flaky-rate:
	uv run --locked python scripts/flaky_rate.py

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .locked.txt .mutmut-results.txt mutants **/__pycache__ *.egg-info build dist
