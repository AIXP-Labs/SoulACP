"""Run hooks only through the explicitly selected isolated interpreter."""

import argparse
import subprocess
import sys
from pathlib import Path

HOOK_ENTRY = """import sys
if sys.version_info < (3, 11):
    print('Repository hooks require Python >=3.11', file=sys.stderr)
    raise SystemExit(2)
try:
    from pre_commit.main import main
except ImportError:
    print('Install the isolated hooks profile described in CONTRIBUTING.', file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(main(['run', '--all-files']))
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Explicit Python >=3.11 hook-environment interpreter")
    args = parser.parse_args(argv)
    python = Path(args.python).expanduser()
    if not python.is_file():
        print(
            "Hook interpreter is missing; install the isolated hooks profile described in CONTRIBUTING.",
            file=sys.stderr,
        )
        return 2
    try:
        return subprocess.run([str(python.absolute()), "-c", HOOK_ENTRY], check=False).returncode
    except OSError:
        print("Hook interpreter could not be started; no global command was used.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
