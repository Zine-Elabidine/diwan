"""A small ACP client: drives `diwan --acp` the way an editor would. Used by the tests, and by
hand to try server mode on a real model:

    uv run python scripts/acp_client.py "list the python files here"
    uv run python scripts/acp_client.py --yes -m deepseek/deepseek-v4-flash "add a docstring to x.py"

It starts a session in the current folder, sends the prompt, prints what streams back and asks
before each tool the agent wants approved (unless --yes)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import IO, Any


class AcpError(Exception):
    pass


class Client:
    """JSON-RPC over two streams, one message per line. `on_update` gets each session/update;
    `on_permission` gets each session/request_permission and returns an optionId, or None to
    answer "cancelled"."""

    def __init__(self, to_agent: IO[str], from_agent: IO[str], *,
                 on_update: Callable[[dict[str, Any]], None] | None = None,
                 on_permission: Callable[[dict[str, Any]], str | None] | None = None):
        self.to_agent, self.from_agent = to_agent, from_agent
        self.on_update = on_update or (lambda u: None)
        self.on_permission = on_permission or (lambda p: None)
        self.updates: list[dict[str, Any]] = []
        self.lines: list[str] = []          # everything received, raw: tests check it's all JSON
        self._next = 0
        self._waiting: dict[int, Future[Any]] = {}
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _send(self, msg: dict[str, Any]) -> None:
        with self._lock:
            self.to_agent.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
            self.to_agent.flush()

    def _read(self) -> None:
        for line in self.from_agent:
            self.lines.append(line)
            msg = json.loads(line)
            if "method" not in msg:                       # an answer to one of our calls
                f = self._waiting.pop(msg["id"], None)
                if f is not None:
                    f.set_result(msg)
            elif msg["method"] == "session/update":
                self.updates.append(msg["params"])
                self.on_update(msg["params"])
            elif msg["method"] == "session/request_permission":
                # answered from another thread: the user may take a while
                threading.Thread(target=self._permission, args=(msg,), daemon=True).start()
            elif "id" in msg:
                self._send({"id": msg["id"], "error": {"code": -32601, "message": "not supported"}})
        for f in self._waiting.values():                  # the agent is gone
            f.set_result({"error": {"code": -1, "message": "the agent closed the connection"}})

    def _permission(self, msg: dict[str, Any]) -> None:
        choice = self.on_permission(msg["params"])
        outcome = ({"outcome": "selected", "optionId": choice} if choice is not None
                   else {"outcome": "cancelled"})
        self._send({"id": msg["id"], "result": {"outcome": outcome}})

    def start(self, method: str, params: dict[str, Any]) -> Future[Any]:
        """Send a request; the future gets the result, or raises AcpError."""
        f: Future[Any] = Future()
        out: Future[Any] = Future()
        with self._lock:
            self._next += 1
            rid = self._next
            self._waiting[rid] = f

        def done(_: Future[Any]) -> None:
            msg = f.result()
            if "error" in msg:
                out.set_exception(AcpError(msg["error"]["message"]))
            else:
                out.set_result(msg.get("result"))
        f.add_done_callback(done)
        self._send({"id": rid, "method": method, "params": params})
        return out

    def call(self, method: str, params: dict[str, Any], timeout: float = 600) -> Any:
        return self.start(method, params).result(timeout)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    # the usual steps
    def initialize(self) -> Any:
        return self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                        "clientInfo": {"name": "acp_client", "version": "0"}})

    def new_session(self, cwd: str) -> str:
        return self.call("session/new", {"cwd": cwd, "mcpServers": []})["sessionId"]

    def prompt(self, sid: str, text: str) -> str:
        return self.call("session/prompt", {"sessionId": sid,
                                            "prompt": [{"type": "text", "text": text}]})["stopReason"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Try `diwan --acp` from the terminal.")
    ap.add_argument("prompt")
    ap.add_argument("-m", "--model", help="passed to diwan")
    ap.add_argument("--yes", action="store_true", help="allow every tool call without asking")
    args = ap.parse_args()

    cmd = [sys.executable, "-m", "diwan.cli", "--acp", *(["-m", args.model] if args.model else [])]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            encoding="utf-8", bufsize=1)
    assert proc.stdin is not None and proc.stdout is not None
    ask = threading.Lock()

    def show(u: dict[str, Any]) -> None:
        up = u["update"]
        kind = up["sessionUpdate"]
        if kind == "agent_message_chunk":
            print(up["content"]["text"], end="", flush=True)
        elif kind == "tool_call":
            print(f"\n● {up['title']}  [{up['kind']}, {up['status']}]", flush=True)
        elif kind == "tool_call_update" and up.get("status") in ("completed", "failed"):
            text = (up.get("content") or [{}])[0].get("content", {}).get("text", "")
            first = text.splitlines()[0][:100] if text else ""
            print(f"  ⎿ {up['status']}: {first}", flush=True)

    def permission(p: dict[str, Any]) -> str | None:
        options = {o["optionId"]: o for o in p["options"]}
        if args.yes:
            return "yes"
        with ask:
            names = " / ".join(f"{oid}={o['name']}" for oid, o in options.items())
            answer = input(f"  allow {p['toolCall']['toolCallId']}? {names} › ").strip() or "yes"
        return answer if answer in options else "no"

    client = Client(proc.stdin, proc.stdout, on_update=show, on_permission=permission)
    sid = None
    try:
        info = client.initialize()
        print(f"[{info['agentInfo']['title']} {info['agentInfo']['version']}, "
              f"ACP v{info['protocolVersion']}]")
        sid = client.new_session(os.getcwd())
        stop = client.prompt(sid, args.prompt)
        print(f"\n[stop: {stop}]")
    except KeyboardInterrupt:
        if sid is not None:
            client.notify("session/cancel", {"sessionId": sid})
        print("\n[cancelled]")
    except AcpError as e:
        print(f"\n[error: {e}]", file=sys.stderr)
        return 1
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
