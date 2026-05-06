#!/usr/bin/env python3
import base64
import json
import mimetypes
import os
import pathlib
import shutil
import subprocess
import sys
import time
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
            "enum": [
                "screenshot",
                "windows",
                "move",
                "click",
                "doubleclick",
                "press",
                "release",
                "scroll",
                "drag",
                "key",
                "type",
                "copy_text",
                "paste_text",
                "paste_file",
                "paste_image",
                "wait",
            ],
            "description": "Computer use action to perform.",
        },
        "target": {
            "type": "string",
            "description": "Hyprland window selector, for example address:0x1234. Required for pointer actions.",
        },
        "x": {"type": "number", "description": "Global logical X coordinate."},
        "y": {"type": "number", "description": "Global logical Y coordinate."},
        "x2": {"type": "number", "description": "Destination global logical X coordinate for drag."},
        "y2": {"type": "number", "description": "Destination global logical Y coordinate for drag."},
        "dx": {"type": "number", "description": "Horizontal scroll wheel ticks."},
        "dy": {"type": "number", "description": "Vertical scroll wheel ticks."},
        "button": {"type": "string", "enum": ["left", "right", "middle", "side", "extra"], "default": "left"},
        "key": {"type": "string", "description": "Key name for key actions, for example enter, escape, v, f5."},
        "keycode": {"type": "integer", "description": "Raw evdev keycode for key actions, ydotool-style."},
        "keys": {"type": "string", "description": "Shortcut string for key actions, for example ctrl+v or alt+tab."},
        "modifiers": {"type": "string", "description": "Optional key modifiers, for example ctrl+shift."},
        "text": {"type": "string", "description": "Text for type/copy_text/paste_text actions."},
        "path": {"type": "string", "description": "Filesystem path for paste_file/paste_image actions."},
        "duration": {"type": "number", "description": "Duration in seconds for wait or drag pacing."},
        "visible_workspace": {"type": "boolean", "description": "For windows, only return active-workspace clients."},
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


def run_available(command: str, args: list[str], *, input_bytes: bytes | None = None, input_text: str | None = None) -> bool:
    executable = shutil.which(command)
    if not executable:
        return False
    run_kwargs: dict[str, Any]
    if input_text is not None:
        run_kwargs = {"input": input_text, "text": True}
    else:
        run_kwargs = {"input": input_bytes}
    proc = subprocess.run(
        [executable, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **run_kwargs,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace") if isinstance(proc.stderr, bytes) else str(proc.stderr)
        raise RuntimeError(f"{command} failed: {stderr.strip()}")
    return True


def result_text(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(info, ensure_ascii=False)}],
        "structuredContent": info,
        "isError": False,
    }


def require_target(args: dict[str, Any]) -> str:
    target = args.get("target")
    if not isinstance(target, str) or not target:
        raise RuntimeError("action requires target")
    return target


def require_xy(args: dict[str, Any]) -> tuple[float, float]:
    x = args.get("x")
    y = args.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise RuntimeError("action requires numeric x and y")
    return float(x), float(y)


def set_clipboard_text(text: str) -> list[str]:
    methods: list[str] = []
    if run_available("wl-copy", ["--type", "text/plain;charset=utf-8"], input_text=text):
        methods.append("wl-copy:text")
    if run_available("xclip", ["-selection", "clipboard", "-t", "text/plain;charset=utf-8"], input_text=text):
        methods.append("xclip:text")
    if not methods:
        raise RuntimeError("no clipboard writer found")
    return methods


def set_clipboard_bytes(data: bytes, mime: str) -> list[str]:
    methods: list[str] = []
    if run_available("wl-copy", ["--type", mime], input_bytes=data):
        methods.append(f"wl-copy:{mime}")
    if run_available("xclip", ["-selection", "clipboard", "-t", mime], input_bytes=data):
        methods.append(f"xclip:{mime}")
    if not methods:
        raise RuntimeError("no clipboard writer found")
    return methods


def file_uri(path: pathlib.Path) -> str:
    return path.resolve().as_uri()


def set_clipboard_uri(path: pathlib.Path) -> list[str]:
    payload = f"{file_uri(path)}\r\n"
    methods: list[str] = []
    if run_available("wl-copy", ["--type", "text/uri-list"], input_text=payload):
        methods.append("wl-copy:text/uri-list")
    if run_available("xclip", ["-selection", "clipboard", "-t", "text/uri-list"], input_text=payload):
        methods.append("xclip:text/uri-list")
    if not methods:
        raise RuntimeError("no clipboard writer found")
    return methods


def keyboard(target: str, key: str, modifiers: str = "", x: float | None = None, y: float | None = None) -> dict[str, Any]:
    cmd = ["keyboard", "--json", target, "tap", key, modifiers]
    if x is not None or y is not None:
        if x is None or y is None:
            raise RuntimeError("keyboard x/y must be passed together")
        cmd.extend(["--x", str(x), "--y", str(y)])
    return call_ctl(cmd)


