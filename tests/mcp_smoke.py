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
    tools_by_name = {tool["name"]: tool for tool in tools}
    expected_tools = {
        "computer",
        "list_apps",
        "get_app_state",
        "click",
        "perform_secondary_action",
        "scroll",
        "drag",
        "type_text",
        "press_key",
        "set_value",
    }
    assert set(tools_by_name) == expected_tools
    assert lines[0]["result"]["serverInfo"]["version"] == "0.3.0"
    actions = set(tools_by_name["computer"]["inputSchema"]["properties"]["action"]["enum"])
    for action in ["screenshot", "windows", "click", "scroll", "drag", "key", "type", "paste_image", "session", "wait", "doctor"]:
        assert action in actions
    assert tools_by_name["computer"]["inputSchema"]["properties"]["keycode"]["type"] == "integer"
    assert tools_by_name["computer"]["inputSchema"]["properties"]["show_cursor"]["type"] == "boolean"
    assert tools_by_name["computer"]["inputSchema"]["properties"]["show_cursor"]["default"] is False
    assert "agent" in tools_by_name["computer"]["inputSchema"]["properties"]["cursor_source"]["enum"]
    assert tools_by_name["computer"]["inputSchema"]["properties"]["cursor_source"]["default"] == "none"
    assert "keys" in tools_by_name["computer"]["inputSchema"]["properties"]["method"]["enum"]
    assert tools_by_name["computer"]["inputSchema"]["properties"]["prefer_related"]["type"] == "boolean"
    assert tools_by_name["computer"]["inputSchema"]["properties"]["restore_clipboard"]["type"] == "boolean"
    assert tools_by_name["computer"]["inputSchema"]["properties"]["related_to"]["type"] == "string"
    assert "begin" in tools_by_name["computer"]["inputSchema"]["properties"]["session_action"]["enum"]

    assert tools_by_name["get_app_state"]["inputSchema"]["required"] == ["app"]
    assert tools_by_name["click"]["inputSchema"]["properties"]["element_index"]["type"] == "string"
    assert tools_by_name["click"]["inputSchema"]["properties"]["x"]["type"] == "number"
    assert tools_by_name["drag"]["inputSchema"]["required"] == ["app", "from_x", "from_y", "to_x", "to_y"]
    assert tools_by_name["press_key"]["inputSchema"]["properties"]["key"]["type"] == "string"
    assert tools_by_name["set_value"]["inputSchema"]["required"] == ["app", "element_index", "value"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
