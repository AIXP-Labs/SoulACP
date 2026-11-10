"""Check source-distribution inputs without executing or extracting the archive."""

import argparse
import sys
import tarfile
from pathlib import PurePosixPath

REQUIRED_INPUTS = frozenset(
    {
        "pyproject.toml",
        "MANIFEST.in",
        "LICENSE",
        "NOTICE",
        "Makefile",
        "requirements-dev.txt",
        "requirements-min.txt",
        "requirements-hooks.txt",
        "uv.lock",
        "tools/run_hooks.py",
        "tools/check_sdist.py",
        "tools/build_release.py",
        "tools/audit_lock.py",
        "tests/test_dependency_policy.py",
        "tests/test_lock_audit.py",
        "tests/test_session_response_ordering.py",
        "tests/test_opencode_response_ordering.py",
        "tests/test_shutdown_exception_privacy.py",
    }
)


def validate_sdist(path):
    with tarfile.open(path, "r:gz") as archive:
        names = set()
        roots = set()
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if not name.parts or name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise ValueError("unsafe source-distribution member")
            roots.add(name.parts[0])
            if member.isfile():
                relative = "/".join(name.parts[1:])
                if relative in names:
                    raise ValueError("duplicate source-distribution input")
                names.add(relative)
        if len(roots) != 1:
            raise ValueError("unsafe source-distribution root")
        missing = REQUIRED_INPUTS - names
        if missing:
            raise ValueError("missing required inputs: " + ", ".join(sorted(missing)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+")
    args = parser.parse_args(argv)
    for path in args.archives:
        try:
            validate_sdist(path)
        except (OSError, ValueError, tarfile.TarError) as error:
            print(
                str(error) if isinstance(error, ValueError) else "Could not read source distribution", file=sys.stderr
            )
            return 1
    print("Source distribution inputs verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
