"""Cursor CLI client — non-pooled, subprocess-per-query."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator

from soulacp.binary import find_cursor_binary
from soulacp.config import ACPConfig

logger = logging.getLogger(__name__)

# Prompt length threshold for using temp file instead of command argument
_PROMPT_FILE_THRESHOLD = 500


class CursorCLIClient:
    """Client for Cursor CLI (cursor-agent).

    Unlike other ACP clients, Cursor CLI:
    - Does NOT use JSON-RPC protocol
    - Creates a new subprocess per query (no pooling)
    - Uses command-line arguments for prompt delivery
    - Long prompts are written to a temp file
    - Supports ``--resume`` for session continuity
    - Supports ``--output-format stream-json`` for streaming
    """

    def __init__(self, config: ACPConfig) -> None:
        self.config = config
        self._binary: str | None = None
        self._session_id: str | None = None
        self._last_used: float = time.time()

    @property
    def is_connected(self) -> bool:
        # Always "connected" — each query creates a new subprocess
        return True

    @property
    def is_idle_timeout(self) -> bool:
        return False  # No idle tracking for non-pooled client

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self._session_id = value

    async def ping(self) -> bool:
        """Always returns True — no persistent process to check."""
        return True

    async def connect(self) -> None:
        """Validate binary exists (no persistent process)."""
        self._binary = find_cursor_binary()
        if not self._binary:
            raise FileNotFoundError("Cursor CLI not found. Install from: https://cursor.com")

    async def disconnect(self) -> None:
        """No-op — each query manages its own subprocess."""
        pass

    async def query(self, prompt: str) -> str:
        """Execute a prompt via cursor-agent subprocess.

        Args:
            prompt: The prompt text.

        Returns:
            The response text.
        """
        binary = self._binary or find_cursor_binary()
        if not binary:
            raise FileNotFoundError("Cursor CLI not found")

        model_id = self.config.model.replace("cursor-cli/", "")
        cmd = [binary, "-p", "--model", model_id, "--output-format", "text"]

        if self._session_id:
            cmd.extend(["--resume", self._session_id])

        temp_path = None
        try:
            if len(prompt) > _PROMPT_FILE_THRESHOLD:
                # Write long prompts to temp file
                with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(prompt)
                    temp_path = f.name
                cmd.extend(["-f", temp_path])
            else:
                cmd.append(prompt)

            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_prompt,
                cwd=self.config.cwd or None,
            )

            if result.returncode != 0 and result.stderr:
                logger.warning("Cursor stderr: %s", result.stderr.strip())

            return result.stdout.strip()

        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    async def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Stream response via cursor-agent with stream-json output.

        Yields incremental text deltas.
        """
        binary = self._binary or find_cursor_binary()
        if not binary:
            raise FileNotFoundError("Cursor CLI not found")

        model_id = self.config.model.replace("cursor-cli/", "")
        cmd = [
            binary,
            "-p",
            "--model",
            model_id,
            "--output-format",
            "stream-json",
            "--stream-partial-output",
        ]

        if self._session_id:
            cmd.extend(["--resume", self._session_id])

        temp_path = None
        try:
            if len(prompt) > _PROMPT_FILE_THRESHOLD:
                with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(prompt)
                    temp_path = f.name
                cmd.extend(["-f", temp_path])
            else:
                cmd.append(prompt)

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.config.cwd or None,
                limit=10 * 1024 * 1024,  # 10 MB – default 64 KB too small for large responses
            )

            full_content = ""
            try:
                while True:
                    line = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=self.config.timeout_stream,
                    )
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue

                    try:
                        data = json.loads(text)
                        new_content = data.get("content", "")
                        # Compute incremental delta
                        delta = new_content[len(full_content) :]
                        full_content = new_content
                        if delta:
                            yield delta
                    except json.JSONDecodeError:
                        continue
            finally:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    async def resume(self, session_id: str) -> bool:
        """Store session_id for subsequent ``--resume`` usage.

        Cursor does not maintain persistent sessions. The stored session_id
        will be passed via ``--resume`` on the next ``query()`` call.
        """
        self._session_id = session_id
        return True
