"""Small builders shared by the tests."""

import json

from tarjuman import Message, Text, ToolCall


def say(text, **kw):
    """An assistant message with just text."""
    return Message("assistant", [Text(text)], **kw)


def call(name, cid="c1", **args):
    """An assistant message calling one tool."""
    return Message("assistant", [ToolCall(cid, name, json.dumps(args))])


def tool(name, where, cancel=None):
    """One tool's run, bound to a context: `where` is a project folder or a PathPolicy."""
    from functools import partial

    from tarjuman import Cancel

    from diwan.paths import PathPolicy
    from diwan.tools import ToolContext, default_tools

    policy = where if isinstance(where, PathPolicy) else PathPolicy(where)
    return partial(default_tools()[name].run, ToolContext(policy, cancel or Cancel()))
