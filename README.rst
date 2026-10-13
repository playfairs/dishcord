Dishcord
========

dishcord is a small python package for running interactive shell sessions from a
``discord.py`` bot. It is intentionally modelled on the shell UX from **Jishaku**,
while keeping the public API small and easy to integrate into an existing bot.

The package focuses on persistent shell sessions, command execution, output
buffering, and safe per-user authorisation rather than offering a broad shell
abstraction.

installation
------------

Install from source::

    python -m pip install -e .

The package also installs the optional ``discord.py`` dependency and supports
Python 3.9+.

minimal integration
-------------------

.. code-block:: python

    import discord
    from discord.ext import commands
    from dishcord import DiscordShell

    intents = discord.Intents.default()
    intents.message_content = True

    class Bot(commands.Bot):
        async def setup_hook(self):
            await self.add_cog(
                DiscordShell(self, owner_ids={123456789012345678})
            )

    bot = Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user}")

    bot.run("TOKEN")

tty sessions
--------------

each session is created with a shell and an optional working directory. commands
execute through the chosen process, and output is collected as text. sessions can
be reused for stateful shell behaviour.

- ``ShellSession.run(command)`` executes a command and returns the output.
- ``ShellSession.write(data)`` sends text to stdin.
- ``ShellSession.read(timeout=None)`` reads pending output.
- ``ShellSession.close()`` terminates the process.
- ``ShellSessionManager`` keeps a collection of named sessions.

The default system shell is selected from ``SHELL`` on Unix-like systems and from
PowerShell on Windows when available.

security
--------

By default, shell access is restricted to the bot owner or explicitly provided
user ids. A developer should not allow arbitrary members to run shell commands.

test ok
--------

A small example bot is included under ``tests/``. It reads ``DISCORD_TOKEN`` and
``DISCORD_OWNER_ID`` from ``.env`` beside the project or from the environment.
Tbh this is just to test how the thing works and it doesnt really have any other
benefit, so yeah u can basically ignore this section unless u care about a test..

if so, run it with the shortened task file whatever i made so its easier:

.. code-block:: bash

    bash ./run-test-bot

or provide the launcher's path to run it from another working directory:

.. code-block:: bash

    bash /path/to/dishcord/bot-runner-file-ok

The launcher locates the project and ``.env`` relative to itself, enters the Nix
dev shell, and starts the test bot. Prefix commands require the Message
Content intent to be enabled both in the code and in the Discord developer
portal.

The bot registers ``!shell <command>``. Keep the token private; the repository's
``.gitignore`` excludes ``.env``.

The bot first sends a ``shell`` code block with ``$ <command>``, then edits that
same message as output arrives, and adds ``[status] Return code <status>`` when
the command exits. The message has paging, standard-input, and close controls.
Closing the paginator terminates the running command. The package requires
``discord.py>=2.6.0``, the release that added Components V2 support.

this is literally just a thing i saw in jishaku and figured why not just make
a smaller thing but just for shells, so yeah, anyways..

Acknowledgements
===================

https://github.com/scarletcafe/jishaku