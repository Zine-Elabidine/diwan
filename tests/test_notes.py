"""The model's notes: kept in the log as `note` calls, read back from the log (never the view),
shown by /notes."""

import json

import pytest
from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan import commands
from diwan.agent import Agent
from diwan.context import ContextManager
from diwan.log import Log
from diwan.models import Ref, Router
from diwan.present import summarize_call
from diwan.session import Session
from diwan.tools import ToolError, default_tools
from helpers import say, tool


def note(cid, kind, text):
    return ToolCall(cid, "note", json.dumps({"kind": kind, "text": text}))


def test_notes_are_read_from_the_log_in_order(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    a = Agent(Fake([
        Message("assistant", [Text("Two ways to fix it."),
                              note("n1", "rejected", "regex parsing: breaks on nested quotes"),
                              note("n2", "decision", "use the csv module")]),
        Message("assistant", [note("n3", "progress", "parser fixed, tests next"),
                              ToolCall("n4", "note", "{broken json")]),
        say("done")]), "m", log, default_tools(), "sys")
    assert a.turn("fix the parser").reason == "done"
    assert a.context_manager.notes() == [
        ("rejected", "regex parsing: breaks on nested quotes"),
        ("decision", "use the csv module"),
        ("progress", "parser fixed, tests next")]
    # no approval was needed: the agent's default approve wasn't consulted for notes
    assert all(e.data.get("call_id") not in ("n1", "n2", "n3")
               for e in log.events if e.type == "approval")


def test_interrupted_notes_are_not_notes(tmp_path):
    log = Log.new(cwd=str(tmp_path))
    log.add_message(Message.user("go"))
    log.add_message(Message("assistant", [], partial=[note("n1", "decision", "half-written")]))
    assert ContextManager(log, 1000).notes() == []


def test_the_tool_rejects_unknown_kinds_and_empty_notes(tmp_path):
    run = tool("note", tmp_path)
    assert run(kind="decision", text="x") == "Noted."
    with pytest.raises(ToolError, match="decision, rejected, progress"):
        run(kind="idea", text="x")
    with pytest.raises(ToolError, match="empty"):
        run(kind="progress", text="  ")
    assert default_tools()["note"].parameters["properties"]["kind"]["enum"] == [
        "decision", "rejected", "progress"]


def test_notes_display_and_the_notes_command(tmp_path):
    assert summarize_call(note("n1", "decision", "use csv\nmore detail")) == "decision: use csv"
    s = Session(lambda log, ref: Agent(Fake([Message("assistant", [note("n1", "decision", "use csv")]),
                                             say("ok")]), ref.model, log, default_tools(), "sys"),
                Log.new(cwd=str(tmp_path)), Ref("openrouter", "m"), Router("openrouter"), tmp_path)
    assert commands.run(s, "/notes").text == "no notes yet"
    s.agent.turn("go")
    reply = commands.run(s, "/notes")
    assert reply.kind == "block" and reply.text == "decision: use csv"
