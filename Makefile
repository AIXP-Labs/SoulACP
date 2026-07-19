.PHONY: install test test-unit test-integration hooks lint typecheck security check format clean build

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -W error -v

test-unit:
	pytest tests/ -W error -m "not integration" -v

test-integration:
	pytest tests/test_integration*.py --run-integration -W error -v

hooks:
	pre-commit run --all-files

lint:
	ruff check src/soulacp/ tests/ examples/
	ruff format --check src/soulacp/ tests/ examples/

typecheck:
	pyright
	PYTHONPATH=src pyright --verifytypes soulacp --ignoreexternal

security:
	bandit -q -r src/soulacp

check: lint typecheck security

format:
	ruff check --fix src/soulacp/ tests/ examples/
	ruff format src/soulacp/ tests/ examples/

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p, True) for p in [pathlib.Path(d) for d in ['build','dist','.pytest_cache','htmlcov']] if p.exists()]; [shutil.rmtree(p, True) for p in pathlib.Path('src').glob('*.egg-info')]"

build: clean
	python -m build
	python -m twine check dist/*
