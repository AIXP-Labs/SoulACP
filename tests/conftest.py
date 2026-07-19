"""Use local sources and keep real provider processes opt-in."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Test the checkout, not a separately installed soulacp version.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests that start installed external agent CLIs",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_integration = config.getoption("--run-integration")
    skip = pytest.mark.skip(reason="requires --run-integration and an authenticated external agent CLI")
    for item in items:
        filename = Path(str(item.fspath)).name
        if filename.startswith(("test_integration", "test_spike_", "test_stress_")):
            item.add_marker(pytest.mark.integration)
            if not run_integration:
                item.add_marker(skip)
