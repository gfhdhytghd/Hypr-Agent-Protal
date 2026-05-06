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
    candidates = [pathlib.Path(p) for p in [os.environ.get("HYPR_AGENT_PROTAL_CTL")] if p]
    candidates.extend(
        [
            ROOT / "scripts" / "hypr-agent-protalctl",
            pathlib.Path(__file__).resolve().with_name("hypr-agent-protalctl"),
        ]
    )
    found = shutil.which("hypr-agent-protalctl")
    if found:
        candidates.append(pathlib.Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("hypr-agent-protalctl not found")


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
                "session",
                "wait",
            ],
            "description": "Computer use action to perform.",
        },
        "target": {
            "type": "string",
            "description": "Hyprland window selector, for example address:0x1234. Optional for screenshot; required for input actions.",
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
        "show_cursor": {
            "type": "boolean",
            "description": "For screenshot, draw the cursor indicator on the returned image.",
            "default": True,
        },
        "cursor_source": {
            "type": "string",
            "enum": ["auto", "agent", "hyprland", "none"],
            "description": "For screenshot, choose the cursor indicator source. auto prefers the last background pointer coordinate.",
            "default": "auto",
        },
        "method": {
            "type": "string",
            "enum": ["auto", "paste", "keys"],
            "description": "For type, choose auto, clipboard paste, or literal key events.",
            "default": "auto",
        },
        "prefer_related": {
            "type": "boolean",
            "description": "For text/paste actions, prefer a same-process related popup over the root target window.",
            "default": True,
        },
        "restore_clipboard": {
            "type": "boolean",
            "description": "For type/paste_text, restore the previous text clipboard after sending paste.",
            "default": True,
        },
        "restore_delay": {
            "type": "number",
            "description": "Seconds to wait before restoring clipboard after paste.",
            "default": 0.35,
        },
        "path": {"type": "string", "description": "Filesystem path for paste_file/paste_image actions."},
        "duration": {"type": "number", "description": "Duration in seconds for wait or drag pacing."},
        "session_action": {
            "type": "string",
            "enum": ["begin", "sync", "end"],
            "description": "For session, begin/sync/end a related-window workspace guard session.",
        },
        "visible_workspace": {"type": "boolean", "description": "For windows, only return active-workspace clients."},
        "related_to": {
            "type": "string",
            "description": "For windows, return the selected Hyprland window and same-process related windows such as XWayland popups.",
        },
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
        raise RuntimeError((proc.stderr or proc.stdout or f"hypr-agent-protalctl exited {proc.returncode}").strip())
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


def run_available(command: str, args: list[str], *, input_bytes: bytes | None = None, input_text: str | None = None, timeout: float = 5.0) -> bool:
    executable = shutil.which(command)
    if not executable:
        return False
    run_kwargs: dict[str, Any]
    if input_text is not None:
        run_kwargs = {"input": input_text, "text": True}
    else:
        run_kwargs = {"input": input_bytes}
    try:
        proc = subprocess.run(
            [executable, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            **run_kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{command} timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{command} failed with exit code {proc.returncode}")
    return True


def run_capture(command: str, args: list[str], *, timeout: float = 5.0) -> tuple[bool, bytes]:
    executable = shutil.which(command)
    if not executable:
        return False, b""
    try:
        proc = subprocess.run([executable, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, b""
    if proc.returncode != 0:
        return False, b""
    return True, proc.stdout


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


def target_address(target: str) -> str | None:
    if target.startswith("address:"):
        return target.split(":", 1)[1].lower()
    return None


def target_uses_xwayland(target: str, window: dict[str, Any] | None = None) -> bool | None:
    if isinstance(window, dict) and isinstance(window.get("xwayland"), bool):
        return bool(window["xwayland"])

    address = target_address(target)
    try:
        windows = call_ctl(["windows", "--related-to", target]).get("windows", [])
    except Exception:
        return None

    for candidate in windows:
        candidate_address = str(candidate.get("address") or "").lower()
        if (address and candidate_address == address) or candidate.get("hyprAgentProtalRelation") == "self":
            if isinstance(candidate.get("xwayland"), bool):
                return bool(candidate["xwayland"])
    return None


def clipboard_snapshot_text() -> dict[str, Any]:
    snapshot: dict[str, Any] = {"wayland": None, "x11": None, "attempted": ["wayland", "x11"]}

    ok, data = run_capture("wl-paste", ["--no-newline", "--type", "text/plain;charset=utf-8"])
    if ok:
        snapshot["wayland"] = {"mime": "text/plain;charset=utf-8", "data": data}
    else:
        ok, data = run_capture("wl-paste", ["--no-newline", "--type", "text/plain"])
        if ok:
            snapshot["wayland"] = {"mime": "text/plain", "data": data}

    ok, data = run_capture("xclip", ["-selection", "clipboard", "-out", "-target", "UTF8_STRING"])
    if ok:
        snapshot["x11"] = {"mime": "UTF8_STRING", "data": data}
    else:
        ok, data = run_capture("xclip", ["-selection", "clipboard", "-out", "-target", "STRING"])
        if ok:
            snapshot["x11"] = {"mime": "STRING", "data": data}

    return snapshot


def clipboard_text_matches(protocol: str, mime: str, data: bytes) -> bool:
    if protocol == "wayland":
        ok, current = run_capture("wl-paste", ["--no-newline", "--type", mime], timeout=1.0)
    else:
        ok, current = run_capture("xclip", ["-selection", "clipboard", "-out", "-target", mime], timeout=1.0)
    return (ok and current == data) or (not ok and data == b"")


def restore_clipboard_text(snapshot: dict[str, Any]) -> dict[str, Any]:
    methods: list[str] = []
    checks: list[dict[str, Any]] = []
    pending_checks: list[tuple[str, str, str, bytes]] = []

    def restore_writer(command: str, args: list[str], data: bytes | None = None) -> bool:
        try:
            return run_available(command, args, input_bytes=data)
        except RuntimeError:
            return False

    wayland = snapshot.get("wayland")
    if isinstance(wayland, dict) and isinstance(wayland.get("data"), bytes):
        mime = str(wayland.get("mime") or "text/plain;charset=utf-8")
        if restore_writer("wl-copy", ["--type", mime], wayland["data"]):
            method = f"wl-copy:{mime}"
            methods.append(method)
            pending_checks.append((method, "wayland", mime, wayland["data"]))
    elif "wayland" in snapshot.get("attempted", []):
        if restore_writer("wl-copy", ["--clear"]):
            method = "wl-copy:clear"
            methods.append(method)
            pending_checks.append((method, "wayland", "text/plain;charset=utf-8", b""))

    x11 = snapshot.get("x11")
    if isinstance(x11, dict) and isinstance(x11.get("data"), bytes):
        mime = str(x11.get("mime") or "UTF8_STRING")
        if restore_writer("xclip", ["-selection", "clipboard"], x11["data"]):
            method = "xclip:text"
            methods.append(method)
            pending_checks.append((method, "x11", mime, x11["data"]))
    elif "x11" in snapshot.get("attempted", []):
        if restore_writer("xclip", ["-selection", "clipboard"], b""):
            method = "xclip:clear-text"
            methods.append(method)
            pending_checks.append((method, "x11", "UTF8_STRING", b""))

    if pending_checks:
        time.sleep(0.05)
    for method, protocol, mime, data in pending_checks:
        checks.append({"method": method, "verified": clipboard_text_matches(protocol, mime, data), "bytes": len(data)})

    verified = bool(checks) and all(bool(check.get("verified")) for check in checks)
    return {"methods": methods, "checks": checks, "verified": verified}


def try_clipboard_write(
    methods: list[str],
    errors: list[str],
    label: str,
    command: str,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    input_text: str | None = None,
) -> bool:
    try:
        if run_available(command, args, input_bytes=input_bytes, input_text=input_text):
            methods.append(label)
            return True
    except RuntimeError as exc:
        errors.append(f"{label}: {exc}")
    return False


def set_clipboard_text(text: str, target_is_xwayland: bool | None = None) -> list[str]:
    methods: list[str] = []
    errors: list[str] = []

    def write_wayland() -> bool:
        return try_clipboard_write(methods, errors, "wl-copy:text", "wl-copy", ["--type", "text/plain;charset=utf-8"], input_text=text)

    def write_x11() -> bool:
        return try_clipboard_write(methods, errors, "xclip:text", "xclip", ["-selection", "clipboard"], input_text=text)

    writers = [write_x11, write_wayland] if target_is_xwayland is True else [write_wayland, write_x11]
    for writer in writers:
        if writer() and target_is_xwayland is not None:
            break

    if not methods:
        detail = f": {'; '.join(errors)}" if errors else ""
        raise RuntimeError(f"no clipboard writer found{detail}")
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


ASCII_KEYMAP: dict[str, tuple[str, str]] = {
    "\n": ("enter", ""),
    "\r": ("enter", ""),
    "\t": ("tab", ""),
    " ": ("space", ""),
    "-": ("minus", ""),
    "_": ("minus", "shift"),
    "=": ("equal", ""),
    "+": ("equal", "shift"),
    "[": ("leftbrace", ""),
    "{": ("leftbrace", "shift"),
    "]": ("rightbrace", ""),
    "}": ("rightbrace", "shift"),
    "\\": ("backslash", ""),
    "|": ("backslash", "shift"),
    ";": ("semicolon", ""),
    ":": ("semicolon", "shift"),
    "'": ("apostrophe", ""),
    '"': ("apostrophe", "shift"),
    "`": ("grave", ""),
    "~": ("grave", "shift"),
    ",": ("comma", ""),
    "<": ("comma", "shift"),
    ".": ("dot", ""),
    ">": ("dot", "shift"),
    "/": ("slash", ""),
    "?": ("slash", "shift"),
    "1": ("1", ""),
    "!": ("1", "shift"),
    "2": ("2", ""),
    "@": ("2", "shift"),
    "3": ("3", ""),
    "#": ("3", "shift"),
    "4": ("4", ""),
    "$": ("4", "shift"),
    "5": ("5", ""),
    "%": ("5", "shift"),
    "6": ("6", ""),
    "^": ("6", "shift"),
    "7": ("7", ""),
    "&": ("7", "shift"),
    "8": ("8", ""),
    "*": ("8", "shift"),
    "9": ("9", ""),
    "(": ("9", "shift"),
    "0": ("0", ""),
    ")": ("0", "shift"),
}


def key_for_char(ch: str) -> tuple[str, str] | None:
    if "a" <= ch <= "z":
        return ch, ""
    if "A" <= ch <= "Z":
        return ch.lower(), "shift"
    return ASCII_KEYMAP.get(ch)


def can_type_with_keys(text: str) -> bool:
    return all(key_for_char(ch) is not None for ch in text)


def type_with_keys(target: str, text: str, *, delay: float = 0.015) -> dict[str, Any]:
    sent: list[dict[str, Any]] = []
    for ch in text:
        key = key_for_char(ch)
        if key is None:
            raise RuntimeError(f"character cannot be typed as key events: U+{ord(ch):04X}")
        key_name, modifiers = key
        sent.append(keyboard(target, key_name, modifiers))
        if delay > 0:
            time.sleep(delay)
    return {"ok": True, "target": target, "method": "keys", "characters": len(text), "keys": len(sent)}


def prefer_related_target(target: str, enabled: bool = True) -> tuple[str, dict[str, Any] | None]:
    if not enabled:
        return target, None

    try:
        windows = call_ctl(["windows", "--related-to", target]).get("windows", [])
    except Exception:
        return target, None

    related = [
        window
        for window in windows
        if window.get("hyprAgentProtalRelation") == "related"
        and isinstance(window.get("address"), str)
        and not window.get("hidden", False)
        and window.get("mapped", True)
    ]
    if not related:
        return target, None

    def score(window: dict[str, Any]) -> tuple[int, int, float]:
        kind_score = 0 if window.get("hyprAgentProtalWindowKind") == "popup" else 1
        focus = window.get("focusHistoryID")
        focus_score = int(focus) if isinstance(focus, int) else 1_000_000
        width, height = window.get("size") or [0, 0]
        area = float(width or 0) * float(height or 0)
        return kind_score, focus_score, area

    selected = sorted(related, key=score)[0]
    return f"address:{selected['address']}", selected


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


def paste(
    target: str,
    args: dict[str, Any],
    methods: list[str],
    *,
    text_clipboard_snapshot: dict[str, Any] | None = None,
    target_for_input: str | None = None,
    related: dict[str, Any] | None = None,
) -> dict[str, Any]:
    x = args.get("x")
    y = args.get("y")
    if target_for_input is None:
        prefer_related = args.get("prefer_related")
        target_for_input, related = prefer_related_target(target, True if not isinstance(prefer_related, bool) else prefer_related)
    time.sleep(0.08)
    key_info = keyboard(target_for_input, "v", "ctrl", float(x) if isinstance(x, (int, float)) else None, float(y) if isinstance(y, (int, float)) else None)

    restore_info: dict[str, Any] = {"methods": [], "checks": [], "verified": False}
    should_restore = args.get("restore_clipboard")
    if text_clipboard_snapshot is not None and (True if not isinstance(should_restore, bool) else should_restore):
        delay = args.get("restore_delay", 0.35)
        if not isinstance(delay, (int, float)):
            delay = 0.35
        time.sleep(max(0.0, min(float(delay), 5.0)))
        restore_info = restore_clipboard_text(text_clipboard_snapshot)

    return {
        "ok": True,
        "target": target,
        "resolvedTarget": target_for_input,
        "relatedTarget": related,
        "method": "paste",
        "clipboard": methods,
        "pasteKey": key_info,
        "clipboardRestored": bool(restore_info["verified"]),
        "clipboardRestore": restore_info["methods"],
        "clipboardRestoreChecks": restore_info["checks"],
    }


def type_text(target: str, text: str, args: dict[str, Any]) -> dict[str, Any]:
    method = args.get("method") if isinstance(args.get("method"), str) else "auto"
    if method not in {"auto", "paste", "keys"}:
        raise RuntimeError("type method must be auto, paste, or keys")

    prefer_related = args.get("prefer_related")
    target_for_input, related = prefer_related_target(target, True if not isinstance(prefer_related, bool) else prefer_related)

    if method == "keys" or (method == "auto" and len(text) <= 80 and can_type_with_keys(text)):
        info = type_with_keys(target_for_input, text)
        info.update({"target": target, "resolvedTarget": target_for_input, "relatedTarget": related})
        return info

    target_is_xwayland = target_uses_xwayland(target_for_input, related)
    snapshot = clipboard_snapshot_text()
    methods = set_clipboard_text(text, target_is_xwayland)
    return paste(target, args, methods, text_clipboard_snapshot=snapshot, target_for_input=target_for_input, related=related)


def computer(args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action")
    if action == "screenshot":
        cmd = ["screenshot", "--base64"]
        target = args.get("target")
        if isinstance(target, str) and target:
            cmd.extend(["--target", target])
        if args.get("show_cursor") is False:
            cmd.append("--no-cursor")
        cursor_source = args.get("cursor_source")
        if isinstance(cursor_source, str) and cursor_source:
            cmd.extend(["--cursor-source", cursor_source])
        info = call_ctl(cmd)
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
        related_to = args.get("related_to")
        if isinstance(related_to, str) and related_to:
            cmd.extend(["--related-to", related_to])
        return result_text(call_ctl(cmd))

    if action == "session":
        session_action = args.get("session_action")
        if not isinstance(session_action, str) or not session_action:
            raise RuntimeError("session requires session_action")
        cmd = ["session", "--json", session_action]
        target = args.get("target")
        if isinstance(target, str) and target:
            cmd.append(target)
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
        return result_text(type_text(target, text_value, args))

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
        prefer_related = args.get("prefer_related")
        target_for_input, related = prefer_related_target(target, True if not isinstance(prefer_related, bool) else prefer_related)
        target_is_xwayland = target_uses_xwayland(target_for_input, related)
        snapshot = clipboard_snapshot_text()
        methods = set_clipboard_text(text_value, target_is_xwayland)
        return result_text(paste(target, args, methods, text_clipboard_snapshot=snapshot, target_for_input=target_for_input, related=related))

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
                "serverInfo": {"name": "hypr-agent-protal", "version": "0.2.2"},
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
                        "title": "hypr-agent-protal",
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
