"""Subprocess environment construction with an explicit minimal mode."""

from __future__ import annotations

import os
from collections.abc import Mapping

_MINIMAL_PARENT_ENV_KEYS = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LANGUAGE",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)


def get_subprocess_environment_value(
    environment: Mapping[str, str],
    name: str,
    default: str | None = None,
) -> str | None:
    """Read an environment value using the target platform's name semantics."""
    if os.name != "nt":
        return environment.get(name, default)

    normalized_name = name.upper()
    result = default
    for configured_name, value in environment.items():
        if configured_name.upper() == normalized_name:
            result = value
    return result


def _absolute_search_path(value: str) -> str:
    directories = [
        directory
        for raw_directory in value.split(os.pathsep)
        if (directory := raw_directory.strip('"')) and os.path.isabs(directory)
    ]
    if not directories:
        directories = [directory for directory in os.defpath.split(os.pathsep) if os.path.isabs(directory)]
    return os.pathsep.join(directories)


def build_subprocess_environment(inherit_parent_env: bool) -> dict[str, str]:
    """Return the inherited or bounded environment for a child process.

    Minimal mode retains only process-launch, executable lookup, home/config
    location, temporary-directory, and locale variables. Credentials and
    arbitrary application variables must be supplied explicitly by the caller.
    """
    if inherit_parent_env:
        return dict(os.environ)

    environment: dict[str, str] = {}
    for name, value in os.environ.items():
        normalized = name.upper()
        if normalized in _MINIMAL_PARENT_ENV_KEYS or normalized.startswith("LC_"):
            inherited_value = _absolute_search_path(value) if normalized == "PATH" else value
            environment[name] = inherited_value
    return environment


def update_subprocess_environment(
    environment: dict[str, str],
    values: Mapping[str, str],
) -> None:
    """Merge values with Windows case-insensitive last-write semantics."""
    if os.name != "nt":
        environment.update(values)
        return

    canonical = {name.upper(): name for name in environment}
    for name, value in values.items():
        previous = canonical.get(name.upper())
        if previous is not None and previous != name:
            environment.pop(previous, None)
        environment[name] = value
        canonical[name.upper()] = name
