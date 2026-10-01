"""Small builders shared by the tests."""

import json

from tarjuman import Message, Text, ToolCall


def say(text, **kw):
    """An assistant message with just text."""
    return Message("assistant", [Text(text)], **kw)


def call(name, cid="c1", **args):
    """An assistant message calling one tool."""
    return Message("assistant", [ToolCall(cid, name, json.dumps(args))])
