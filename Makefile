.PHONY: install test lint format check clean

install:
	pip install -e ".[dev,extract]"

test:
	pytest

lint:
	ruff check src tests

format:
	ruff format src tests

check: lint test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache **/__pycache__ *.egg-info build dist
