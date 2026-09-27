.PHONY: install test lint format check audit mutation clean

# Every target runs the tools pinned in uv.lock.
install:
	uv sync --locked --all-extras

test:
	uv run --locked pytest --cov

lint:
	uv run --locked ruff format --check src tests benchmarks
	uv run --locked ruff check src tests benchmarks
	uv run --locked mypy src
	uv run --locked lint-imports

format:
	uv run --locked ruff format src tests benchmarks

check: lint test

audit:
	uv export --locked --all-extras --no-emit-project --format requirements-txt > .locked.txt
	uv run --locked pip-audit --strict --disable-pip --no-deps -r .locked.txt
	uv run --locked bandit -c pyproject.toml -r src -q

mutation:
	uv run --locked mutmut run

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .locked.txt mutants **/__pycache__ *.egg-info build dist
