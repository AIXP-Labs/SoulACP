.PHONY: install test test-unit test-integration check format clean build

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v

test-unit:
	pytest tests/ --ignore=tests/test_integration*.py -v

test-integration:
	pytest tests/test_integration*.py -v

check:
	ruff check src/soulacp/ tests/
	ruff format --check src/soulacp/ tests/

format:
	ruff check --fix src/soulacp/ tests/
	ruff format src/soulacp/ tests/

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p, True) for p in [pathlib.Path(d) for d in ['build','dist','.pytest_cache','htmlcov']] if p.exists()]; [p.unlink() for p in pathlib.Path('.').glob('*.egg-info')]"

build: clean
	python -m build
