"""Shared helpers: command-name rules, base64, mention parsing."""

import re
from base64 import b64encode

COMMAND_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def is_valid_command_name(name):
    return isinstance(name, str) and COMMAND_NAME_RE.fullmatch(name) is not None


def b64(text):
    return b64encode(text.encode("utf-8")).decode("ascii")


def parse_mentions(content):
    """User ids mentioned as <@userId>, skipping `code spans` and ``` fences.

    Matches what the clients actually render as a mention.
    """
    if not content:
        return []
    ids = []
    i = 0
    length = len(content)
    fenced = False
    in_code = False
    while i < length:
        char = content[i]
        if char == "`":
            j = i
            while j < length and content[j] == "`":
                j += 1
            if j - i >= 3:
                if not in_code:
                    fenced = not fenced
            elif not fenced:
                in_code = not in_code
            i = j
            continue
        if not fenced and not in_code and char == "<" and content.startswith("<@", i):
            end = content.find(">", i + 2)
            if end != -1:
                ident = content[i + 2:end]
                if ident and not any(c in ident for c in "<>@ \t\r\n"):
                    ids.append(ident)
                    i = end + 1
                    continue
        i += 1
    return ids