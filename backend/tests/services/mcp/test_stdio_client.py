from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from loguru import logger
from tutor.services.mcp.config import MCPServerSpec
from tutor.services.mcp.stdio_client import StdioMCPClient


class _FakeStdin:
    def __init__(self, order: list[str]) -> None:
        self._order = order

    def close(self) -> None:
        self._order.append("stdin.close")

    async def wait_closed(self) -> None:
        self._order.append("stdin.wait_closed")


class _BlockingFakeStdin(_FakeStdin):
    def __init__(self, order: list[str], release: asyncio.Event) -> None:
        super().__init__(order)
        self._release = release

    async def wait_closed(self) -> None:
        self._order.append("stdin.wait_closed")
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self._order.append("stdin.wait_closed.cancelled")
            raise


class _FakeProcess:
    def __init__(self, order: list[str], eof: asyncio.Event) -> None:
        self.stdin = _FakeStdin(order)
        self.returncode: int | None = None
        self._order = order
        self._eof = eof

    def terminate(self) -> None:
        self._order.append("terminate")
        self.returncode = 0
        self._eof.set()

    def kill(self) -> None:
        self._order.append("kill")
        self.returncode = -9
        self._eof.set()

    async def wait(self) -> int:
        self._order.append("wait")
        await asyncio.sleep(0)
        return self.returncode or 0


@pytest.mark.asyncio
async def test_shutdown_closes_stdin_and_lets_pipe_readers_reach_eof() -> None:
    order: list[str] = []
    eof = asyncio.Event()
    cancelled: list[str] = []

    async def reader(name: str) -> None:
        try:
            await eof.wait()
            order.append(f"{name}.eof")
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    client = StdioMCPClient(MCPServerSpec(name="MiniMax", command="fake"))
    client._proc = _FakeProcess(order, eof)  # type: ignore[assignment]
    client._read_task = asyncio.create_task(reader("stdout"))
    client._stderr_task = asyncio.create_task(reader("stderr"))

    await client._kill()

    assert order[:2] == ["stdin.close", "stdin.wait_closed"]
    assert "terminate" in order
    assert "wait" in order
    assert {"stdout.eof", "stderr.eof"}.issubset(order)
    assert cancelled == []
    assert client._proc is None
    assert client._read_task is None
    assert client._stderr_task is None


@pytest.mark.asyncio
async def test_shutdown_bounds_stalled_stdin_close_before_terminating_process() -> None:
    order: list[str] = []
    eof = asyncio.Event()
    release_stdin = asyncio.Event()
    process = _FakeProcess(order, eof)
    process.stdin = _BlockingFakeStdin(order, release_stdin)

    async def reader(name: str) -> None:
        await eof.wait()
        order.append(f"{name}.eof")

    client = StdioMCPClient(MCPServerSpec(name="MiniMax", command="fake"))
    client._proc = process  # type: ignore[assignment]
    client._read_task = asyncio.create_task(reader("stdout"))
    client._stderr_task = asyncio.create_task(reader("stderr"))
    client._STDIN_CLOSE_TIMEOUT = 0.01

    shutdown = asyncio.create_task(client._kill())
    try:
        await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
    finally:
        release_stdin.set()
        await shutdown

    assert order[:3] == [
        "stdin.close",
        "stdin.wait_closed",
        "stdin.wait_closed.cancelled",
    ]
    assert "terminate" in order
    assert "wait" in order
    assert {"stdout.eof", "stderr.eof"}.issubset(order)
    assert client._proc is None
    assert client._read_task is None
    assert client._stderr_task is None


@pytest.mark.asyncio
async def test_minimax_stderr_log_replaces_invalid_utf8_and_redacts_credentials() -> None:
    class _Stderr:
        def __init__(self) -> None:
            self._lines = [
                b"MiniMax api_key=SECRET_MINIMAX_STDERR \xff\n",
                b"",
            ]

        async def readline(self) -> bytes:
            return self._lines.pop(0)

    client = StdioMCPClient(MCPServerSpec(name="MiniMax", command="fake"))
    client._proc = SimpleNamespace(stderr=_Stderr())  # type: ignore[assignment]
    records = []
    sink_id = logger.add(records.append, format="{message}", level="DEBUG")
    try:
        await client._stderr_loop()
    finally:
        logger.remove(sink_id)

    captured = "\n".join(str(record) for record in records)
    assert "MCP_SERVER_STDERR" in captured
    assert "MiniMax" in captured
    assert "SECRET_MINIMAX_STDERR" not in captured
    assert "�" in captured


@pytest.mark.asyncio
async def test_startup_log_never_records_expanded_mcp_arguments(monkeypatch) -> None:
    secret = "SECRET_EXPANDED_MINIMAX_ARGUMENT"
    client = StdioMCPClient(
        MCPServerSpec(
            name="MiniMax",
            command="fake-command",
            args=["--api-key", secret],
        )
    )
    client._resolve_command = lambda _command: "C:/tools/fake-command.exe"  # type: ignore[method-assign]
    client._request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"serverInfo": {}, "capabilities": {}},
            {"tools": []},
        ]
    )
    client._notify = AsyncMock()  # type: ignore[method-assign]
    client._read_loop = AsyncMock()  # type: ignore[method-assign]
    client._stderr_loop = AsyncMock()  # type: ignore[method-assign]

    async def fake_subprocess(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    records = []
    sink_id = logger.add(records.append, format="{message}", level="INFO")
    try:
        await client.start()
        await asyncio.sleep(0)
    finally:
        logger.remove(sink_id)
        client._proc = None

    captured = "\n".join(str(record) for record in records)
    assert "MCP_SERVER_STARTING" in captured
    assert "fake-command.exe" in captured
    assert "arg_count" in captured
    assert secret not in captured
