"""Audit every PyPI name/version in uv.lock, including inactive marker branches."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from importlib import metadata
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*\Z")
MAX_LOCK_BYTES = 2 * 1024 * 1024
MAX_PACKAGES = 5000


class AuditError(ValueError):
    """A fixed, non-sensitive reason why complete auditing could not be proven."""


def canonical(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise AuditError("invalid package name")
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_packages(lock, project):
    """Ignore marker selection, never unknown sources or unauditable packages."""
    if not isinstance(lock, dict) or not isinstance(project, dict):
        raise AuditError("invalid lock document")
    packages = lock.get("package")
    if not isinstance(packages, list) or not 0 < len(packages) <= MAX_PACKAGES:
        raise AuditError("invalid locked package list")
    project_name, project_version = canonical(project["name"]), project["version"]
    found_project, expected = 0, set()
    for package in packages:
        if not isinstance(package, dict):
            raise AuditError("invalid locked package")
        name, version = canonical(package.get("name")), package.get("version")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise AuditError("invalid locked version")
        source = package.get("source")
        if name == project_name and version == project_version and source == {"editable": "."}:
            found_project += 1
        elif source == {"registry": "https://pypi.org/simple"}:
            expected.add((name, version))
        else:
            raise AuditError("locked source cannot be audited through public PyPI")
    if found_project != 1 or not expected:
        raise AuditError("lock must contain the current project and registry dependencies")
    return sorted(expected)


def batches(packages):
    """pip-audit accepts only one version of a canonical name per invocation."""
    result, occurrences = [], defaultdict(int)
    for name, version in packages:
        index = occurrences[name]
        if index == len(result):
            result.append([])
        result[index].append((name, version))
        occurrences[name] += 1
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditError("duplicate audit response field")
        result[key] = value
    return result


def validate_response(text, expected, exit_code):
    if exit_code not in {0, 1}:
        raise AuditError("audit subprocess failed")
    try:
        response = json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise AuditError("invalid audit response JSON") from exc
    dependencies = response.get("dependencies") if isinstance(response, dict) else None
    if not isinstance(dependencies, list):
        raise AuditError("invalid audit dependency results")
    observed, findings = set(), []
    for item in dependencies:
        if not isinstance(item, dict) or "skip_reason" in item:
            raise AuditError("audit skipped a locked dependency")
        pair = (canonical(item.get("name")), item.get("version"))
        if not isinstance(pair[1], str) or pair not in expected or pair in observed:
            raise AuditError("unexpected or duplicate audited dependency")
        vulnerabilities = item.get("vulns")
        if not isinstance(vulnerabilities, list):
            raise AuditError("invalid audit vulnerability results")
        observed.add(pair)
        if vulnerabilities:
            findings.append({"name": pair[0], "version": pair[1], "vulnerability_count": len(vulnerabilities)})
    if observed != set(expected):
        raise AuditError("audit did not cover every locked dependency")
    if exit_code and not findings:
        raise AuditError("audit subprocess failed without complete vulnerability results")
    return findings


def audit(packages):
    groups, findings = batches(packages), []
    with tempfile.TemporaryDirectory(prefix="soulacp-lock-audit-") as temporary:
        for index, group in enumerate(groups):
            requirements = Path(temporary) / f"batch-{index}.txt"
            requirements.write_text("".join(f"{name}=={version}\n" for name, version in group), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "pip_audit",
                    "--no-deps",
                    "--disable-pip",
                    "--strict",
                    "--vulnerability-service",
                    "pypi",
                    "--progress-spinner",
                    "off",
                    "--desc",
                    "off",
                    "--aliases",
                    "off",
                    "--format",
                    "json",
                    "--output",
                    "stdout",
                    "--cache-dir",
                    str(Path(temporary) / "cache"),
                    "--requirement",
                    str(requirements),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            # Never echo subprocess output, URLs, descriptions or collection errors.
            findings.extend(validate_response(completed.stdout, group, completed.returncode))
    return {
        "schema": "soulacp.all_marker_lock_audit.v1",
        "status": "FAIL" if findings else "PASS",
        "audited_package_versions": len(packages),
        "batches": len(groups),
        "all_marker_branches": True,
        "skipped_dependencies": 0,
        "vulnerable_packages": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Optional new JSON report file; existing files are never overwritten"
    )
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        print("Audit report must be a new writable file.", file=sys.stderr)
        return 2
    try:
        if (ROOT / "uv.lock").stat().st_size > MAX_LOCK_BYTES:
            raise AuditError("lock file exceeds the supported size")
        with (ROOT / "uv.lock").open("rb") as stream:
            lock = tomllib.load(stream)
        with (ROOT / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)["project"]
        result = audit(locked_packages(lock, project))
        result["tool"] = "pip-audit " + metadata.version("pip-audit")
    except (
        AuditError,
        OSError,
        subprocess.TimeoutExpired,
        metadata.PackageNotFoundError,
        KeyError,
        tomllib.TOMLDecodeError,
    ) as error:
        reason = str(error) if isinstance(error, AuditError) else "lock audit could not complete"
        result = {"schema": "soulacp.all_marker_lock_audit.v1", "status": "ERROR", "reason": reason}
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(output)
        except OSError:
            print("Audit report must be a new writable file.", file=sys.stderr)
            return 2
    print(output, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
