import os
import sys
from pathlib import Path

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]

    for line in (project_root / ".env").read_text().splitlines() if (project_root / ".env").is_file() else []:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)

    sys.path.insert(0, str(project_root / "src"))

    import discord
    from discord.ext import commands

    from dishcord import DiscordShell

    intents = discord.Intents.default()
    intents.message_content = True

    class TestBot(commands.Bot):
        async def setup_hook(self) -> None:
            await self.add_cog(
                DiscordShell(
                    self,
                    owner_ids={int(os.environ["DISCORD_OWNER_ID"])},
                    shell=os.environ.get("SHELL"),
                )
            )

    bot = TestBot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        print(f"Logged in as {bot.user}")

    token = os.environ["DISCORD_TOKEN"]
    bot.run(token)
