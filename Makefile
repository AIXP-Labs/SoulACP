.PHONY: install test test-unit test-integration hooks lint typecheck security check format clean build

PYTHON ?= python
HOOKS_PYTHON ?= $(if $(filter Windows_NT,$(OS)),.venv-hooks/Scripts/python.exe,.venv-hooks/bin/python)
DIST_DIR ?= dist/0.2.6

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -W error -v

test-unit:
	pytest tests/ -W error -m "not integration" -v

test-integration:
	pytest tests/test_integration*.py --run-integration -W error -v

hooks:
	"$(PYTHON)" tools/run_hooks.py --python "$(HOOKS_PYTHON)"

lint:
	ruff check src/soulacp/ tests/ examples/ tools/
	ruff format --check src/soulacp/ tests/ examples/ tools/

typecheck:
	pyright
	PYTHONPATH=src pyright --verifytypes soulacp --ignoreexternal

security:
	bandit -q -r src/soulacp

check: lint typecheck security

format:
	ruff check --fix src/soulacp/ tests/ examples/ tools/
	ruff format src/soulacp/ tests/ examples/ tools/

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p, True) for p in [pathlib.Path(d) for d in ['build','dist','.pytest_cache','htmlcov']] if p.exists()]; [shutil.rmtree(p, True) for p in pathlib.Path('src').glob('*.egg-info')]"

build:
	"$(PYTHON)" tools/build_release.py --outdir "$(DIST_DIR)"
