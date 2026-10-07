"""A tiny MCP server over stdio for the tests: an `echo` tool, a `fail` tool, and a `slow`
tool that waits until it is cancelled."""

import json
import sys
import time

TOOLS = [
    {"name": "echo", "description": "Echo the text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Takes forever.", "inputSchema": {"type": "object"}},
]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


print("a stray line on stdout", flush=True)   # some servers do this; the client must skip it
for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
            "serverInfo": {"name": "test", "version": "1"}}})
    elif method == "tools/list":
        cursor = msg["params"].get("cursor")
        page = TOOLS[:2] if cursor is None else TOOLS[2:]          # two pages
        result = {"tools": page, **({"nextCursor": "2"} if cursor is None else {})}
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == "echo":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"echo: {args['text']}"}]}})
        elif name == "fail":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "it broke"}], "isError": True}})
        elif name == "slow":
            time.sleep(0.05)   # never answers; the client cancels
    elif method == "notifications/cancelled":
        sys.stderr.write(f"cancelled {msg['params']['requestId']}\n")
        sys.stderr.flush()
