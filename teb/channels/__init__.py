"""
teb.channels — Channel layer for execution updates.

Provides a uniform abstraction for sending notifications and receiving
structured commands across multiple messaging platforms.
"""

from __future__ import annotations

from teb.channels.base import Channel, CommandResult
from teb.channels.discord import DiscordChannel
from teb.channels.router import route_command
from teb.channels.slack import SlackChannel
from teb.channels.whatsapp import WhatsAppChannel

__all__ = [
    "Channel",
    "CommandResult",
    "DiscordChannel",
    "SlackChannel",
    "WhatsAppChannel",
    "route_command",
]
