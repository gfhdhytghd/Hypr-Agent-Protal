#!/usr/bin/env python3
import json
import pathlib
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: mcp_smoke.py <mcp-script>", file=sys.stderr)
        return 2

    script = pathlib.Path(sys.argv[1]).resolve()
    repo = script.parents[1]
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "hypr-agent-protal-smoke", "version": "0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            "",
        ]
    )

    proc = subprocess.run(
        [sys.executable, str(script)],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return proc.returncode or 1

    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert lines[0]["result"]["serverInfo"]["name"] == "hypr-agent-protal"
    tools = lines[1]["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "computer"
    actions = set(tools[0]["inputSchema"]["properties"]["action"]["enum"])
    for action in ["screenshot", "windows", "click", "scroll", "drag", "key", "type", "paste_image", "session", "wait"]:
        assert action in actions
    assert tools[0]["inputSchema"]["properties"]["keycode"]["type"] == "integer"
    assert "keys" in tools[0]["inputSchema"]["properties"]["method"]["enum"]
    assert tools[0]["inputSchema"]["properties"]["prefer_related"]["type"] == "boolean"
    assert tools[0]["inputSchema"]["properties"]["restore_clipboard"]["type"] == "boolean"
    assert tools[0]["inputSchema"]["properties"]["related_to"]["type"] == "string"
    assert "begin" in tools[0]["inputSchema"]["properties"]["session_action"]["enum"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
