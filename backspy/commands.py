"""Slash commands: Option/Command definitions, signature inference, coercion."""

import inspect
import logging
import typing

from .utils import is_valid_command_name

log = logging.getLogger(__name__)

STRING = "string"
INTEGER = "integer"
NUMBER = "number"
BOOLEAN = "boolean"

OPTION_TYPES = (STRING, INTEGER, NUMBER, BOOLEAN)

_PY_TO_OPTION = {str: STRING, int: INTEGER, float: NUMBER, bool: BOOLEAN}
_STR_TO_OPTION = {"str": STRING, "string": STRING, "int": INTEGER, "integer": INTEGER,
                  "float": NUMBER, "number": NUMBER, "bool": BOOLEAN, "boolean": BOOLEAN}


def _unwrap_annotation(annotation):
    """Optional[X] -> X, so Optional[int] maps to integer instead of string."""
    if typing.get_origin(annotation) is typing.Union:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _normalize_choices(choices):
    if choices is None:
        return None
    normalized = []
    for choice in choices:
        if isinstance(choice, dict):
            normalized.append({"name": str(choice["name"]), "value": choice["value"]})
        elif isinstance(choice, (tuple, list)) and len(choice) == 2:
            normalized.append({"name": str(choice[0]), "value": choice[1]})
        else:
            raise ValueError("choices must be ('name', value) pairs or {'name','value'} dicts")
    if len(normalized) > 25:
        raise ValueError("an option may have at most 25 choices")
    return normalized or None


class Option:
    """A slash-command argument."""

    def __init__(self, name, description=None, *, type=STRING, required=False, choices=None):
        if not is_valid_command_name(name):
            raise ValueError(f"invalid option name {name!r}: 1-32 chars of a-z 0-9 _ -")
        self.name = name
        self.description = description or f"Value for {name}"
        if not 1 <= len(self.description) <= 100:
            raise ValueError(f"option {name!r}: description must be 1-100 characters")
        if type not in OPTION_TYPES:
            raise ValueError(f"option {name!r}: type must be one of {OPTION_TYPES}")
        self.type = type
        self.required = bool(required)
        self.choices = _normalize_choices(choices)
        if self.choices and self.type == BOOLEAN:
            raise ValueError(f"option {name!r}: boolean options take no choices")

    def to_payload(self):
        payload = {"name": self.name, "description": self.description, "type": self.type}
        if self.required:
            payload["required"] = True
        if self.choices:
            payload["choices"] = self.choices
        return payload

    def __repr__(self):
        return f"<Option {self.name!r} type={self.type} required={self.required}>"


class Command:
    """A slash command with its python callback."""

    def __init__(self, name, description, options=None, *, callback=None):
        if not is_valid_command_name(name):
            raise ValueError(f"invalid command name {name!r}: 1-32 chars of a-z 0-9 _ -")
        if not isinstance(description, str) or not 1 <= len(description) <= 100:
            raise ValueError(f"command {name!r}: description must be 1-100 characters")
        options = list(options or [])
        if len(options) > 10:
            raise ValueError(f"command {name!r}: at most 10 options")
        names = [option.name for option in options]
        if len(set(names)) != len(names):
            raise ValueError(f"command {name!r}: duplicate option names")
        # required options must come first - the server enforces this
        self.options = sorted(options, key=lambda option: not option.required)
        self.name = name
        self.description = description
        self.callback = callback

    def to_payload(self):
        return {
            "name": self.name,
            "description": self.description,
            "options": [option.to_payload() for option in self.options],
        }

    def __repr__(self):
        return f"<Command /{self.name} options={len(self.options)}>"


def infer_options(func):
    """Build the Option list from a callback signature, skipping the first (ctx) parameter."""
    try:
        parameters = list(inspect.signature(func).parameters.values())[1:]
    except (TypeError, ValueError):
        return []
    options = []
    for parameter in parameters:
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = _unwrap_annotation(parameter.annotation)
        option_type = _PY_TO_OPTION.get(annotation)
        if option_type is None and isinstance(annotation, str):
            option_type = _STR_TO_OPTION.get(annotation)
        if option_type is None and annotation in OPTION_TYPES:
            option_type = annotation
        required = parameter.default is inspect.Parameter.empty
        options.append(Option(parameter.name, f"Value for {parameter.name}",
                              type=option_type or STRING, required=required))
    return options


def coerce_value(annotation, value):
    """Best-effort conversion of a server-parsed option value to the annotation."""
    annotation = _unwrap_annotation(annotation)
    if annotation in (inspect.Parameter.empty, None, object):
        return value
    try:
        if annotation is bool:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("true", "1", "yes", "on")
        if annotation is int:
            return int(value)
        if annotation is float:
            return float(value)
        if annotation is str:
            return value if isinstance(value, str) else str(value)
    except (TypeError, ValueError):
        return value
    return value