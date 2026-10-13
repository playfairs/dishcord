"""Public package interface for the Dishcord shell."""

from .shell import (
    ShellReader,
    ShellSession,
    ShellSessionManager,
    clean_ansi,
    sanitize_output,
)

try:
    from .discord_integration import DiscordShell
except Exception:  # pragma: no cover - optional runtime dependency fallback
    DiscordShell = None  # type: ignore[assignment]

__all__ = [
    "DiscordShell",
    "ShellReader",
    "ShellSession",
    "ShellSessionManager",
    "clean_ansi",
    "sanitize_output",
]

__version__ = "0.1.0"
