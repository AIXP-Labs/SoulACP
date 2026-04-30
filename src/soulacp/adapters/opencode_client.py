"""OpenCode-specific ACP client with Windows compatibility."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_opencode_binary
from soulacp.config import ACPConfig
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class OpenCodeACPClient(ACPClientBase):
    """ACP client for OpenCode CLI with Windows compatibility.

    Key differences from Claude/Gemini:
    - Uses ``subprocess.Popen`` (synchronous) + thread-based reader
      for Windows compatibility (asyncio stdin is unreliable on Windows)
    - Model passed via ``OPENCODE_CONFIG_CONTENT`` environment variable
    - Uses ``CREATE_NO_WINDOW`` flag on Windows
    - stdin writes via ``os.write(fileno())`` on Windows
    """

    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self._popen: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        if self._popen is not None:
            return self._connected and self._popen.poll() is None
        return super().is_connected

    def _get_acp_command(self) -> list[str]:
        """Return command to start OpenCode CLI in ACP mode."""
        binary = find_opencode_binary()
        if not binary:
            raise FileNotFoundError("OpenCode CLI not found. Install from: https://opencode.ai")
        return [binary, "acp"]

    async def connect(self) -> None:
        """Start OpenCode via synchronous Popen + thread reader."""
        cmd = self._get_acp_command()

        env = os.environ.copy()
        env["HEADLESS"] = "true"
        env["FORCE_COLOR"] = "0"
        env["NO_COLOR"] = "1"

        # Merge model into existing OPENCODE_CONFIG_CONTENT (don't overwrite)
        oc_config = {}
        existing = os.environ.get("OPENCODE_CONFIG_CONTENT")
        if existing:
            try:
                oc_config = json.loads(existing)
            except (json.JSONDecodeError, TypeError):
                pass
        model_id = self.config.model.replace("opencode-acp/", "")
        if model_id:
            oc_config["model"] = model_id
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(oc_config)

        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._popen = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **kwargs,
        )
        self._connected = True
        self._last_used = time.time()
        self._loop = asyncio.get_running_loop()

        # Start synchronous reader thread
        self._reader_thread = threading.Thread(target=self._sync_read_loop, daemon=True)
        self._reader_thread.start()

        # stderr reader thread
        threading.Thread(target=self._sync_read_stderr, daemon=True).start()

        await self._initialize()
        logger.info("OpenCode connected: session=%s", self.session_id)

    async def _initialize(self) -> None:
        """Perform OpenCode protocol handshake and create a session."""
        await self._rpc(
            "initialize",
            {
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
            timeout=self.config.timeout_connect,
        )

        res = await self._rpc(
            "session/new",
            {
                "cwd": self.config.cwd or os.getcwd(),
                "mcpServers": [],
            },
            timeout=self.config.timeout_connect,
        )
        self.session_id = res.get("sessionId") or res.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")

    async def _rpc(self, method: str, params: dict, timeout: int | None = None) -> Any:
        """Send JSON-RPC via synchronous stdin write."""
        if not self._connected or not self._popen:
            raise ConnectionError("OpenCode not connected")

        if timeout is None:
            timeout = self.config.timeout_connect

        self._msg_id += 1
        mid = self._msg_id
        msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "method": method,
                "params": params,
            }
        )

        self._sync_write((msg + "\n").encode())

        future = asyncio.get_running_loop().create_future()
        with self._pending_lock:
            self._pending[mid] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise

    def _sync_write(self, data: bytes) -> None:
        """Write to stdin using os.write on Windows for reliability."""
        if not self._popen or not self._popen.stdin:
            return
        if sys.platform == "win32":
            os.write(self._popen.stdin.fileno(), data)
        else:
            self._popen.stdin.write(data)
            self._popen.stdin.flush()

    def _sync_read_loop(self) -> None:
        """Synchronous stdout reader running in a thread."""
        try:
            while self._popen and self._popen.poll() is None:
                line = self._popen.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                msg = self._parse_json(text)
                if msg and self._loop:
                    asyncio.run_coroutine_threadsafe(self._dispatch(msg), self._loop)
        except Exception as e:
            logger.warning("OpenCode read loop ended unexpectedly: %s", e)
        finally:
            if self._connected:
                logger.warning(
                    "OpenCode subprocess disconnected (pending_rpcs=%d, session=%s)",
                    len(self._pending),
                    self.session_id,
                )
            self._connected = False
            if self._loop:
                with self._pending_lock:
                    for _mid, fut in list(self._pending.items()):
                        if not fut.done():
                            self._loop.call_soon_threadsafe(
                                fut.set_exception,
                                ConnectionError("OpenCode disconnected"),
                            )
                    self._pending.clear()
                # Send stream termination signal to prevent query_stream() hang
                if self._streaming and self._stream_queue:
                    self._loop.call_soon_threadsafe(self._stream_queue.put_nowait, None)

    def _sync_read_stderr(self) -> None:
        """Synchronous stderr reader running in a thread."""
        try:
            while self._popen and self._popen.poll() is None:
                line = self._popen.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("OpenCode stderr: %s", text)
        except Exception:
            pass

    async def disconnect(self) -> None:
        """Terminate the OpenCode subprocess."""
        self._connected = False
        if self._popen:
            try:
                self._popen.terminate()
                self._popen.wait(timeout=5)
            except Exception:
                try:
                    self._popen.kill()
                except Exception:
                    pass
            finally:
                self._popen = None
        # Wait for reader thread to exit
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=3)

    async def _send_result(self, msg_id: int, result: Any) -> None:
        """Send result via synchronous write."""
        if not self._connected:
            return
        resp = json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})
        self._sync_write((resp + "\n").encode())

    async def _send_error(self, msg_id: int, code: int, message: str) -> None:
        """Send error via synchronous write."""
        if not self._connected:
            return
        resp = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": message},
            }
        )
        self._sync_write((resp + "\n").encode())
