#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]


def find_ctl() -> pathlib.Path:
    candidates = [pathlib.Path(p) for p in [os.environ.get("HYPRCUMCTL")] if p]
    candidates.extend(
        [
            ROOT / "scripts" / "hyprcumctl",
            pathlib.Path(__file__).resolve().with_name("hyprcumctl"),
        ]
    )
    found = shutil.which("hyprcumctl")
    if found:
        candidates.append(pathlib.Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("hyprcumctl not found")


COMPUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["screenshot", "move", "click", "doubleclick", "press", "release"],
            "description": "Computer use action to perform.",
        },
        "target": {
            "type": "string",
            "description": "Hyprland window selector, for example address:0x1234. Required for pointer actions.",
        },
        "x": {"type": "number", "description": "Global logical X coordinate."},
        "y": {"type": "number", "description": "Global logical Y coordinate."},
        "button": {"type": "string", "enum": ["left", "right", "middle", "side", "extra"], "default": "left"},
    },
    "required": ["action"],
    "additionalProperties": False,
}


def response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def call_ctl(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run([str(find_ctl()), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"hyprcumctl exited {proc.returncode}").strip())
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


def computer(args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action")
    if action == "screenshot":
        info = call_ctl(["screenshot", "--base64"])
        data = info.pop("pngBase64")
        text = json.dumps(info, ensure_ascii=False)
        return {
            "content": [
                {"type": "text", "text": text},
                {"type": "image", "mimeType": "image/png", "data": data},
            ],
            "structuredContent": info,
            "isError": False,
        }

    if action in {"move", "click", "doubleclick", "press", "release"}:
        target = args.get("target")
        x = args.get("x")
        y = args.get("y")
        if not isinstance(target, str) or not target:
            raise RuntimeError("pointer actions require target")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise RuntimeError("pointer actions require numeric x and y")
        button = args.get("button") or "left"
        info = call_ctl(["pointer", "--json", target, str(x), str(y), action, str(button)])
        return {
            "content": [{"type": "text", "text": json.dumps(info, ensure_ascii=False)}],
            "structuredContent": info,
            "isError": False,
        }

    raise RuntimeError(f"unsupported action: {action}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    req_id = message.get("id")

    if method == "initialize":
        return response(
            req_id,
            {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "hyprcum", "version": "0.1.0"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )

    if method == "tools/list":
        return response(
            req_id,
            {
                "tools": [
                    {
                        "name": "computer",
                        "title": "Hyprland Computer",
                        "description": "Take Hyprland screenshots and send background pointer input to a selected window.",
                        "inputSchema": COMPUTER_SCHEMA,
                    }
                ]
            },
        )

    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "computer":
            return response(req_id, {"content": [{"type": "text", "text": "unknown tool"}], "isError": True})
        try:
            return response(req_id, computer(params.get("arguments") or {}))
        except Exception as exc:
            return response(req_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})

    if method and method.startswith("notifications/"):
        return None

    return error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            out = handle(msg)
        except Exception as exc:
            out = error(None, -32603, str(exc))
        if out is not None:
            print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
