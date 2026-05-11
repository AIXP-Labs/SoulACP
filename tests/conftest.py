"""pytest configuration for soulacp tests."""

import sys
from pathlib import Path

# Make tests use the LOCAL src/ source (development), not the
# pip-installed version. Otherwise edits to src/soulacp/... do not
# take effect until you re-install. Insert at index 0 to win precedence.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
