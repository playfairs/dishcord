from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Iterable, Optional

from .shell import ShellReader, ShellSessionManager, sanitize_output

DISCORD_MESSAGE_LIMIT = 2000
PAGE_CONTENT_LIMIT = 1975


async def react_to_successful_command(message: Any, exit_status: int) -> None:
    if exit_status == 0:
        await message.add_reaction("✅") # mhmmm straight out of JISHAKU ()glamuroussss


def split_pages(transcript: str) -> list[str]:
    pages: list[str] = []
    while len(transcript) > PAGE_CONTENT_LIMIT:
        split_at = transcript.rfind("\n", 0, PAGE_CONTENT_LIMIT)
        if split_at <= 0:
            split_at = PAGE_CONTENT_LIMIT
        pages.append(transcript[:split_at])
        transcript = transcript[split_at:].lstrip("\n")
    pages.append(transcript)
    return pages


if TYPE_CHECKING:
    import discord as discord_types

try:  # pragma: no cover - optional dependency
    import discord
    from discord.ext import commands
except ImportError:  # pragma: no cover
    discord = None
    commands = None


if commands is not None:

    class ShellStandardInputModal(discord.ui.Modal, title="Type input"):
        stdin_content = discord.ui.TextInput(
            label="Text", style=discord.TextStyle.short
        )

        def __init__(self, reader: ShellReader) -> None:
            super().__init__(timeout=300)
            self.reader = reader

        async def on_submit(self, interaction: discord_types.Interaction) -> None:
            try:
                await self.reader.write_stdin(self.stdin_content.value or "")
            except RuntimeError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(
                    "Sent into stdin", ephemeral=True
                )

    class ShellPaginatorView(discord.ui.View):
        def __init__(self, owner: Any, reader: ShellReader, command: str) -> None:
            super().__init__(timeout=7200)
            self.owner_id = owner.id
            self.reader = reader
            self.transcript = f"$ {sanitize_output(command)}\n\n"
            self.page_index = 0
            self.message: Any = None
            self.cancelled = False
            self._updates_finished = False
            self._dirty = asyncio.Event()
            self._edit_lock = asyncio.Lock()
            self._updater_task: Optional[asyncio.Task[None]] = None

        def pages(self) -> list[str]:
            return split_pages(self.transcript)

        def render(self) -> str:
            pages = self.pages()
            self.page_index = max(0, min(self.page_index, len(pages) - 1))
            page = pages[self.page_index]
            if not page.endswith("\n"):
                page += "\n"
            content = f"```{self.reader.highlight}\n{page}```"
            if len(content) > DISCORD_MESSAGE_LIMIT:
                raise RuntimeError("shell output page exceeds Discord's message limit")
            return content

        async def interaction_check(
            self, interaction: discord_types.Interaction
        ) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message(
                "Only the command author can control this shell session.",
                ephemeral=True,
            )
            return False

        def start_updater(self) -> None:
            self._updater_task = asyncio.create_task(self._update_loop())

        async def _update_loop(self) -> None:
            while not self._updates_finished and not self.cancelled:
                await self._dirty.wait()
                await asyncio.sleep(1)
                self._dirty.clear()
                await self._edit_message()

        async def _edit_message(self) -> None:
            if self.message is None or self.cancelled:
                return
            async with self._edit_lock:
                try:
                    await self.message.edit(
                        content=self.render(),
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.NotFound:
                    self.cancelled = True
                    self.stop()

        async def add_output(self, line: str) -> None:
            follow_latest = self.page_index == len(self.pages()) - 1
            self.transcript += f"{sanitize_output(line)}\n"
            if follow_latest:
                self.page_index = len(self.pages()) - 1
            self._dirty.set()

        async def finish(self, exit_status: int) -> None:
            self.transcript = self.transcript.rstrip("\n")
            self.transcript += f"\n\n[status] Return code {exit_status}"
            self.page_index = len(self.pages()) - 1
            self._updates_finished = True
            self._dirty.set()
            await self._edit_message()
            if self._updater_task is not None:
                self._updater_task.cancel()

        async def on_timeout(self) -> None:
            self.cancelled = True
            await self.reader.close()
            if self.message is not None:
                await self.message.edit(view=None)

        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
        async def previous_page(
            self,
            interaction: discord_types.Interaction,
            _button: discord_types.ui.Button,
        ) -> None:
            self.page_index = max(0, self.page_index - 1)
            await interaction.response.edit_message(content=self.render(), view=self)

        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
        async def next_page(
            self,
            interaction: discord_types.Interaction,
            _button: discord_types.ui.Button,
        ) -> None:
            self.page_index = min(len(self.pages()) - 1, self.page_index + 1)
            await interaction.response.edit_message(content=self.render(), view=self)

        @discord.ui.button(label="⌨ stdin", row=0)
        async def send_standard_input(
            self,
            interaction: discord_types.Interaction,
            _button: discord_types.ui.Button,
        ) -> None:
            if (
                self.reader.process is None
                or self.reader.process.returncode is not None
            ):
                await interaction.response.send_message(
                    "Shell process is not accepting input.", ephemeral=True
                )
                return
            await interaction.response.send_modal(ShellStandardInputModal(self.reader))

        @discord.ui.button(
            label="■ close paginator", style=discord.ButtonStyle.danger, row=0
        )
        async def close_paginator(
            self,
            interaction: discord_types.Interaction,
            _button: discord_types.ui.Button,
        ) -> None:
            self.cancelled = True
            self._updates_finished = True
            self.stop()
            await interaction.response.defer()
            await self.reader.close()
            if self._updater_task is not None:
                self._updater_task.cancel()
            if self.message is not None:
                await self.message.delete()

    class DiscordShell(commands.Cog):
        def __init__(
            self,
            bot: Optional[Any] = None,
            *,
            owner_ids: Optional[Iterable[int]] = None,
            authorized_users: Optional[Iterable[int]] = None,
            shell: Optional[str] = None,
            default_session: str = "default",
        ) -> None:
            self.bot = bot
            self.owner_ids = set(owner_ids or [])
            self.authorized_users = set(authorized_users or [])
            self.shell_executable = shell
            self.default_session = default_session
            self.sessions = ShellSessionManager()
            self.active_readers: set[ShellReader] = set()

        def is_authorized(self, user_id: Optional[int]) -> bool:
            if user_id is None:
                return False
            if user_id in self.authorized_users:
                return True
            if self.owner_ids and user_id in self.owner_ids:
                return True
            return False

        async def create_session(self, *, session_name: Optional[str] = None) -> Any:
            name = session_name or self.default_session
            return self.sessions.get(name, shell=self.shell_executable)

        async def execute(
            self,
            *,
            user_id: Optional[int],
            command: str,
            session_name: Optional[str] = None,
        ) -> str:
            if not self.is_authorized(user_id):
                raise PermissionError(
                    "this user is not authorised to run shell commands"
                )
            session = self.sessions.get(
                session_name or self.default_session, shell=self.shell_executable
            )
            return await session.run(command)

        async def close_session(self, *, session_name: Optional[str] = None) -> int:
            return await self.sessions.close(session_name or self.default_session)

        async def cog_unload(self) -> None:
            await asyncio.gather(
                *(reader.close() for reader in tuple(self.active_readers))
            )
            await self.sessions.close_all()

        @commands.command(name="shell")
        async def shell(self, ctx: Any, *, command: Optional[str] = None) -> None:
            if not self.is_authorized(ctx.author.id):
                raise commands.NotOwner(ctx=ctx)
            if not command:
                await ctx.send("Usage: `!shell <command>`")
                return
            reader = ShellReader(command, shell=self.shell_executable)
            await reader.start()
            self.active_readers.add(reader)
            view = ShellPaginatorView(ctx.author, reader, command)
            try:
                view.message = await ctx.send(
                    view.render(),
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                view.start_updater()
                try:
                    async for line in reader:
                        if view.cancelled:
                            return
                        await view.add_output(line)
                except asyncio.TimeoutError:
                    if not view.cancelled:
                        await view.add_output(
                            f"[timeout] No output for {reader.timeout:g} seconds"
                        )
                        await reader.close()

                if not view.cancelled:
                    exit_status = await reader.wait()
                    await view.finish(exit_status)
                    await react_to_successful_command(ctx.message, exit_status)
            finally:
                if not view.cancelled:
                    await reader.close()
                self.active_readers.discard(reader)
                if view._updater_task is not None and not view._updates_finished:
                    view._updater_task.cancel()

        @property
        def commands_module(self) -> Any:
            return commands

else:

    class DiscordShell:
        """what the fuck do u mean u dont use discord.py."""

        def __init__(
            self,
            bot: Optional[Any] = None,
            *,
            owner_ids: Optional[Iterable[int]] = None,
            authorized_users: Optional[Iterable[int]] = None,
            shell: Optional[str] = None,
            default_session: str = "default",
        ) -> None:
            self.bot = bot
            self.owner_ids = set(owner_ids or [])
            self.authorized_users = set(authorized_users or [])
            self.shell_executable = shell
            self.default_session = default_session
            self.sessions = ShellSessionManager()

        def is_authorized(self, user_id: Optional[int]) -> bool:
            if user_id is None:
                return False
            if user_id in self.authorized_users:
                return True
            if self.owner_ids and user_id in self.owner_ids:
                return True
            return False

        async def create_session(self, *, session_name: Optional[str] = None) -> Any:
            name = session_name or self.default_session
            return self.sessions.get(name, shell=self.shell_executable)

        async def execute(
            self,
            *,
            user_id: Optional[int],
            command: str,
            session_name: Optional[str] = None,
        ) -> str:
            if not self.is_authorized(user_id):
                raise PermissionError(
                    "this user is not authorised to run shell commands"
                )
            session = self.sessions.get(
                session_name or self.default_session, shell=self.shell_executable
            )
            return await session.run(command)

        async def close_session(self, *, session_name: Optional[str] = None) -> int:
            return await self.sessions.close(session_name or self.default_session)

        @property
        def commands_module(self) -> Any:
            return None


__all__ = ["DiscordShell"]
