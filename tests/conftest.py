"""pytest configuration for soulacp tests."""

import sys
from pathlib import Path

# Add soulacp to path for development testing
sys.path.insert(0, str(Path(__file__).parent.parent))
