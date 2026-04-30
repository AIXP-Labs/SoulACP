"""Terminal service — executes commands for CLI subprocesses."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

security_logger = logging.getLogger("soulacp.security")


class TerminalService:
    """Host-side terminal service for ACP clients.

    Manages short-lived subprocess executions requested by the CLI tool.
    Each terminal is identified by a unique ID and supports create,
    wait, output retrieval, and release.
    """

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self._terminals: dict[str, dict[str, Any]] = {}

    async def create(self, command: str, **_extra: Any) -> dict[str, Any]:
        """Create a terminal and start executing *command* (non-blocking).

        .. warning::
            This method executes commands via the system shell.
            Only use with trusted CLI tools.
        """
        security_logger.warning("Terminal command requested: %r (cwd=%s)", command, self.cwd)
        terminal_id = str(uuid.uuid4())[:8]
        exit_event = asyncio.Event()

        self._terminals[terminal_id] = {
            "output": "",
            "exit_code": None,
            "exit_event": exit_event,
        }

        async def _run() -> None:
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self.cwd,
                )
                stdout, _ = await proc.communicate()
                self._terminals[terminal_id]["output"] = stdout.decode("utf-8", errors="replace")
                self._terminals[terminal_id]["exit_code"] = proc.returncode
            except Exception as exc:
                self._terminals[terminal_id]["output"] = str(exc)
                self._terminals[terminal_id]["exit_code"] = -1
            finally:
                exit_event.set()

        asyncio.create_task(_run())
        return {"terminalId": terminal_id}

    async def wait_for_exit(
        self,
        terminal_id: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Block until the terminal process exits."""
        term = self._terminals.get(terminal_id)
        if not term:
            raise KeyError(f"Terminal not found: {terminal_id}")
        await term["exit_event"].wait()
        return {
            "exitStatus": {
                "exitCode": term["exit_code"],
                "signal": None,
            }
        }

    async def get_output(
        self,
        terminal_id: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Return the current output of a terminal."""
        term = self._terminals.get(terminal_id)
        if not term:
            raise KeyError(f"Terminal not found: {terminal_id}")
        result: dict[str, Any] = {"output": term["output"]}
        if term["exit_code"] is not None:
            result["exitStatus"] = {"exitCode": term["exit_code"]}
        return result

    async def release(
        self,
        terminal_id: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Release terminal resources."""
        self._terminals.pop(terminal_id, None)
        return {}
