.PHONY: install test lint format check audit mutation flaky-rate clean

# Every target runs the tools pinned in uv.lock.
install:
	uv sync --locked --all-extras

test:
	uv run --locked pytest --cov

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
	@# Under a throwaway HOME, with no XDG or cubby variable: a mutant that
	@# ignores the test isolation must still not reach the real state folder
	@# (it did once). uv keeps its own cache.
	home=$$(mktemp -d) && trap 'rm -rf "$$home"' EXIT && \
		env -u XDG_STATE_HOME -u XDG_CONFIG_HOME -u XDG_DATA_HOME -u CUBBY_STATE_DIR -u CUBBY_CONFIG \
		HOME="$$home" UV_CACHE_DIR="$$(uv cache dir)" uv run --locked mutmut run
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
