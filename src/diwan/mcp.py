"""MCP servers: tools from other programs (docs/design-decisions.md §11). A small client of our
own: JSON-RPC 2.0 over a child process's stdin/stdout, or over HTTP (streamable HTTP: a POST
answered with JSON or a server-sent event stream). Enough of the protocol for tools:
initialize, tools/list, tools/call, and cancelling a call.

Servers are listed in ~/.diwan/mcp.json, in the format other agents use:

    {"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_TOKEN": "..."}},
        "docs":   {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}}}

Only the user's own file is read: a project's file would let any cloned repository start
programs on this machine."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from tarjuman import Cancel

from . import __version__
from .log import sessions_dir
from .tools.base import Tool, ToolContext, ToolError, clip

PROTOCOL = "2025-06-18"
START_TIMEOUT = 30.0     # seconds for a server to start and list its tools
CALL_TIMEOUT = 600.0     # a tool call longer than this is abandoned


class MCPError(Exception):
    """The server failed, or answered with a JSON-RPC error."""


def config_path() -> Path:
    return sessions_dir().parent / "mcp.json"


def load_config(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """The servers in mcp.json, by name; empty when there is no file. Raises MCPError when
    the file can't be read."""
    path = path or config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise MCPError(f"{path}: {e}") from None
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise MCPError(f"{path}: expected {{\"mcpServers\": {{...}}}}")
    return {name: spec for name, spec in servers.items()
            if isinstance(spec, dict) and not spec.get("disabled")}


# --- transports ---------------------------------------------------------------------------------

