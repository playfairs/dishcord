from __future__ import annotations

import asyncio
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean_ansi(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", value).replace("\r", "")


def sanitize_output(value: str) -> str:
    text = clean_ansi(value)
    return text.replace("``", "`\u200b`")


class ShellReader:
    def __init__(
        self,
        code: str,
        *,
        shell: Optional[str] = None,
        timeout: float = 120,
        escape_ansi: bool = True,
    ) -> None:
        self.code = code
        self.shell = shell or self._default_shell()
        self.timeout = timeout
        self.escape_ansi = escape_ansi or sys.platform == "win32"
        self.ps1 = "$"
        self.highlight = "shell"
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stdin_fd: Optional[int] = None
        self.stdin_pipe: Optional[asyncio.StreamWriter] = None
        self.queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self.reader_tasks: list[asyncio.Task[None]] = []
        self.last_output = asyncio.get_running_loop().time()
        self._remaining_readers = 0
        self._close_code: Optional[int] = None

    @staticmethod
    def _default_shell() -> str:
        if sys.platform == "win32":
            return shutil.which("powershell") or shutil.which("cmd") or "cmd"
        return os.getenv("SHELL") or "/bin/bash"

    async def start(self) -> "ShellReader":
        if sys.platform == "win32":
            if "powershell" in self.shell.lower():
                self.ps1 = "PS >"
                self.highlight = "powershell"
                args = [self.shell, "-NoLogo", "-NoProfile", "-Command", self.code]
            else:
                self.ps1 = "cmd >"
                self.highlight = "cmd"
                args = [self.shell, "/c", self.code]
            self.process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.stdin_pipe = self.process.stdin
        else:
            import pty

            master_fd, slave_fd = pty.openpty()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    self.shell,
                    "-c",
                    self.code,
                    stdin=slave_fd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except BaseException:
                os.close(master_fd)
                raise
            finally:
                os.close(slave_fd)
            self.stdin_fd = master_fd

        if self.process.stdout is None or self.process.stderr is None:
            await self.close()
            raise RuntimeError("shell process did not expose stdout and stderr")

        self._remaining_readers = 2
        self.reader_tasks = [
            asyncio.create_task(self._read_stream(self.process.stdout)),
            asyncio.create_task(self._read_stream(self.process.stderr, stderr=True)),
        ]
        return self

    async def _read_stream(
        self, stream: asyncio.StreamReader, *, stderr: bool = False
    ) -> None:
        try:
            while line := await stream.readline():
                text = (
                    line.decode("utf-8", errors="replace")
                    .replace("\r", "")
                    .rstrip("\n")
                )
                text = clean_ansi(text) if self.escape_ansi else text
                text = text.replace("``", "`\u200b`")
                await self.queue.put(f"[stderr] {text}" if stderr else text)
        finally:
            self._remaining_readers -= 1
            if self._remaining_readers == 0:
                await self.queue.put(None)

    @property
    def closed(self) -> bool:
        return self._remaining_readers == 0

    @property
    def close_code(self) -> Optional[int]:
        if self.process is not None:
            return self.process.returncode
        return self._close_code

    async def write_stdin(self, value: str) -> None:
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError("shell process is not accepting input")
        if self.stdin_fd is not None:
            await asyncio.to_thread(
                os.write, self.stdin_fd, (value + "\r").encode("utf-8")
            )
        elif self.stdin_pipe is not None:
            self.stdin_pipe.write((value + "\r\n").encode("utf-8"))
            await self.stdin_pipe.drain()
        else:
            raise RuntimeError("shell stdin is unavailable")

    def __aiter__(self) -> "ShellReader":
        return self

    async def __anext__(self) -> str:
        if self.closed and self.queue.empty():
            raise StopAsyncIteration
        elapsed = asyncio.get_running_loop().time() - self.last_output
        remaining = self.timeout - elapsed
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            item = await asyncio.wait_for(self.queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            raise
        if item is None:
            raise StopAsyncIteration
        self.last_output = asyncio.get_running_loop().time()
        return item

    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            self._close_code = await self.process.wait()
        if self.stdin_fd is not None:
            os.close(self.stdin_fd)
            self.stdin_fd = None
        if self.reader_tasks:
            await asyncio.gather(*self.reader_tasks, return_exceptions=True)

    async def wait(self) -> int:
        if self.process is None:
            raise RuntimeError("shell process has not been started")
        return await self.process.wait()


@dataclass
class ShellSession:
    """long live the shell!!!!!!!!!"""

    shell: Optional[str] = None
    cwd: Optional[str] = None
    env: Optional[dict[str, str]] = None
    timeout: float = 30.0
    command: Optional[str] = None
    name: str = "default"
    _process: Optional[subprocess.Popen[str]] = field(
        default=None, init=False, repr=False
    )
    _output: list[str] = field(default_factory=list, init=False)
    _command_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )
    _closed: bool = field(default=False, init=False)
    _read_event: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )
    _read_buffer: list[str] = field(default_factory=list, init=False, repr=False)
    _reader_task: Optional[asyncio.Task[None]] = field(
        default=None, init=False, repr=False
    )
    _stdin: Optional[object] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.command is None:
            self.command = self._default_command()
        self._start_process()

    def _default_command(self) -> str:
        if platform.system() == "Windows":
            return os.getenv("COMSPEC") or "cmd.exe"
        return os.getenv("SHELL") or "/bin/bash"

    def _start_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        if platform.system() == "Windows":
            shell = self.shell or self._default_command()
            cmd = [shell]
            if "powershell" in shell.lower():
                cmd = [shell, "-NoLogo", "-NoProfile", "-Command", "-"]
            elif shell.lower().endswith("cmd.exe") or shell.lower().endswith("cmd"):
                cmd = [shell, "/K"]
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.cwd,
                env=self.env,
                bufsize=1,
            )
            self._stdin = self._process.stdin
            self._reader_task = asyncio.create_task(self._read_until_exit())
            return

        shell = self.shell or self._default_command()
        command = [shell]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self.cwd,
            env=self.env,
            bufsize=1,
        )
        self._stdin = self._process.stdin
        self._reader_task = asyncio.create_task(self._read_until_exit())

    async def _read_until_exit(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        while True:
            chunk = await asyncio.to_thread(self._process.stdout.readline)
            if chunk == "":
                break
            text = clean_ansi(chunk)
            self._output.append(text)
            self._read_buffer.append(text)
            self._read_event.set()
        self._closed = True
        self._read_event.set()

    async def write(self, data: str) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("shell session is not running")
        self._process.stdin.write(data)
        self._process.stdin.flush()

    async def send(self, data: str) -> None:
        await self.write(data if data.endswith("\n") else f"{data}\n")

    async def read(self, timeout: Optional[float] = None) -> str:
        if timeout is None:
            timeout = self.timeout
        if self._read_buffer:
            text = "".join(self._read_buffer)
            self._read_buffer.clear()
            return text
        try:
            await asyncio.wait_for(self._read_event.wait(), timeout)
        except asyncio.TimeoutError:
            return ""
        text = "".join(self._read_buffer)
        self._read_buffer.clear()
        self._read_event.clear()
        return text

    async def run(self, command: str) -> str:
        output, _ = await self.execute(command)
        return output

    async def execute(self, command: str) -> tuple[str, int]:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("shell session is not running")

        async with self._command_lock:
            marker = f"__DISHCORD_STATUS_{uuid.uuid4().hex}__"
            wrapped_command = (
                f"eval {shlex.quote(command)}\n"
                f"__dishcord_status=$?\n"
                f"printf '\\n{marker}:%s\\n' \"$__dishcord_status\"\n"
            )
            await self.write(wrapped_command)

            output_parts: list[str] = []
            marker_prefix = f"{marker}:"
            deadline = asyncio.get_running_loop().time() + self.timeout
            try:
                while True:
                    if not self._read_buffer:
                        self._read_event.clear()
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        await asyncio.wait_for(
                            self._read_event.wait(), timeout=remaining
                        )
                    chunk = "".join(self._read_buffer)
                    self._read_buffer.clear()
                    self._read_event.clear()

                    marker_index = chunk.rfind(marker_prefix)
                    if marker_index < 0:
                        output_parts.append(chunk)
                        if self._closed:
                            raise RuntimeError(
                                "shell session exited before command completed"
                            )
                        continue

                    output_parts.append(chunk[:marker_index])
                    status_text = chunk[
                        marker_index + len(marker_prefix) :
                    ].splitlines()[0]
                    if not status_text.isdigit():
                        raise RuntimeError("shell returned an invalid command status")
                    return "".join(output_parts).strip(), int(status_text)
            except asyncio.TimeoutError as error:
                raise TimeoutError(
                    f"shell command exceeded {self.timeout:g} seconds"
                ) from error

    async def close(self, *, terminate: bool = False) -> int:
        if self._process is None:
            return 0
        if terminate:
            self._process.terminate()
        else:
            try:
                if self._process.stdin is not None:
                    self._process.stdin.close()
            except Exception:
                pass
        try:
            return self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            return self._process.wait(timeout=5)
        finally:
            self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed or (
            self._process is not None and self._process.poll() is not None
        )

    @property
    def exit_code(self) -> Optional[int]:
        if self._process is None:
            return None
        return self._process.poll()

    async def __aenter__(self) -> "ShellSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close(terminate=True)


class ShellSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ShellSession] = {}

    def create(
        self,
        *,
        name: str = "default",
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> ShellSession:
        session = ShellSession(name=name, shell=shell, cwd=cwd, env=env)
        self._sessions[name] = session
        return session

    def get(
        self, name: str = "default", *, shell: Optional[str] = None
    ) -> ShellSession:
        if name not in self._sessions:
            return self.create(name=name, shell=shell)
        return self._sessions[name]

    async def close(self, name: str = "default") -> int:
        session = self._sessions.get(name)
        if session is None:
            return 0
        result = await session.close(terminate=True)
        self._sessions.pop(name, None)
        return result

    async def close_all(self) -> None:
        for name in list(self._sessions):
            await self.close(name)

    def __contains__(self, name: str) -> bool:
        return name in self._sessions

    def __getitem__(self, name: str) -> ShellSession:
        return self.get(name)


__all__ = ["ShellSession", "ShellSessionManager", "clean_ansi", "sanitize_output"]
