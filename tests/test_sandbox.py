"""bash in the sandbox (Linux, bubblewrap): writes land only in the project, credentials are
hidden, the network can be cut, and sandboxed commands run without asking."""

import json

import pytest
from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan import sandbox
from diwan.agent import Agent
from diwan.log import Log
from diwan.paths import PathPolicy
from diwan.tools import ToolContext, default_tools

pytestmark = pytest.mark.skipif(sandbox.available() is not None, reason="no bubblewrap here")


def bash(tmp_path, command, network=True):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(exist_ok=True)
    (home / ".ssh").mkdir(parents=True, exist_ok=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY")
    (home / ".netrc").write_text("machine x password y")
    # the real /tmp (read-only), where pytest keeps these folders: the hiding is really tested
    box = sandbox.Sandbox(project, home, network=network, fresh_tmp=False)
    tool = default_tools(box)["bash"]
    return tool.run(ToolContext(PathPolicy(project, home=home)), command=command)


def test_writes_land_only_in_the_project(tmp_path):
    out = bash(tmp_path, f"echo hi > made.txt; echo no > {tmp_path}/outside.txt; echo done")
    assert "done" in out
    assert (tmp_path / "project" / "made.txt").read_text() == "hi\n"
    assert not (tmp_path / "outside.txt").exists()


def test_credentials_are_hidden(tmp_path):
    home = tmp_path / "home"
    out = bash(tmp_path, f"ls -A {home}/.ssh; cat {home}/.netrc; echo end")
    assert "id_ed25519" not in out and "password" not in out and "end" in out


def test_the_network_can_be_cut(tmp_path):
    out = bash(tmp_path, "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"",
               network=False)
    assert "exit code" in out and ("unreachable" in out.lower() or "error" in out.lower())


def test_sandboxed_commands_run_without_asking(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    asked = []
    log = Log.new(cwd=str(project))
    script = [Message("assistant", [ToolCall("c1", "bash", json.dumps({"command": "echo ran"}))]),
              Message("assistant", [Text("done")])]
    a = Agent(Fake(script), "m", log, default_tools(sandbox.Sandbox(project, tmp_path / "home")),
              "sys", approve=lambda c, t, o: asked.append(c.name) or True)
    a.turn("go")
    assert asked == [] and log.messages()[2].tool_results[0].text == "ran"
