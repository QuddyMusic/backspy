"""backspy - a Python bot library for Backspace instances."""

import logging

logging.getLogger("backspy").addHandler(logging.NullHandler())

from .client import Client
from .commands import BOOLEAN, INTEGER, NUMBER, STRING, Command, Option
from .errors import BackspaceError, ConnectionClosed, HTTPException, LoginFailure, RateLimited
from .models import Attachment, Interaction, Message, Object, ReactionEvent, TypingEvent, User
from .owner import OwnerClient
from .utils import parse_mentions

__version__ = "1.0.0"

__all__ = [
    "Client", "OwnerClient",
    "Message", "User", "Interaction", "Object", "ReactionEvent", "TypingEvent", "Attachment",
    "Command", "Option", "STRING", "INTEGER", "NUMBER", "BOOLEAN",
    "BackspaceError", "HTTPException", "RateLimited", "LoginFailure", "ConnectionClosed",
    "parse_mentions",
]