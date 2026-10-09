"""Server mode (ACP): a client drives Diwan over newline-delimited JSON-RPC. The server runs in
this process on pipes, with a scripted model; one test starts the real `diwan --acp`."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan.acp import Server
from diwan.agent import Agent
from diwan.log import Log
from diwan.tools import default_tools

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from acp_client import AcpError, Client


def call(id, name, **args):
    return Message("assistant", [ToolCall(id, name, json.dumps(args))])


def say(text):
    return Message("assistant", [Text(text)])


@pytest.fixture
def connect(tmp_path):
    """connect(script, on_permission=...) -> (client, project): a server on pipes."""
    opened = []

    def start(script, on_permission=None):
        project = tmp_path / "project"
        project.mkdir(exist_ok=True)
        fake = Fake(script)

        def make_agent(log, on, approve):
            return Agent(fake, "fake-model", log, default_tools(), "sys", approve=approve, on=on,
                         sleep=lambda s: None)

        c_read, s_write = (os.fdopen(fd, m, encoding="utf-8") for fd, m in zip(os.pipe(), "rw", strict=True))
        s_read, c_write = (os.fdopen(fd, m, encoding="utf-8") for fd, m in zip(os.pipe(), "rw", strict=True))
        server = Server(s_read, s_write, project, lambda folder: Log.new(cwd=str(folder)),
                        make_agent)
        t = threading.Thread(target=lambda: (server.serve(), s_write.close()), daemon=True)
        t.start()
        client = Client(c_write, c_read, on_permission=on_permission)
        opened.append((c_write, t))
        client.initialize()
        return client, project

    yield start
    for w, t in opened:
        w.close()
        t.join(timeout=5)


def kinds(client):
    return [u["update"]["sessionUpdate"] for u in client.updates]


def test_a_prompt_streams_back_and_ends_the_turn(connect):
    client, project = connect([say("Hello from Diwan.")])
    sid = client.new_session(str(project))
    assert client.prompt(sid, "hi") == "end_turn"
    text = "".join(u["update"]["content"]["text"] for u in client.updates
                   if u["update"]["sessionUpdate"] == "agent_message_chunk")
    assert text == "Hello from Diwan."
    assert all(u["sessionId"] == sid for u in client.updates)
    assert all(json.loads(line)["jsonrpc"] == "2.0" for line in client.lines)


def test_the_client_approves_or_rejects_tool_calls(connect):
    asked = []

    def answer(p):
        asked.append(p)
        return "yes" if len(asked) == 1 else "no"

    client, project = connect([call("c1", "write", path="a.txt", content="A"),
                               call("c2", "write", path="b.txt", content="B"), say("done")],
                              on_permission=answer)
    sid = client.new_session(str(project))
    assert client.prompt(sid, "write two files") == "end_turn"
    assert (project / "a.txt").read_text() == "A" and not (project / "b.txt").exists()
    assert [o["kind"] for o in asked[0]["options"]] == ["allow_once", "allow_always", "reject_once"]
    first = [u["update"] for u in client.updates if u["update"].get("toolCallId") == "c1"]
    assert [(u["sessionUpdate"], u["status"]) for u in first] == [
        ("tool_call", "pending"), ("tool_call_update", "in_progress"),
        ("tool_call_update", "completed")]
    assert first[0]["kind"] == "edit" and first[0]["locations"][0]["path"].endswith("a.txt")
    second = [u["update"] for u in client.updates if u["update"].get("toolCallId") == "c2"]
    assert second[-1]["status"] == "failed"


def test_always_stops_asking_for_that_tool(connect):
    asked = []
    client, project = connect([call("c1", "write", path="a.txt", content="A"),
                               call("c2", "write", path="b.txt", content="B"), say("done")],
                              on_permission=lambda p: asked.append(p) or "always")
    sid = client.new_session(str(project))
    client.prompt(sid, "go")
    assert len(asked) == 1 and (project / "b.txt").exists()


def test_cancel_while_waiting_for_approval(connect):
    asking = threading.Event()

    def never_answer(p):
        asking.set()
        threading.Event().wait(30)   # the user walked away: no answer (None = cancelled)

    client, project = connect([call("c1", "write", path="a.txt", content="A"), say("never")],
                              on_permission=never_answer)
    sid = client.new_session(str(project))
    pending = client.start("session/prompt", {"sessionId": sid,
                                              "prompt": [{"type": "text", "text": "go"}]})
    assert asking.wait(5)
    client.notify("session/cancel", {"sessionId": sid})
    assert pending.result(5) == {"stopReason": "cancelled"}
    assert not (project / "a.txt").exists()


def test_a_loaded_session_replays_its_conversation(connect):
    client, project = connect([say("first answer"), say("second answer")])
    sid = client.new_session(str(project))
    client.prompt(sid, "first question")
    client.updates.clear()
    assert client.call("session/load", {"sessionId": sid, "cwd": str(project), "mcpServers": []}) == {}
    replay = [(u["update"]["sessionUpdate"], u["update"]["content"]["text"]) for u in client.updates]
    assert replay == [("user_message_chunk", "first question"),
                      ("agent_message_chunk", "first answer")]
    assert client.prompt(sid, "and then?") == "end_turn"


def test_a_child_agents_approved_tool_call_finishes(connect):
    client, project = connect([call("p1", "agent", task="write a.txt", readonly=False),
                               call("k1", "write", path="a.txt", content="A"),   # the child
                               say("written"), say("the child wrote it")],
                              on_permission=lambda p: "yes")
    sid = client.new_session(str(project))
    assert client.prompt(sid, "go") == "end_turn"
    k1 = [u["update"] for u in client.updates if u["update"].get("toolCallId") == "k1"]
    assert k1[0]["status"] == "pending" and k1[-1]["status"] == "completed"


def test_a_cancel_between_turns_does_not_stop_the_next_one(connect):
    client, project = connect([say("still here")])
    sid = client.new_session(str(project))
    client.notify("session/cancel", {"sessionId": sid})
    assert client.prompt(sid, "hi") == "end_turn"


def test_only_this_folders_own_sessions_can_be_loaded(connect, tmp_path):
    client, project = connect([])
    elsewhere = Log.new(cwd=str(tmp_path / "other"))
    child = Log.new(cwd=str(project), parent="x", task="t")
    for log, why in ((elsewhere, "was started in"), (child, "child agent")):
        with pytest.raises(AcpError, match=why):
            client.call("session/load", {"sessionId": log.id, "cwd": str(project), "mcpServers": []})


def test_bad_requests_get_errors_not_a_dead_server(connect, tmp_path):
    client, project = connect([])
    with pytest.raises(AcpError, match="works in"):
        client.new_session(str(tmp_path))                 # another folder
    with pytest.raises(AcpError, match="absolute"):
        client.new_session("project")
    with pytest.raises(AcpError, match="no session"):
        client.prompt("nope", "hi")
    with pytest.raises(AcpError, match="unknown method"):
        client.call("session/fly", {})
    assert client.new_session(str(project))               # still serving


def test_the_real_command_writes_only_protocol_on_stdout(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "diwan.cli", "--acp", "-m", "openrouter:some/model"],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": 1}}) + "\n",
        capture_output=True, text=True, cwd=tmp_path, timeout=60, check=False)
    lines = proc.stdout.splitlines()
    assert proc.returncode == 0 and len(lines) == 1, proc.stderr
    assert json.loads(lines[0])["result"]["agentInfo"]["name"] == "diwan"
