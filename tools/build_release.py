"""Build and validate distributions without cleaning or overwriting old outputs."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", required=True, help="A new, nonexistent output directory")
    args = parser.parse_args(argv)
    destination = Path(args.outdir).expanduser().absolute()
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("Build output must be a new writable directory; existing paths are never overwritten.", file=sys.stderr)
        return 2
    result = subprocess.run([sys.executable, "-m", "build", "--outdir", str(destination)], cwd=ROOT, check=False)
    if result.returncode:
        return result.returncode
    wheels = sorted(destination.glob("*.whl"))
    sources = sorted(destination.glob("*.tar.gz"))
    if not wheels or not sources:
        print("Build did not produce both wheel and source distribution; outputs retained.", file=sys.stderr)
        return 1
    for command in [
        [sys.executable, "-m", "twine", "check", *map(str, wheels), *map(str, sources)],
        [sys.executable, str(ROOT / "tools/check_sdist.py"), *map(str, sources)],
    ]:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
