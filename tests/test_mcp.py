"""MCP servers as tools: started from ~/.diwan/mcp.json, listed, called, cancelled; a broken
server is reported and the session goes on."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from tarjuman import Cancel

from diwan import mcp
from diwan.paths import PathPolicy
from diwan.tools import ToolContext, ToolError

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_server.py")


@pytest.fixture
def connected():
    c = mcp.connect({"test": {"command": sys.executable, "args": [SERVER]},
                     "broken": {"command": "/no/such/program"}})
    yield c
    c.close()


def test_tools_from_a_stdio_server(connected, tmp_path):
    assert set(connected.tools) == {"mcp__test__echo", "mcp__test__fail", "mcp__test__slow"}
    assert "broken" in connected.errors and "can't start" in connected.errors["broken"]
    echo = connected.tools["mcp__test__echo"]
    assert echo.parameters["required"] == ["text"] and not echo.readonly
    assert echo.run(ToolContext(PathPolicy(tmp_path)), text="hi") == "echo: hi"
    with pytest.raises(ToolError, match="it broke"):
        connected.tools["mcp__test__fail"].run(ToolContext(PathPolicy(tmp_path)))


def test_a_stopped_turn_cancels_the_call(connected, tmp_path):
    cancel = Cancel()
    threading.Timer(0.3, cancel.cancel).start()
    with pytest.raises(ToolError, match="stopped"):
        connected.tools["mcp__test__slow"].run(ToolContext(PathPolicy(tmp_path), cancel))


def test_config_file(tmp_path):
    p = tmp_path / "mcp.json"
    assert mcp.load_config(p) == {}
    p.write_text(json.dumps({"mcpServers": {"a": {"command": "x"}, "b": {"url": "u", "disabled": True}}}))
    assert list(mcp.load_config(p)) == ["a"]
    p.write_text("{broken")
    with pytest.raises(mcp.MCPError):
        mcp.load_config(p)
    assert mcp.tool_name("my server", "do.thing") == "mcp__my_server__do_thing"


class _Handler(BaseHTTPRequestHandler):
    """Streamable HTTP: JSON for initialize, an event stream for the rest."""

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in msg:
            self.send_response(202)
            self.end_headers()
            return
        if msg["method"] == "initialize":
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "s1")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
            return
        assert self.headers["Mcp-Session-Id"] == "s1"
        result = ({"tools": [{"name": "time", "inputSchema": {"type": "object"}}]}
                  if msg["method"] == "tools/list" else {"content": [{"type": "text", "text": "noon"}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b": ping\n\n")
        event = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())

    def log_message(self, *a):
        pass


def test_tools_from_an_http_server(tmp_path):
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = mcp.connect({"web": {"url": f"http://127.0.0.1:{httpd.server_port}/mcp"}})
    try:
        assert not c.errors and list(c.tools) == ["mcp__web__time"]
        assert c.tools["mcp__web__time"].run(ToolContext(PathPolicy(tmp_path))) == "noon"
    finally:
        c.close()
        httpd.shutdown()
