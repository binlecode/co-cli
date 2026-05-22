"""Unix socket IPC for the dream daemon."""

from __future__ import annotations

import asyncio
from pathlib import Path


class DaemonIPC:
    """Unix socket server for receiving daemon control commands.

    One client connection is served at a time via receive_one / send_ack.
    """

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self, sock_path: Path) -> None:
        """Bind and start listening on the Unix socket at sock_path."""
        # Remove stale socket file if present
        if sock_path.exists():
            sock_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(sock_path),
        )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Accept and store the next incoming connection."""
        self._reader = reader
        self._writer = writer

    async def receive_one(self) -> str:
        """Wait for the next line from a connected client and return it stripped."""
        # Wait until a connection arrives
        while self._reader is None:
            await asyncio.sleep(0.05)
        line = await self._reader.readline()
        return line.decode().strip()

    async def send_ack(self, msg: str) -> None:
        """Send an acknowledgement line to the current client."""
        if self._writer is not None:
            self._writer.write((msg + "\n").encode())
            await self._writer.drain()
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def close(self) -> None:
        """Stop the server and close any open connection."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._reader = None
            self._writer = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


async def send_command(
    sock_path: Path,
    command: str,
    timeout_ms: int = 2000,
) -> str | None:
    """Connect to the daemon socket, send command, and return the reply.

    Returns None on any connection or timeout error.
    """
    timeout_s = timeout_ms / 1000.0
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(sock_path)),
            timeout=timeout_s,
        )
        writer.write((command.rstrip("\n") + "\n").encode())
        await writer.drain()
        reply_bytes = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return reply_bytes.decode().strip()
    except Exception:
        return None