class _Stdio:
    """A server as a child process: one JSON message per line each way."""

    def __init__(self, name: str, spec: dict[str, Any]):
        logs = sessions_dir().parent / "mcp-logs"
        logs.mkdir(parents=True, exist_ok=True)
        self._stderr = (logs / f"{name}.log").open("ab")
        env = {**os.environ, **{k: str(v) for k, v in (spec.get("env") or {}).items()}}
        try:
            self.proc = subprocess.Popen(
                [spec["command"], *[str(a) for a in spec.get("args", [])]],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, env=env,
                cwd=spec.get("cwd"))
        except OSError as e:
            self._stderr.close()
            raise MCPError(f"can't start {spec['command']!r}: {e}") from None
        self._waiting: dict[int, Future[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True, name=f"mcp-{name}").start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue   # a server printing to stdout by mistake
            if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    f = self._waiting.pop(msg["id"], None)
                if f is not None:
                    f.set_result(msg)
        with self._lock:   # the process ended: nobody gets an answer now
            waiting, self._waiting = self._waiting, {}
        for f in waiting.values():
            f.set_exception(MCPError("the server stopped"))

    def send(self, msg: dict[str, Any]) -> Future[dict[str, Any]] | None:
        """Write a message; for a request, a future for its answer."""
        f: Future[dict[str, Any]] | None = None
        if "id" in msg:
            f = Future()
            with self._lock:
                self._waiting[msg["id"]] = f
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise MCPError("the server stopped") from None
        return f

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self._stderr.close()


class _HTTP:
    """A server at a URL: each message is a POST; an answer comes back as JSON, or as a
    server-sent event stream that carries it."""

    def __init__(self, name: str, spec: dict[str, Any]):
        self.url = spec["url"]
        self.headers = {"Accept": "application/json, text/event-stream",
                        **{k: str(v) for k, v in (spec.get("headers") or {}).items()}}
        self.client = httpx.Client(timeout=httpx.Timeout(CALL_TIMEOUT, connect=15))
        self.pool = ThreadPoolExecutor(4, thread_name_prefix=f"mcp-{name}")

    def send(self, msg: dict[str, Any]) -> Future[dict[str, Any]] | None:
        if "id" not in msg:
            self._post(msg)
            return None
        return self.pool.submit(self._post, msg)

    def _post(self, msg: dict[str, Any]) -> dict[str, Any]:
        try:
            with self.client.stream("POST", self.url, json=msg, headers=self.headers) as r:
                if r.status_code >= 400:
                    r.read()
                    raise MCPError(f"HTTP {r.status_code}: {r.text[:200]}")
                if sid := r.headers.get("mcp-session-id"):
                    self.headers["Mcp-Session-Id"] = sid
                if "id" not in msg:
                    return {}
                if r.headers.get("content-type", "").startswith("text/event-stream"):
                    for line in r.iter_lines():
                        if line.startswith("data:"):
                            try:
                                event = json.loads(line[5:])
                            except ValueError:
                                continue
                            if isinstance(event, dict) and event.get("id") == msg["id"]:
                                return event
                    raise MCPError("the stream ended without an answer")
                r.read()
                return r.json()
        except httpx.HTTPError as e:
            raise MCPError(f"{type(e).__name__}: {e}") from None

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.client.close()


# --- a server -----------------------------------------------------------------------------------

@dataclass
class Server:
    name: str
    spec: dict[str, Any]
    tools: list[dict[str, Any]] = field(default_factory=list)   # as tools/list gave them
    _transport: _Stdio | _HTTP | None = None
    _next: int = 0

    def start(self) -> None:
        """Start (or connect), initialize, and list the tools. Raises MCPError."""
        if "url" in self.spec:
            self._transport = _HTTP(self.name, self.spec)
        elif "command" in self.spec:
            self._transport = _Stdio(self.name, self.spec)
        else:
            raise MCPError("needs a \"command\" or a \"url\"")
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "diwan", "version": __version__}},
                     timeout=START_TIMEOUT)
        self.notify("notifications/initialized")
        tools, cursor = [], None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {},
                                timeout=START_TIMEOUT)
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools

    def request(self, method: str, params: dict[str, Any], cancel: Cancel | None = None,
                timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        """Send a request and wait for its result. A fired `cancel` tells the server and stops
        waiting (raising the turn's stop through ToolContext); MCPError otherwise."""
        if self._transport is None:
            raise MCPError("not started")
        self._next += 1
        rid = self._next
        f = self._transport.send({"jsonrpc": "2.0", "id": rid, "method": method,
                                  "params": params})
        assert f is not None
        waited = 0.0
        while True:
            if cancel is not None and cancel.cancelled:
                self.notify("notifications/cancelled", {"requestId": rid, "reason": "stopped"})
                raise MCPError("stopped")
            try:
                msg = f.result(timeout=0.2)
                break
            except FutureTimeout:
                waited += 0.2
                if waited >= timeout:
                    self.notify("notifications/cancelled", {"requestId": rid, "reason": "timeout"})
                    raise MCPError(f"no answer to {method} after {timeout:.0f}s") from None
        if "error" in msg:
            err = msg["error"] or {}
            raise MCPError(f"{err.get('message', 'error')} (code {err.get('code')})")
        return msg.get("result") or {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._transport is not None:
            try:
                self._transport.send({"jsonrpc": "2.0", "method": method, **(
                    {"params": params} if params else {})})
            except MCPError:
                pass

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


# --- as tools -----------------------------------------------------------------------------------

def tool_name(server: str, tool: str) -> str:
    """mcp__<server>__<tool>, in the characters and length every provider accepts."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{tool}")
    return name[:64]


class MCPTool(Tool):
    """One server tool. Never read-only: hints from a server are not trusted, so every call
    asks for approval (or "always", per tool)."""
    readonly = False

    def __init__(self, server: Server, spec: dict[str, Any]):
        self.server, self.remote = server, spec["name"]
        self.name = tool_name(server.name, spec["name"])
        self.description = (spec.get("description") or spec.get("title") or spec["name"]).strip()
        schema = spec.get("inputSchema") or {}
        self.parameters = {"type": "object", "properties": {}, **schema}

    def run(self, ctx: ToolContext, **args: Any) -> str:
        try:
            result = self.server.request("tools/call", {"name": self.remote, "arguments": args},
                                         ctx.cancel)
        except MCPError as e:
            raise ToolError(f"{self.server.name}: {e}") from None
        text = content_text(result.get("content") or [])
        if result.get("isError"):
            raise ToolError(clip(text or "the tool failed"))
        if not text and result.get("structuredContent") is not None:
            text = json.dumps(result["structuredContent"], ensure_ascii=False)
        return clip(text or "(no output)")


def content_text(content: list[dict[str, Any]]) -> str:
    parts = []
    for c in content:
        kind = c.get("type")
        if kind == "text":
            parts.append(c.get("text", ""))
        elif kind == "resource":
            r = c.get("resource") or {}
            parts.append(r.get("text") or f"[resource {r.get('uri', '')}]")
        elif kind == "resource_link":
            parts.append(f"[resource {c.get('uri', '')}: {c.get('name', '')}]")
        else:
            parts.append(f"[{kind} omitted]")
    return "\n".join(parts)


@dataclass
class Connected:
    servers: list[Server]
    tools: dict[str, Tool]
    errors: dict[str, str]    # server name -> why it isn't there

    def close(self) -> None:
        for s in self.servers:
            s.close()


# the servers this process started (cli.py), for /mcp
current: Connected | None = None


def connect(config: dict[str, dict[str, Any]]) -> Connected:
    """Start every server at once; a server that fails is reported, not fatal."""
    servers = [Server(name, spec) for name, spec in config.items()]
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max(len(servers), 1)) as pool:
        for s, f in [(s, pool.submit(s.start)) for s in servers]:
            try:
                f.result()
            except Exception as e:  # a broken server must not stop the session
                errors[s.name] = str(e) or type(e).__name__
                s.close()
    up = [s for s in servers if s.name not in errors]
    tools: dict[str, Tool] = {}
    for s in up:
        for spec in s.tools:
            if isinstance(spec, dict) and spec.get("name"):
                t = MCPTool(s, spec)
                tools[t.name] = t
    return Connected(up, tools, errors)


def describe(c: Connected | None) -> str:
    """For /mcp: each server, its tools, and the ones that failed to start."""
    if c is None or not (c.servers or c.errors):
        return f"No MCP servers. Add them to {config_path()} ({{\"mcpServers\": {{...}}}})."
    lines = []
    for s in c.servers:
        names = ", ".join(t.get("name", "?") for t in s.tools) or "no tools"
        lines.append(f"● {s.name}: {names}")
    for name, why in c.errors.items():
        lines.append(f"✗ {name}: {why}")
    return "\n".join(lines)