def key_from_args(args: dict[str, Any]) -> tuple[str, str]:
    keycode = args.get("keycode")
    if isinstance(keycode, int):
        if keycode < 0:
            raise RuntimeError("keycode must be non-negative")
        modifiers = args.get("modifiers") if isinstance(args.get("modifiers"), str) else ""
        return str(keycode), modifiers

    keys = args.get("keys")
    if isinstance(keys, str) and keys:
        parts = [p for p in keys.replace("-", "+").split("+") if p]
        if not parts:
            raise RuntimeError("empty keys shortcut")
        return parts[-1], "+".join(parts[:-1])

    key = args.get("key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("key action requires key or keys")
    modifiers = args.get("modifiers") if isinstance(args.get("modifiers"), str) else ""
    return key, modifiers


def paste(target: str, args: dict[str, Any], methods: list[str]) -> dict[str, Any]:
    x = args.get("x")
    y = args.get("y")
    key_info = keyboard(target, "v", "ctrl", float(x) if isinstance(x, (int, float)) else None, float(y) if isinstance(y, (int, float)) else None)
    return {"ok": True, "target": target, "clipboard": methods, "pasteKey": key_info}


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

    if action == "windows":
        cmd = ["windows"]
        if args.get("visible_workspace"):
            cmd.append("--visible-workspace")
        return result_text(call_ctl(cmd))

    if action in {"move", "click", "doubleclick", "press", "release"}:
        target = require_target(args)
        x, y = require_xy(args)
        button = args.get("button") or "left"
        info = call_ctl(["pointer", "--json", target, str(x), str(y), action, str(button)])
        return result_text(info)

    if action == "scroll":
        target = require_target(args)
        x, y = require_xy(args)
        dy = args.get("dy", 1)
        dx = args.get("dx", 0)
        if not isinstance(dx, (int, float)) or not isinstance(dy, (int, float)):
            raise RuntimeError("scroll requires numeric dx/dy")
        info = call_ctl(["pointer", "--json", target, str(x), str(y), "scroll", str(dy), "--dx", str(dx)])
        return result_text(info)

    if action == "drag":
        target = require_target(args)
        x, y = require_xy(args)
        x2 = args.get("x2")
        y2 = args.get("y2")
        if not isinstance(x2, (int, float)) or not isinstance(y2, (int, float)):
            raise RuntimeError("drag requires numeric x2 and y2")
        button = args.get("button") or "left"
        duration = args.get("duration", 0.15)
        call_ctl(["pointer", "--json", target, str(x), str(y), "press", str(button)])
        time.sleep(max(0.0, min(float(duration), 3.0)) / 2.0)
        call_ctl(["pointer", "--json", target, str(x2), str(y2), "move", str(button)])
        time.sleep(max(0.0, min(float(duration), 3.0)) / 2.0)
        info = call_ctl(["pointer", "--json", target, str(x2), str(y2), "release", str(button)])
        info.update({"from": {"x": x, "y": y}, "to": {"x": x2, "y": y2}})
        return result_text(info)

    if action == "key":
        target = require_target(args)
        key, modifiers = key_from_args(args)
        x = args.get("x")
        y = args.get("y")
        info = keyboard(target, key, modifiers, float(x) if isinstance(x, (int, float)) else None, float(y) if isinstance(y, (int, float)) else None)
        return result_text(info)

    if action == "type":
        target = require_target(args)
        text_value = args.get("text")
        if not isinstance(text_value, str):
            raise RuntimeError("type requires text")
        return result_text(paste(target, args, set_clipboard_text(text_value)))

    if action == "copy_text":
        text_value = args.get("text")
        if not isinstance(text_value, str):
            raise RuntimeError("copy_text requires text")
        return result_text({"ok": True, "clipboard": set_clipboard_text(text_value)})

    if action == "paste_text":
        target = require_target(args)
        text_value = args.get("text")
        if not isinstance(text_value, str):
            raise RuntimeError("paste_text requires text")
        return result_text(paste(target, args, set_clipboard_text(text_value)))

    if action == "paste_file":
        target = require_target(args)
        path_value = args.get("path")
        if not isinstance(path_value, str):
            raise RuntimeError("paste_file requires path")
        path = pathlib.Path(path_value).expanduser()
        if not path.is_file():
            raise RuntimeError(f"file not found: {path}")
        return result_text(paste(target, args, set_clipboard_uri(path)))

    if action == "paste_image":
        target = require_target(args)
        path_value = args.get("path")
        if not isinstance(path_value, str):
            raise RuntimeError("paste_image requires path")
        path = pathlib.Path(path_value).expanduser()
        if not path.is_file():
            raise RuntimeError(f"file not found: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        if not mime.startswith("image/"):
            raise RuntimeError(f"not an image MIME type: {mime}")
        return result_text(paste(target, args, set_clipboard_bytes(path.read_bytes(), mime)))

    if action == "wait":
        duration = args.get("duration", 1)
        if not isinstance(duration, (int, float)):
            raise RuntimeError("wait requires numeric duration")
        time.sleep(max(0.0, min(float(duration), 30.0)))
        return result_text({"ok": True, "duration": duration})

    raise RuntimeError(f"unsupported action: {action}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    req_id = message.get("id")

    if method == "initialize":
        return response(
            req_id,
            {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "hyprcum", "version": "0.2.0"},
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
                        "description": "Take Hyprland screenshots and send background pointer, keyboard, scroll, drag, and clipboard paste actions to a selected window.",
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
