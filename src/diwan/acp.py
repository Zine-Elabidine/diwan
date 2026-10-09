"""Server mode: Diwan as an ACP agent (Agent Client Protocol, v1), driven by another program
over stdio: an editor (Zed, JetBrains, Neovim), a window, a room.

It is a third front-end, like ui.py and tui.py: the agent's events go out as `session/update`
notifications, approvals as `session/request_permission` requests, and prompts come in as
`session/prompt`. Messages are JSON-RPC 2.0, one per line; stdout carries nothing else.

Diwan's own additions (ACP lets agents add methods starting with `_`): `_diwan/message` gives
a session a message whenever (runner.py): queued during a turn, or it wakes an idle session;
such a turn's end is announced with the `_diwan/turn_ended` notification.

One process holds several sessions. For now they all run in the server's folder: sandbox,
skills and MCP are set per process (cli.py), so a session elsewhere is refused. The client's
MCP servers are not used (~/.diwan/mcp.json is), and Telepathy memory is off."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import IO, Any

from tarjuman import ReasoningDelta, TextDelta, ToolCall

from . import __version__
from .agent import Agent
from .events import ChildEvent, ToolFinished, ToolStarted, TurnEnded, UIEvent, UserAdded
from .log import Log, sessions_dir
from .present import summarize_call
from .runner import Busy, Runner
from .session import Approvals
from .tools import Tool

PROTOCOL_VERSION = 1

STOP_REASONS = {"done": "end_turn", "max_tokens": "max_tokens", "max_steps": "max_turn_requests",
                "interrupted": "cancelled"}

# what a client shows for each tool (ACP tool kinds)
KINDS = {"read": "read", "write": "edit", "edit": "edit", "grep": "search", "glob": "search",
         "recall": "search", "bash": "execute", "note": "think", "agent": "other"}

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700, -32600, -32601, -32602, -32603)


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# builds an agent on a log, wired to the session's `on` and `approve`
MakeAgent = Callable[[Log, Callable[[UIEvent], None],
                      Callable[[ToolCall, Tool, bool], bool]], Agent]


class AcpSession:
    """One conversation: its agent, and the bridge from its events to the client."""

    def __init__(self, server: Server, log: Log, make_agent: MakeAgent, auto: bool):
        self.server, self.log, self.id = server, log, log.id
        self.approvals = Approvals(auto=auto)
        self.announced: set[str] = set()     # tool calls the client already knows about
        self.pending: set[Future[Any]] = set()   # permission requests awaiting an answer
        self.agent = make_agent(log, self.on, self.approve)
        self.runner = Runner(self.agent, on_wake=self._woke, on_woken_end=self._woken_end)

    def _woke(self, text: str) -> None:
        """A turn starts with no prompt: show the client what started it."""
        self.update({"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": text}})

    def _woken_end(self, ended: TurnEnded) -> None:
        """No session/prompt waits for this turn: say it ended (a Diwan notification)."""
        self.server.notify("_diwan/turn_ended", {"sessionId": self.id,
                                                 "stopReason": STOP_REASONS.get(ended.reason, "end_turn"),
                                                 **({"error": ended.error} if ended.error else {})})

    def update(self, body: dict[str, Any]) -> None:
        self.server.notify("session/update", {"sessionId": self.id, "update": body})

    def on(self, ev: UIEvent) -> None:
        if isinstance(ev, ChildEvent) and isinstance(ev.event, ToolStarted | ToolFinished):
            ev = ev.event   # a child agent's tool: the client may have approved it, show its end
        if isinstance(ev, TextDelta):
            self.update({"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": ev.text}})
        elif isinstance(ev, ReasoningDelta):
            self.update({"sessionUpdate": "agent_thought_chunk",
                         "content": {"type": "text", "text": ev.text}})
        elif isinstance(ev, UserAdded):
            self.update({"sessionUpdate": "user_message_chunk",
                         "content": {"type": "text", "text": ev.text}})
        elif isinstance(ev, ToolStarted):
            if ev.call.id in self.announced:
                self.update({"sessionUpdate": "tool_call_update", "toolCallId": ev.call.id,
                             "status": "in_progress"})
            else:
                self.update(self._tool_call(ev.call, "in_progress"))
        elif isinstance(ev, ToolFinished):
            if ev.call.id not in self.announced:   # refused before it started (denied, bad path)
                self.update(self._tool_call(ev.call, "pending"))
            self.update({"sessionUpdate": "tool_call_update", "toolCallId": ev.call.id,
                         "status": "failed" if ev.result.is_error else "completed",
                         "content": [{"type": "content",
                                      "content": {"type": "text", "text": ev.result.text}}]})

    def _tool_call(self, call: ToolCall, status: str) -> dict[str, Any]:
        self.announced.add(call.id)
        try:
            args = call.args()
        except ValueError:
            args = {}
        body: dict[str, Any] = {"sessionUpdate": "tool_call", "toolCallId": call.id,
                                "title": f"{call.name} {summarize_call(call)}".strip(),
                                "kind": KINDS.get(call.name, "other"), "status": status,
                                "rawInput": args}
        if isinstance(args.get("path"), str):
            body["locations"] = [{"path": str(self.agent.paths.resolve(args["path"]))}]
        return body

    def approve(self, call: ToolCall, tool: Tool, outside: bool) -> bool:
        """Ask the client, and wait. "Always" is never offered for a call outside the project."""
        if call.id not in self.announced:
            self.update(self._tool_call(call, "pending"))
        if self.approvals.covers(call, outside):
            return True
        options = [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                   *([] if outside else [{"optionId": "always", "name": f"Always allow {call.name}",
                                          "kind": "allow_always"}]),
                   {"optionId": "no", "name": "Reject", "kind": "reject_once"}]
        answer = self.server.request("session/request_permission", {
            "sessionId": self.id, "toolCall": {"toolCallId": call.id}, "options": options},
            track=self.pending)
        outcome = (answer or {}).get("outcome") or {}
        if outcome.get("outcome") != "selected":   # cancelled
            return False
        return self.approvals.answer(call, str(outcome.get("optionId")), outside)

    def cancel(self) -> None:
        """Stop the running turn. Waiting permission requests are answered "no" here: the
        agent must not wait for a client that has moved on."""
        self.agent.interrupt()
        for f in list(self.pending):
            if not f.done():
                f.set_result({"outcome": {"outcome": "cancelled"}})

    def replay(self) -> None:
        """The conversation so far, as the client would have seen it (session/load)."""
        for m in self.log.messages():
            kind = {"user": "user_message_chunk", "assistant": "agent_message_chunk"}.get(m.role)
            if kind and m.text:
                self.update({"sessionUpdate": kind, "content": {"type": "text", "text": m.text}})


class Server:
    def __init__(self, inp: IO[str], out: IO[str], cwd: Path,
                 new_log: Callable[[Path], Log], make_agent: MakeAgent, auto: bool = False):
        """new_log: a fresh session log for a folder. auto: approve every call (-y)."""
        self.inp, self.out, self.cwd = inp, out, cwd.resolve()
        self.new_log, self.make_agent, self.auto = new_log, make_agent, auto
        self.sessions: dict[str, AcpSession] = {}
        self._write = threading.Lock()
        self._next_id = 0
        self._waiting: dict[int, Future[Any]] = {}   # our requests, by id
        self._workers: list[threading.Thread] = []

    # --- wire ---------------------------------------------------------------------------------

    def send(self, msg: dict[str, Any]) -> None:
        line = json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False)   # no raw newlines
        with self._write:
            self.out.write(line + "\n")
            self.out.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any],
                track: set[Future[Any]] | None = None) -> Any:
        """Send a request to the client and wait for its answer (from an agent's thread)."""
        f: Future[Any] = Future()
        with self._write:
            self._next_id += 1
            rid = self._next_id
            self._waiting[rid] = f
        if track is not None:
            track.add(f)
        try:
            self.send({"id": rid, "method": method, "params": params})
            return f.result()
        finally:
            self._waiting.pop(rid, None)
            if track is not None:
                track.discard(f)

    def serve(self) -> int:
        """Read messages until the client closes stdin."""
        for line in self.inp:
            if line.strip():
                self.handle(line)
        for f in list(self._waiting.values()):   # the client is gone: nobody will answer
            if not f.done():
                f.set_result(None)
        for s in self.sessions.values():
            s.cancel()
        for t in self._workers:
            t.join(timeout=10)
        return 0

    def handle(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            self.send({"id": None, "error": {"code": PARSE_ERROR, "message": str(e)}})
            return
        if "method" not in msg:   # an answer to one of our requests
            f = self._waiting.get(msg.get("id"))
            if f is not None and not f.done():
                f.set_result(msg.get("result"))
            return
        rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if method == "session/prompt":   # long: in its own thread, so cancel can come in
            t = threading.Thread(target=self._answer, args=(rid, method, params), daemon=True)
            self._workers.append(t)
            t.start()
        else:
            self._answer(rid, method, params)

    def _answer(self, rid: Any, method: str, params: dict[str, Any]) -> None:
        try:
            result = self.dispatch(method, params)
        except RpcError as e:
            if rid is not None:
                self.send({"id": rid, "error": {"code": e.code, "message": str(e)}})
            return
        except Exception as e:   # a bug must not kill the server
            if rid is not None:
                self.send({"id": rid, "error": {"code": INTERNAL,
                                                "message": f"{type(e).__name__}: {e}"}})
            return
        if rid is not None:      # notifications get no answer
            self.send({"id": rid, "result": result})

    # --- methods ------------------------------------------------------------------------------

    def dispatch(self, method: str, p: dict[str, Any]) -> Any:
        if method == "initialize":
            return {"protocolVersion": PROTOCOL_VERSION,
                    "agentCapabilities": {"loadSession": True,
                                          "promptCapabilities": {"image": False, "audio": False,
                                                                 "embeddedContext": True}},
                    "agentInfo": {"name": "diwan", "title": "Diwan", "version": __version__},
                    "authMethods": []}
        if method == "session/new":
            log = self.new_log(self._folder(p))
            s = self.sessions[log.id] = AcpSession(self, log, self.make_agent, self.auto)
            return {"sessionId": s.id}
        if method == "session/load":
            self._folder(p)
            sid = str(p.get("sessionId", ""))
            path = sessions_dir() / f"{sid}.jsonl"
            if "/" in sid or not path.is_file():
                raise RpcError(INVALID_PARAMS, f"no session {sid!r}")
            s = self.sessions.get(sid)
            if s is None:
                log = Log.load(path)
                meta = log.events[0].data
                if "parent" in meta:
                    raise RpcError(INVALID_PARAMS, f"{sid} is a child agent's session")
                if Path(str(meta.get("cwd", ""))).resolve() != self.cwd:
                    raise RpcError(INVALID_PARAMS, f"{sid} was started in {meta.get('cwd')}")
                s = AcpSession(self, log, self.make_agent, self.auto)
            self.sessions[sid] = s
            s.replay()
            return {}
        if method == "session/prompt":
            s = self._session(p)
            try:
                ended = s.runner.run(prompt_text(p.get("prompt") or []))
            except Busy:
                raise RpcError(INVALID_REQUEST, "this session is already working on a turn") from None
            if ended.reason == "error":
                raise RpcError(INTERNAL, ended.error or "the turn failed")
            return {"stopReason": STOP_REASONS[ended.reason]}
        if method == "session/cancel":
            self._session(p).cancel()
            return None
        if method == "_diwan/message":
            # a message for the session, whenever: the model gets it at its next request, or
            # it wakes an idle session (the War Room's messages between sessions use this)
            text = str(p.get("text", "")).strip()
            if not text:
                raise RpcError(INVALID_PARAMS, "the message is empty")
            return {"woke": self._session(p).runner.deliver(text)}
        raise RpcError(METHOD_NOT_FOUND, f"unknown method {method!r}")

    def _session(self, p: dict[str, Any]) -> AcpSession:
        s = self.sessions.get(str(p.get("sessionId")))
        if s is None:
            raise RpcError(INVALID_PARAMS, f"no session {p.get('sessionId')!r}")
        return s

    def _folder(self, p: dict[str, Any]) -> Path:
        cwd = Path(str(p.get("cwd", "")))
        if not cwd.is_absolute():
            raise RpcError(INVALID_PARAMS, "cwd must be an absolute path")
        if cwd.resolve() != self.cwd:
            raise RpcError(INVALID_PARAMS, f"this server works in {self.cwd}; start one in {cwd}")
        return self.cwd


def prompt_text(blocks: list[dict[str, Any]]) -> str:
    """The prompt's content blocks as one message: text, and embedded files with their name."""
    parts = []
    for b in blocks:
        if b.get("type") == "text":
            parts.append(str(b.get("text", "")))
        elif b.get("type") == "resource":
            r = b.get("resource") or {}
            if "text" in r:
                parts.append(f"<file uri=\"{r.get('uri', '')}\">\n{r['text']}\n</file>")
        elif b.get("type") == "resource_link":
            parts.append(f"(file: {b.get('uri', '')})")
    return "\n\n".join(p for p in parts if p)


def run(cwd: Path, new_log: Callable[[Path], Log], make_agent: MakeAgent, auto: bool) -> int:
    """Serve on the real stdin/stdout. Anything else printed (a library, a stray print) goes
    to stderr: one stray line on stdout would break the client."""
    out = sys.stdout
    sys.stdout = sys.stderr
    return Server(sys.stdin, out, cwd, new_log, make_agent, auto).serve()
