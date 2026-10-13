import asyncio
from unittest.mock import AsyncMock

import pytest

from dishcord import (
    DiscordShell,
    ShellReader,
    ShellSession,
    ShellSessionManager,
    clean_ansi,
)
from dishcord.discord_integration import (
    ShellPaginatorView,
    react_to_successful_command,
)


@pytest.mark.asyncio
async def test_session_run_and_output():
    session = ShellSession(name="echo-test", shell="/bin/sh")
    try:
        output, status = await session.execute("printf 'hello from shell\\n'")
        assert output == "hello from shell"
        assert status == 0

        _, failed_status = await session.execute("false")
        assert failed_status == 1
    finally:
        await session.close(terminate=True)


@pytest.mark.asyncio
async def test_shell_reader_streams_output_and_stderr():
    reader = await ShellReader(
        "printf 'first\\n'; printf 'problem\\n' >&2; exit 7",
        shell="/bin/sh",
    ).start()
    try:
        lines = [line async for line in reader]
        assert lines == ["first", "[stderr] problem"]
        assert await reader.wait() == 7
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_shell_reader_accepts_standard_input():
    reader = await ShellReader(
        "read value; printf 'received:%s\\n' \"$value\"",
        shell="/bin/sh",
    ).start()
    try:
        await reader.write_stdin("input text")
        assert [line async for line in reader] == ["received:input text"]
        assert await reader.wait() == 0
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_shell_reader_times_out_on_silent_command():
    reader = await ShellReader("sleep 5", shell="/bin/sh", timeout=0.05).start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await reader.__anext__()
    finally:
        await reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("exit_status", "expected_reactions"), [(0, ["✅"]), (1, [])])
async def test_only_successful_commands_get_checkmark(exit_status, expected_reactions):
    message = type("Message", (), {"add_reaction": AsyncMock()})()

    await react_to_successful_command(message, exit_status)

    assert [call.args[0] for call in message.add_reaction.await_args_list] == (
        expected_reactions
    )


@pytest.mark.asyncio
async def test_shell_paginator_edits_same_transcript_and_keeps_status():
    from types import SimpleNamespace

    reader = await ShellReader("sleep 5", shell="/bin/sh").start()
    view = ShellPaginatorView(SimpleNamespace(id=1), reader, "echo hi")

    class Message:
        def __init__(self):
            self.edits = []

        async def edit(self, **kwargs):
            self.edits.append(kwargs)

    message = Message()
    view.message = message
    try:
        assert view.render() == "```shell\n$ echo hi\n\n```"
        await view._edit_message()
        await view.add_output("hi")
        assert view.render() == "```shell\n$ echo hi\n\nhi\n```"
        await view._edit_message()
        assert len(message.edits) == 2
        assert message.edits[0]["content"] == "```shell\n$ echo hi\n\n```"
        assert message.edits[1]["content"] == "```shell\n$ echo hi\n\nhi\n```"
        await view.finish(0)
        assert view.render().endswith("[status] Return code 0\n```")
        assert len(view.render()) <= 2000
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_session_manager_round_trip():
    manager = ShellSessionManager()
    session = manager.create(name="demo")
    assert manager.get("demo") is session
    assert "demo" in manager
    await manager.close("demo")


def test_clean_ansi_removes_escape_codes():
    text = "\x1b[31mred\x1b[0m"
    assert "red" == clean_ansi(text)


@pytest.mark.asyncio
async def test_authorized_users():
    shell = DiscordShell(owner_ids={1}, authorized_users={2})
    assert shell.is_authorized(1) is True
    assert shell.is_authorized(2) is True
    assert shell.is_authorized(99) is False


@pytest.mark.asyncio
async def test_long_running_queue_cleanup():
    session = ShellSession(name="sleep-test", shell="/bin/sh")
    try:
        output, status = await session.execute(
            "python3 -c \"import time; time.sleep(0.2); print('done')\""
        )
        assert "done" in output
        assert status == 0
    finally:
        await session.close(terminate=True)
