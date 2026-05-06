#!/usr/bin/env python3
import base64
import json
import mimetypes
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_VERSION = "0.3.4"
SNAPSHOTS: dict[str, dict[str, Any]] = {}

_ATSPI_INIT_ERROR: str | None | bool = None
_ATSPI: Any = None
A11Y_LAUNCH_ENV = {
    "NO_AT_BRIDGE": "0",
    "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
    "GTK_MODULES": "gail:atk-bridge",
}
CHROMIUM_LIKE_EXECUTABLES = {
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "brave",
    "brave-browser",
    "microsoft-edge",
    "vivaldi",
    "opera",
    "electron",
    "code",
    "codium",
    "discord",
    "slack",
}


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
                "doctor",
                "launch",
                "launch_app",
                "open_app",
                "get_cursor_position",
                "left_click",
                "right_click",
                "middle_click",
                "double_click",
                "triple_click",
                "hover",
                "left_click_drag",
            ],
            "description": "Computer use action to perform.",
        },
        "app": {"type": "string", "description": "Preferred app/window selector for semantic tools and compatibility aliases. Use this instead of target/global coordinates when possible."},
        "command": {"type": "string", "description": "Command line to launch for launch/open_app actions. Prefer app for simple app names."},
        "args": {"type": "array", "items": {"type": "string"}, "description": "Additional launch arguments."},
        "url": {"type": "string", "description": "URL or file target to pass to a launched app, usually with new_window for browsers."},
        "new_window": {"type": "boolean", "default": True, "description": "For browser launches, request a new window and use about:blank when no URL is supplied."},
        "reuse_existing": {"type": "boolean", "default": False, "description": "For launch/open_app, return an already running matching app instead of forcing a new launch."},
        "timeout": {"type": "number", "description": "Seconds to wait for a launched app window to appear."},
        "target": {
            "type": "string",
            "description": "Low-level Hyprland window selector, for example address:0x1234. Prefer app plus screenshot/window-relative coordinates unless you intentionally need global-coordinate fallback.",
        },
        "coordinate": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 2,
            "description": "Compatibility coordinate pair. With app, interpreted in coordinate_space; with target and no app, treated as low-level global logical coordinates.",
        },
        "start_coordinate": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 2,
            "description": "Compatibility drag start coordinate pair.",
        },
        "coordinate_space": {
            "type": "string",
            "enum": ["screenshot", "window", "global"],
            "default": "screenshot",
            "description": "Coordinate space for app-relative compatibility aliases. Prefer screenshot pixels from get_app_state. Global is only for low-level computer fallback.",
        },
        "x": {"type": "number", "description": "Global logical X coordinate."},
        "y": {"type": "number", "description": "Global logical Y coordinate."},
        "x2": {"type": "number", "description": "Destination global logical X coordinate for drag."},
        "y2": {"type": "number", "description": "Destination global logical Y coordinate for drag."},
        "dx": {"type": "number", "description": "Horizontal scroll wheel ticks."},
        "dy": {"type": "number", "description": "Vertical scroll wheel ticks."},
        "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "Compatibility scroll direction."},
        "scroll_amount": {"type": "number", "description": "Compatibility scroll tick amount."},
        "button": {"type": "string", "enum": ["left", "right", "middle", "side", "extra"], "default": "left"},
        "key": {"type": "string", "description": "Key name for key actions, for example enter, escape, v, f5."},
        "keycode": {"type": "integer", "description": "Raw evdev keycode for key actions, ydotool-style."},
        "keys": {"type": "string", "description": "Shortcut string for key actions, for example ctrl+v or alt+tab."},
        "modifiers": {"type": "string", "description": "Optional key modifiers, for example ctrl+shift."},
        "text": {"type": "string", "description": "Text for type/copy_text/paste_text actions."},
        "show_cursor": {
            "type": "boolean",
            "description": "For screenshot debugging, draw the cursor indicator on the returned image. The real desktop indicator is rendered by the Hyprland plugin.",
            "default": False,
        },
        "cursor_source": {
            "type": "string",
            "enum": ["auto", "agent", "hyprland", "none"],
            "description": "For screenshot debugging, choose the cursor indicator source. auto prefers the last background pointer coordinate.",
            "default": "none",
        },
        "method": {
            "type": "string",
            "enum": ["auto", "paste", "keys"],
            "description": "For type, choose auto, clipboard paste, or literal key events.",
            "default": "auto",
        },
        "repeat": {"type": "integer", "description": "Number of times to repeat a key action."},
        "source": {"type": "string", "enum": ["auto", "hyprland", "agent"], "default": "auto", "description": "Cursor source for get_cursor_position."},
        "include_global": {"type": "boolean", "default": False, "description": "Include Hyprland global logical coordinates in get_cursor_position diagnostics."},
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


def string_property(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def number_property(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def integer_property(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def coordinate_schema(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": description}


def coordinate_space_property(*, include_global: bool = False) -> dict[str, Any]:
    values = ["screenshot", "window"]
    if include_global:
        values.append("global")
    return {
        "type": "string",
        "enum": values,
        "default": "screenshot",
        "description": "Coordinate space. screenshot uses get_app_state pixels; window uses logical coordinates from the target window's top-left.",
    }


READ_ONLY_ANNOTATIONS = {"readOnlyHint": True}
ACTION_ANNOTATIONS = {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
LAUNCH_ANNOTATIONS = {"destructiveHint": False, "idempotentHint": False, "openWorldHint": True}


def tool_definitions() -> list[dict[str, Any]]:
    app = string_property("App name, Hyprland class/title, pid, or address:0x... selector.")
    launch_app = string_property("App name, desktop id, executable, or simple command to launch, for example chromium, dolphin, org.kde.dolphin.desktop.")
    launch_schema = object_schema(
        {
            "app": launch_app,
            "command": string_property("Full command line to launch. Prefer app unless custom arguments are needed."),
            "args": {"type": "array", "items": {"type": "string"}, "description": "Additional command arguments."},
            "url": string_property("Optional URL or file target to pass to the launched app."),
            "new_window": {"type": "boolean", "default": True, "description": "For browsers, request a new window and open about:blank when no URL is supplied."},
            "reuse_existing": {"type": "boolean", "default": False, "description": "Return an already running matching app instead of launching another copy."},
            "timeout": number_property("Seconds to wait for a Hyprland window to appear. Defaults to 8."),
        }
    )
    element_index = string_property("Element index from the last get_app_state result.")
    coordinate = coordinate_schema("(x, y) coordinate in coordinate_space. Defaults to screenshot pixels from get_app_state.")
    start_coordinate = coordinate_schema("(x, y) starting coordinate for a drag in coordinate_space.")
    point_props = {
        "app": app,
        "element_index": element_index,
        "coordinate": coordinate,
        "x": number_property("X coordinate in coordinate_space."),
        "y": number_property("Y coordinate in coordinate_space."),
        "coordinate_space": coordinate_space_property(),
    }
    screenshot_props = {
        "app": app,
        "target": string_property("Hyprland window selector, for example address:0x1234."),
        "show_cursor": {"type": "boolean", "default": False},
        "cursor_source": {"type": "string", "enum": ["auto", "agent", "hyprland", "none"], "default": "none"},
    }
    return [
        {
            "name": "computer",
            "title": "hypr-agent-protal",
            "description": "hypr-agent-protal compatibility tool for Hyprland background Computer Use. Prefer app plus get_app_state/screenshot coordinates; target/x/y global coordinates are only the low-level fallback. Do not use the obsolete hyprcum namespace.",
            "inputSchema": COMPUTER_SCHEMA,
        },
        {
            "name": "list_apps",
            "description": "List running Hyprland apps/windows available to hypr-agent-protal. Start here before choosing a target app. If the desired app is missing, call launch_app/open_app.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema({}),
        },
        {
            "name": "launch_app",
            "description": "Launch an app through Hyprland and return the matching window selector. Use this when list_apps does not show the app the user asked to operate. For Chromium/Chrome, url/new_window launches a new accessible browser window.",
            "annotations": LAUNCH_ANNOTATIONS,
            "inputSchema": launch_schema,
        },
        {
            "name": "open_app",
            "description": "Compatibility alias for launch_app.",
            "annotations": LAUNCH_ANNOTATIONS,
            "inputSchema": launch_schema,
        },
        {
            "name": "get_app_state",
            "description": "Get a target app/window screenshot and accessibility tree. Call this before action tools, then use element_index or screenshot/window-relative coordinates.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema({"app": app}, ["app"]),
        },
        {
            "name": "read_app_state",
            "description": "Compatibility alias for get_app_state.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema({"app": app}, ["app"]),
        },
        {
            "name": "screenshot",
            "description": "Capture an unoccluded screenshot for an app/window, or the visible compositor if no app is passed. Prefer app over low-level target.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema(screenshot_props),
        },
        {
            "name": "get_screenshot",
            "description": "Compatibility alias for screenshot.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema(screenshot_props),
        },
        {
            "name": "get_cursor_position",
            "description": "Return the current cursor position in monitor, screenshot, or window-relative coordinates.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema(
                {
                    "app": app,
                    "source": {"type": "string", "enum": ["auto", "hyprland", "agent"], "default": "auto"},
                    "include_global": {"type": "boolean", "default": False},
                }
            ),
        },
        {
            "name": "list_windows",
            "description": "Compatibility alias for list_apps.",
            "annotations": READ_ONLY_ANNOTATIONS,
            "inputSchema": object_schema({}),
        },
        {
            "name": "click",
            "description": "Click an element by index or screenshot/window-relative coordinates.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(
                {
                    **point_props,
                    "click_count": integer_property("Number of clicks. Defaults to 1."),
                    "mouse_button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                },
                ["app"],
            ),
        },
        {
            "name": "perform_secondary_action",
            "description": "Invoke a secondary AT-SPI action exposed by an element.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema({"app": app, "element_index": element_index, "action": string_property("Secondary action name.")}, ["app", "element_index", "action"]),
        },
        {
            "name": "scroll",
            "description": "Scroll an element or coordinate in a direction by pages.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(
                {
                    **point_props,
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "pages": number_property("Number of pages to scroll. Defaults to 1."),
                },
                ["app", "direction"],
            ),
        },
        {
            "name": "drag",
            "description": "Drag from one screenshot/window-relative coordinate to another.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(
                {
                    "app": app,
                    "start_coordinate": start_coordinate,
                    "coordinate": coordinate_schema("(x, y) destination coordinate in coordinate_space."),
                    "from_x": number_property("Start X coordinate in coordinate_space."),
                    "from_y": number_property("Start Y coordinate in coordinate_space."),
                    "to_x": number_property("End X coordinate in coordinate_space."),
                    "to_y": number_property("End Y coordinate in coordinate_space."),
                    "coordinate_space": coordinate_space_property(),
                    "duration": number_property("Drag pacing duration in seconds. Defaults to 0.2."),
                },
                ["app"],
            ),
        },
        {
            "name": "left_click",
            "description": "Compatibility alias for click with the left button.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "right_click",
            "description": "Compatibility alias for click with the right button.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "middle_click",
            "description": "Compatibility alias for click with the middle button.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "double_click",
            "description": "Compatibility alias for a double left click.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "triple_click",
            "description": "Compatibility alias for a triple left click.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "hover",
            "description": "Compatibility alias: move the background pointer to an element or coordinate without clicking.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "move_mouse",
            "description": "Compatibility alias for hover.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(point_props, ["app"]),
        },
        {
            "name": "left_click_drag",
            "description": "Compatibility alias for drag using start_coordinate and coordinate.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(
                {
                    "app": app,
                    "start_coordinate": start_coordinate,
                    "coordinate": coordinate,
                    "coordinate_space": coordinate_space_property(),
                    "duration": number_property("Drag pacing duration in seconds. Defaults to 0.2."),
                },
                ["app", "start_coordinate", "coordinate"],
            ),
        },
        {
            "name": "type_text",
            "description": "Type literal text into the target app, preferring AT-SPI editable text then background input fallback.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema({"app": app, "text": string_property("Literal text to type.")}, ["app", "text"]),
        },
        {
            "name": "type",
            "description": "Compatibility alias for type_text.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema({"app": app, "text": string_property("Literal text to type.")}, ["app", "text"]),
        },
        {
            "name": "press_key",
            "description": "Press a key or key-combination, using xdotool-style syntax such as ctrl+v, Return, or super+c.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema({"app": app, "key": string_property("Key or key-combination to press.")}, ["app", "key"]),
        },
        {
            "name": "key",
            "description": "Compatibility alias for press_key. Accepts key or text.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema(
                {
                    "app": app,
                    "key": string_property("Key or key-combination to press."),
                    "text": string_property("Compatibility key text."),
                    "repeat": integer_property("Number of times to repeat the key. Defaults to 1."),
                },
                ["app"],
            ),
        },
        {
            "name": "set_value",
            "description": "Set the value of a settable accessibility element.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema({"app": app, "element_index": element_index, "value": string_property("Value to assign.")}, ["app", "element_index", "value"]),
        },
        {
            "name": "wait",
            "description": "Compatibility alias: wait for a short duration.",
            "annotations": ACTION_ANNOTATIONS,
            "inputSchema": object_schema({"duration": number_property("Seconds to wait. Defaults to 1.")}),
        },
    ]


def response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def call_ctl(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run([str(find_ctl()), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=hyprctl_environment(), check=False)
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


def hyprctl_environment() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return env

    proc = subprocess.run(["hyprctl", "instances", "-j"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            instances = json.loads(proc.stdout)
        except json.JSONDecodeError:
            instances = []
        if instances:
            wayland_display = env.get("WAYLAND_DISPLAY")
            matching = [item for item in instances if wayland_display and item.get("wl_socket") == wayland_display]
            selected = max(matching or instances, key=lambda item: int(item.get("time") or 0))
            if selected.get("instance"):
                env["HYPRLAND_INSTANCE_SIGNATURE"] = str(selected["instance"])
            if not env.get("WAYLAND_DISPLAY") and selected.get("wl_socket"):
                env["WAYLAND_DISPLAY"] = str(selected["wl_socket"])
            return env

    runtime_root = pathlib.Path(env["XDG_RUNTIME_DIR"]) / "hypr"
    sockets = sorted(runtime_root.glob("*/.socket.sock"), key=lambda path: path.stat().st_mtime, reverse=True)
    if sockets:
        env["HYPRLAND_INSTANCE_SIGNATURE"] = sockets[0].parent.name
    return env


def result_text(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(info, ensure_ascii=False)}],
        "structuredContent": info,
        "isError": False,
    }


def mcp_text(text: str, *, is_error: bool = False, structured: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def mcp_snapshot_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    content = [{"type": "text", "text": render_snapshot_text(snapshot)}]
    image = snapshot.get("screenshotPngBase64")
    if isinstance(image, str) and image:
        content.append({"type": "image", "mimeType": "image/png", "data": image})
    structured = {k: v for k, v in snapshot.items() if k != "screenshotPngBase64"}
    return {"content": content, "structuredContent": structured, "isError": False}


def require_target(args: dict[str, Any]) -> str:
    target = args.get("target")
    if not isinstance(target, str) or not target:
        raise RuntimeError("action requires target")
    return target


def require_xy(args: dict[str, Any]) -> tuple[float, float]:
    pair = coordinate_pair(args.get("coordinate"))
    if pair is not None:
        return pair
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


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def window_selector(window: dict[str, Any]) -> str:
    address = str(window.get("address") or "")
    if not address:
        raise RuntimeError("target window has no address")
    return f"address:{address}"


def window_geometry(window: dict[str, Any]) -> dict[str, float]:
    at = window.get("at") or [0, 0]
    size = window.get("size") or [0, 0]
    return {"x": float(at[0] or 0), "y": float(at[1] or 0), "width": float(size[0] or 0), "height": float(size[1] or 0)}


def list_hypr_windows() -> list[dict[str, Any]]:
    windows = call_ctl(["windows"]).get("windows", [])
    return [window for window in windows if isinstance(window, dict) and window.get("mapped", True) and not window.get("hidden", False)]


def resolve_hypr_window(app: str) -> dict[str, Any]:
    query = normalize(app)
    if not query:
        raise RuntimeError("Missing required argument: app")

    windows = list_hypr_windows()

    def address_matches(window: dict[str, Any]) -> bool:
        address = normalize(window.get("address"))
        return query == address or query == address.removeprefix("0x") or (query.startswith("address:") and query.split(":", 1)[1] == address)

    def score(window: dict[str, Any]) -> tuple[int, int, float]:
        fields = {
            "class": normalize(window.get("class")),
            "initialClass": normalize(window.get("initialClass")),
            "title": normalize(window.get("title")),
            "initialTitle": normalize(window.get("initialTitle")),
            "pid": str(window.get("pid") or ""),
        }
        if address_matches(window):
            primary = 0
        elif fields["pid"] == query:
            primary = 1
        elif fields["class"] == query or fields["initialClass"] == query:
            primary = 2
        elif fields["title"] == query or fields["initialTitle"] == query:
            primary = 3
        elif query in fields["class"] or query in fields["initialClass"]:
            primary = 4
        elif query in fields["title"] or query in fields["initialTitle"]:
            primary = 5
        else:
            primary = 100
        focus = window.get("focusHistoryID")
        focus_score = int(focus) if isinstance(focus, int) else 1_000_000
        geom = window_geometry(window)
        area = geom["width"] * geom["height"]
        return primary, focus_score, -area

    matches = [window for window in windows if score(window)[0] < 100]
    if not matches:
        raise RuntimeError(f'appNotFound("{app}")')
    return sorted(matches, key=score)[0]


def list_apps_text(windows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for window in sorted(windows, key=lambda item: (normalize(item.get("class")), normalize(item.get("title")), int(item.get("pid") or 0))):
        workspace = window.get("workspace") or {}
        name = str(window.get("class") or window.get("initialClass") or "unknown")
        title = str(window.get("title") or "untitled")
        address = str(window.get("address") or "")
        pid = int(window.get("pid") or 0)
        attrs = [f"running", f"pid={pid}", f"window=address:{address}", f"workspace={workspace.get('name', workspace.get('id', ''))}"]
        if window.get("xwayland"):
            attrs.append("xwayland")
        lines.append(f"{name} -- {title} [{', '.join(attrs)}]")
    return "\n".join(lines) if lines else "No running Hyprland apps are visible to hypr-agent-protal."


def executable_basename(value: str) -> str:
    return pathlib.Path(value).name.lower()


def normalize_desktop_id(value: str) -> str:
    return value[:-8] if value.endswith(".desktop") else value


def resolve_launch_executable(name: str) -> str:
    aliases = {
        "chrome": ["google-chrome-stable", "google-chrome", "chromium"],
        "google-chrome": ["google-chrome-stable", "google-chrome", "chromium"],
        "chromium": ["chromium", "chromium-browser", "google-chrome-stable", "google-chrome"],
        "browser": ["chromium", "google-chrome-stable", "google-chrome", "firefox"],
    }
    candidates = aliases.get(name.lower(), [name])
    for candidate in candidates:
        if shutil.which(candidate):
            return candidate
    return name


def launch_parts(args: dict[str, Any]) -> tuple[list[str], str]:
    command = args.get("command")
    app = args.get("app")
    if isinstance(command, str) and command.strip():
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            raise RuntimeError(f"invalid launch command: {exc}") from exc
        match_query = executable_basename(parts[0]) if parts else command
    elif isinstance(app, str) and app.strip():
        app_value = app.strip()
        if app_value.endswith(".desktop"):
            parts = ["gtk-launch", normalize_desktop_id(app_value)]
            match_query = normalize_desktop_id(app_value)
        else:
            try:
                parts = shlex.split(app_value)
            except ValueError as exc:
                raise RuntimeError(f"invalid app command: {exc}") from exc
            if parts:
                parts[0] = resolve_launch_executable(parts[0])
            match_query = executable_basename(parts[0]) if parts else app_value
    else:
        raise RuntimeError("launch_app requires app or command")

    if not parts:
        raise RuntimeError("launch_app resolved to an empty command")

    extra_args = args.get("args")
    if isinstance(extra_args, list):
        parts.extend(str(item) for item in extra_args if isinstance(item, str))

    url = args.get("url")
    new_window = args.get("new_window")
    if not isinstance(new_window, bool):
        new_window = True

    browser_like = launch_is_chromium_like(parts)
    if browser_like:
        if "--force-renderer-accessibility" not in parts:
            parts.append("--force-renderer-accessibility")
        if new_window and not any(part == "--new-window" or part.startswith("--app=") for part in parts):
            parts.append("--new-window")
        if isinstance(url, str) and url:
            parts.append(url)
        elif new_window and not any(not part.startswith("-") for part in parts[1:]):
            parts.append("about:blank")
    elif isinstance(url, str) and url:
        parts.append(url)

    return parts, match_query


def launch_is_chromium_like(parts: list[str]) -> bool:
    if not parts:
        return False
    base = executable_basename(parts[0])
    if base in CHROMIUM_LIKE_EXECUTABLES:
        return True
    return any(token in base for token in ("chrom", "brave", "electron", "discord", "slack"))


def launch_command_string(parts: list[str]) -> str:
    env_parts = ["env", *[f"{key}={value}" for key, value in A11Y_LAUNCH_ENV.items()]]
    return shlex.join([*env_parts, *parts])


def hyprctl_exec(command: str) -> str:
    hyprctl = shutil.which("hyprctl")
    if not hyprctl:
        raise RuntimeError("hyprctl not found")
    env = hyprctl_environment()
    proc = subprocess.run([hyprctl, "dispatch", "exec", command], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"hyprctl dispatch exec failed with exit code {proc.returncode}").strip())
    return proc.stdout.strip()


def window_identities(windows: list[dict[str, Any]]) -> set[str]:
    identities: set[str] = set()
    for window in windows:
        for key in ("address", "stableId"):
            value = str(window.get(key) or "")
            if value:
                identities.add(f"{key}:{value}")
    return identities


def window_matches_launch(window: dict[str, Any], query: str) -> bool:
    normalized = normalize(query)
    if not normalized:
        return False
    fields = [
        normalize(window.get("class")),
        normalize(window.get("initialClass")),
        normalize(window.get("title")),
        normalize(window.get("initialTitle")),
        str(window.get("pid") or ""),
    ]
    desktop = normalized.removesuffix(".desktop")
    return any(normalized == field or desktop == field or normalized in field or desktop in field for field in fields if field)


def wait_for_launch_window(before_ids: set[str], query: str, timeout: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    deadline = time.monotonic() + max(0.0, timeout)
    latest: list[dict[str, Any]] = []
    while True:
        latest = list_hypr_windows()
        new_windows = [window for window in latest if not (window_identities([window]) & before_ids)]
        matching_new = [window for window in new_windows if window_matches_launch(window, query)]
        if matching_new:
            return matching_new[0], matching_new
        if new_windows:
            return new_windows[0], new_windows
        matching_existing = [window for window in latest if window_matches_launch(window, query)]
        if matching_existing:
            return matching_existing[0], []
        if time.monotonic() >= deadline:
            return None, []
        time.sleep(0.2)


def remember_snapshot(query: str, snapshot: dict[str, Any]) -> None:
    window = snapshot.get("window") or {}
    app = snapshot.get("app") or {}
    keys = [
        query,
        window_selector(window) if window.get("address") else "",
        window.get("address"),
        window.get("class"),
        window.get("initialClass"),
        window.get("title"),
        app.get("name"),
        app.get("bundleIdentifier"),
        str(app.get("pid") or ""),
    ]
    for key in keys:
        normalized = normalize(key)
        if normalized:
            SNAPSHOTS[normalized] = snapshot


def current_snapshot(app: str) -> dict[str, Any]:
    snapshot = SNAPSHOTS.get(normalize(app))
    if snapshot is None:
        snapshot = build_app_snapshot(app)
    return snapshot


def lookup_element(snapshot: dict[str, Any], element_index: str) -> dict[str, Any]:
    try:
        index = int(element_index)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'unknown element_index "{element_index}"') from exc
    for element in snapshot.get("elements") or []:
        if int(element.get("index", -1)) == index:
            return dict(element)
    raise RuntimeError(f'unknown element_index "{element_index}"')


def screenshot_point_to_global(snapshot: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    screenshot = snapshot.get("screenshot") or {}
    bounds = screenshot.get("logicalBounds") or {}
    scale = float(screenshot.get("scale") or 1.0)
    if scale <= 0:
        scale = 1.0
    return float(bounds.get("x") or 0.0) + float(x) / scale, float(bounds.get("y") or 0.0) + float(y) / scale


def window_point_to_global(snapshot: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    screenshot = snapshot.get("screenshot") or {}
    bounds = screenshot.get("logicalBounds") or {}
    return float(bounds.get("x") or 0.0) + float(x), float(bounds.get("y") or 0.0) + float(y)


def point_to_global(snapshot: dict[str, Any], x: float, y: float, coordinate_space: Any = "screenshot") -> tuple[float, float]:
    space = normalize(coordinate_space or "screenshot").replace("_", "-")
    if space in {"screenshot", "screenshot-pixel", "screenshot-pixels", "image", "pixel", "pixels"}:
        return screenshot_point_to_global(snapshot, x, y)
    if space in {"window", "window-relative", "window-logical", "logical"}:
        return window_point_to_global(snapshot, x, y)
    if space == "global":
        return x, y
    raise RuntimeError(f"unsupported coordinate_space: {coordinate_space}")


def coordinate_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y = value[0], value[1]
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return float(x), float(y)


def point_from_args(args: dict[str, Any], *, prefix: str = "", coordinate_key: str = "coordinate") -> tuple[float, float] | None:
    pair = coordinate_pair(args.get(coordinate_key))
    if pair is not None:
        return pair

    if prefix:
        x_key = f"{prefix}_x"
        y_key = f"{prefix}_y"
    else:
        x_key = "x"
        y_key = "y"
    x = args.get(x_key)
    y = args.get(y_key)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return float(x), float(y)
    return None


def snapshot_position(snapshot: dict[str, Any], global_x: float, global_y: float) -> dict[str, Any]:
    screenshot = snapshot.get("screenshot") or {}
    bounds = screenshot.get("logicalBounds") or {}
    scale = float(screenshot.get("scale") or 1.0)
    if scale <= 0:
        scale = 1.0
    left = float(bounds.get("x") or 0.0)
    top = float(bounds.get("y") or 0.0)
    width = float(bounds.get("width") or 0.0)
    height = float(bounds.get("height") or 0.0)
    window_x = global_x - left
    window_y = global_y - top
    screenshot_x = window_x * scale
    screenshot_y = window_y * scale
    screenshot_width = float(screenshot.get("width") or width * scale)
    screenshot_height = float(screenshot.get("height") or height * scale)
    return {
        "window": {"x": window_x, "y": window_y, "coordinateSpace": "window"},
        "screenshot": {"x": screenshot_x, "y": screenshot_y, "coordinateSpace": "screenshot"},
        "insideWindow": 0 <= window_x <= width and 0 <= window_y <= height,
        "insideScreenshot": 0 <= screenshot_x <= screenshot_width and 0 <= screenshot_y <= screenshot_height,
    }


def element_center(element: dict[str, Any]) -> tuple[float, float]:
    frame = element.get("frame")
    if not isinstance(frame, dict):
        raise RuntimeError("element has no frame; use x/y coordinates instead")
    return float(frame.get("x") or 0.0) + float(frame.get("width") or 0.0) / 2.0, float(frame.get("y") or 0.0) + float(frame.get("height") or 0.0) / 2.0


def action_point(snapshot: dict[str, Any], args: dict[str, Any], *, default_center: bool = False) -> tuple[float, float]:
    element_index = args.get("element_index")
    if isinstance(element_index, str) and element_index:
        return element_center(lookup_element(snapshot, element_index))

    point = point_from_args(args)
    if point is not None:
        return point

    if default_center:
        screenshot = snapshot.get("screenshot") or {}
        return float(screenshot.get("width") or 0.0) / 2.0, float(screenshot.get("height") or 0.0) / 2.0

    raise RuntimeError("action requires element_index, coordinate, or x/y")


def cursor_state_path() -> pathlib.Path:
    return pathlib.Path(tempfile.gettempdir()) / f"hypr-agent-protal-{os.getuid()}" / "cursor.json"


def screenshot_for_window(window: dict[str, Any]) -> tuple[dict[str, Any], str]:
    info = call_ctl(["screenshot", "--target", window_selector(window), "--base64", "--no-cursor"])
    data = info.pop("pngBase64")
    return info, data


def hyprctl_json(*args: str) -> Any:
    proc = subprocess.run(["hyprctl", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=hyprctl_environment(), check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"hyprctl {' '.join(args)} failed").strip())
    return json.loads(proc.stdout or "null")


def hyprland_cursor_position() -> dict[str, float]:
    data = hyprctl_json("cursorpos", "-j")
    return {"x": float(data.get("x") or 0.0), "y": float(data.get("y") or 0.0)}


def agent_cursor_position() -> dict[str, Any] | None:
    try:
        data = json.loads(cursor_state_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    x = data.get("x")
    y = data.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return {
        "x": float(x),
        "y": float(y),
        "target": str(data.get("target") or ""),
        "action": str(data.get("action") or ""),
        "button": str(data.get("button") or ""),
        "timestamp": float(data.get("timestamp") or 0.0),
    }


def monitor_position(global_x: float, global_y: float) -> dict[str, Any] | None:
    monitors = hyprctl_json("monitors", "-j")
    if not isinstance(monitors, list):
        return None
    for monitor in monitors:
        if not isinstance(monitor, dict):
            continue
        x = float(monitor.get("x") or 0.0)
        y = float(monitor.get("y") or 0.0)
        width = float(monitor.get("width") or 0.0)
        height = float(monitor.get("height") or 0.0)
        if x <= global_x < x + width and y <= global_y < y + height:
            return {
                "name": monitor.get("name"),
                "id": monitor.get("id"),
                "x": global_x - x,
                "y": global_y - y,
                "width": width,
                "height": height,
                "scale": monitor.get("scale"),
                "coordinateSpace": "monitor",
            }
    return None


def atspi_init_error() -> str | None:
    global _ATSPI_INIT_ERROR, _ATSPI
    if _ATSPI_INIT_ERROR is not None:
        return _ATSPI_INIT_ERROR if isinstance(_ATSPI_INIT_ERROR, str) else None
    try:
        import gi  # type: ignore

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore

        Atspi.init()
        _ATSPI = Atspi
        _ATSPI_INIT_ERROR = False
        return None
    except Exception as exc:
        _ATSPI_INIT_ERROR = f"{type(exc).__name__}: {exc}"
        return _ATSPI_INIT_ERROR


def atspi_available() -> bool:
    return atspi_init_error() is None and _ATSPI is not None


def atspi_safe(call: Any, default: Any = None) -> Any:
    try:
        value = call()
        return default if value is None else value
    except Exception:
        return default


def atspi_desktop() -> Any:
    if not atspi_available():
        return None
    return _ATSPI.get_desktop(0)


def atspi_child_count(node: Any) -> int:
    return int(atspi_safe(node.get_child_count, 0) or 0)


def atspi_child_at(node: Any, index: int) -> Any:
    return atspi_safe(lambda: node.get_child_at_index(index))


def atspi_name(node: Any) -> str:
    return str(atspi_safe(node.get_name, "") or "")


def atspi_role(node: Any) -> str:
    return str(atspi_safe(node.get_role_name, "") or "")


def atspi_pid(node: Any) -> int:
    try:
        return int(atspi_safe(node.get_process_id, 0) or 0)
    except Exception:
        return 0


def atspi_state_contains(node: Any, state: Any) -> bool:
    state_set = atspi_safe(node.get_state_set)
    if state_set is None:
        return False
    return bool(atspi_safe(lambda: state_set.contains(state), False))


def atspi_extents(node: Any) -> dict[str, float] | None:
    component = atspi_safe(node.get_component_iface)
    if component is None:
        return None
    rect = atspi_safe(lambda: _ATSPI.Component.get_extents(component, _ATSPI.CoordType.SCREEN))
    if rect is None or rect.width <= 0 or rect.height <= 0 or rect.width > 100000 or rect.height > 100000:
        return None
    return {"x": float(rect.x), "y": float(rect.y), "width": float(rect.width), "height": float(rect.height)}


def atspi_iter_apps() -> list[Any]:
    root = atspi_desktop()
    if root is None:
        return []
    apps = []
    for index in range(atspi_child_count(root)):
        app = atspi_child_at(root, index)
        if app is not None and atspi_name(app):
            apps.append(app)
    return apps


def atspi_app_windows(app: Any) -> list[tuple[int, Any]]:
    windows = []
    for index in range(atspi_child_count(app)):
        child = atspi_child_at(app, index)
        if child is None:
            continue
        role = atspi_role(child).lower()
        bounds = atspi_extents(child)
        if role in {"frame", "window", "dialog", "alert", "filler"} or bounds is not None:
            windows.append((index, child))
    return windows


def atspi_match_window(app: Any, hypr_window: dict[str, Any]) -> tuple[int, Any] | None:
    windows = atspi_app_windows(app)
    if not windows:
        return None
    title = normalize(hypr_window.get("title"))
    for index, node in windows:
        if title and title in normalize(atspi_name(node)):
            return index, node
    for index, node in windows:
        if atspi_state_contains(node, _ATSPI.StateType.ACTIVE):
            return index, node
    for index, node in windows:
        if atspi_state_contains(node, _ATSPI.StateType.SHOWING):
            return index, node
    return windows[0]


def atspi_resolve_window(hypr_window: dict[str, Any]) -> tuple[Any, int, Any] | None:
    if not atspi_available():
        return None
    pid = int(hypr_window.get("pid") or 0)
    title = normalize(hypr_window.get("title"))
    klass = normalize(hypr_window.get("class"))
    for app in atspi_iter_apps():
        if pid and atspi_pid(app) == pid:
            matched = atspi_match_window(app, hypr_window)
            if matched:
                return app, matched[0], matched[1]
    for app in atspi_iter_apps():
        name = normalize(atspi_name(app))
        if (klass and (klass == name or klass in name or name in klass)) or (title and title in name):
            matched = atspi_match_window(app, hypr_window)
            if matched:
                return app, matched[0], matched[1]
    return None


def atspi_action_names(node: Any) -> list[str]:
    names = []
    count = int(atspi_safe(node.get_n_actions, 0) or 0)
    for index in range(count):
        name = str(atspi_safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(atspi_safe(lambda i=index: node.get_action_description(i), "") or "")
        label = name or description
        if label and label not in names:
            names.append(label)
    return names


def atspi_accessible_id(node: Any) -> str:
    return str(atspi_safe(node.get_accessible_id, "") or "")


def atspi_text_value(node: Any) -> str:
    if not bool(atspi_safe(node.is_text, False)):
        return ""
    text_iface = atspi_safe(node.get_text_iface)
    if text_iface is None:
        return ""
    count = int(atspi_safe(lambda: _ATSPI.Text.get_character_count(text_iface), 0) or 0)
    if count <= 0:
        return ""
    value = str(atspi_safe(lambda: _ATSPI.Text.get_text(text_iface, 0, min(count, 500)), "") or "")
    return value + "..." if count > 500 else value


def atspi_numeric_value(node: Any) -> str:
    value_iface = atspi_safe(node.get_value_iface)
    if value_iface is None:
        return ""
    current = atspi_safe(lambda: _ATSPI.Value.get_current_value(value_iface))
    return "" if current is None else str(current)


def atspi_image_frame(node: Any, atspi_window_bounds: dict[str, float] | None, screenshot: dict[str, Any]) -> dict[str, float] | None:
    bounds = atspi_extents(node)
    if bounds is None:
        return None
    if atspi_window_bounds is None:
        return bounds
    rel = {
        "x": bounds["x"] - atspi_window_bounds["x"],
        "y": bounds["y"] - atspi_window_bounds["y"],
        "width": bounds["width"],
        "height": bounds["height"],
    }
    sx = float(screenshot.get("width") or 0) / atspi_window_bounds["width"] if atspi_window_bounds["width"] > 0 else float(screenshot.get("scale") or 1)
    sy = float(screenshot.get("height") or 0) / atspi_window_bounds["height"] if atspi_window_bounds["height"] > 0 else float(screenshot.get("scale") or 1)
    return {"x": rel["x"] * sx, "y": rel["y"] * sy, "width": rel["width"] * sx, "height": rel["height"] * sy}


def atspi_record_for(node: Any, index: int, path: list[int], atspi_window_bounds: dict[str, float] | None, screenshot: dict[str, Any]) -> dict[str, Any]:
    role = atspi_role(node)
    return {
        "index": index,
        "runtimeId": path[:],
        "automationId": atspi_accessible_id(node),
        "name": atspi_name(node),
        "controlType": role,
        "localizedControlType": role,
        "className": str(atspi_safe(node.get_toolkit_name, "") or ""),
        "value": atspi_text_value(node) or atspi_numeric_value(node),
        "nativeWindowHandle": 0,
        "frame": atspi_image_frame(node, atspi_window_bounds, screenshot),
        "actions": atspi_action_names(node),
        "source": "atspi",
    }


def atspi_render_tree(root: Any, root_path: list[int], screenshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], dict[str, float] | None]:
    records: list[dict[str, Any]] = []
    lines: list[str] = []
    atspi_window_bounds = atspi_extents(root)

    def visit(node: Any, depth: int, path: list[int]) -> None:
        if node is None or len(records) >= 500 or depth > 64:
            return
        index = len(records)
        record = atspi_record_for(node, index, path, atspi_window_bounds, screenshot)
        records.append(record)

        role = record["localizedControlType"] or record["controlType"] or "element"
        title = record["name"] or record["automationId"] or ""
        value_segment = ""
        if record["value"] and record["value"] != title:
            safe_value = str(record["value"]).replace("\r", "\\r").replace("\n", "\\n")
            value_segment = " Value: " + safe_value
        actions_segment = " Secondary Actions: " + ", ".join(record["actions"]) if record["actions"] else ""
        frame_segment = ""
        if isinstance(record["frame"], dict):
            frame = record["frame"]
            frame_segment = " Frame: {{x: {0}, y: {1}, width: {2}, height: {3}}}".format(
                round(frame["x"]), round(frame["y"]), round(frame["width"]), round(frame["height"])
            )
        lines.append(("\t" * (depth + 1)) + f"{index} {role} {title}{value_segment}{actions_segment}{frame_segment}".rstrip())

        for child_index in range(atspi_child_count(node)):
            visit(atspi_child_at(node, child_index), depth + 1, path + [child_index])

    visit(root, 0, root_path)
    return records, lines, atspi_window_bounds


def atspi_resolve_path(app: Any, path: list[Any]) -> Any:
    node = app
    for index in path:
        node = atspi_child_at(node, int(index))
        if node is None:
            return None
    return node


def atspi_node_for_element(snapshot: dict[str, Any], element: dict[str, Any]) -> Any:
    window = snapshot.get("window") or {}
    resolved = atspi_resolve_window(window)
    if not resolved:
        return None
    app, _, _ = resolved
    path = element.get("runtimeId")
    if not isinstance(path, list):
        return None
    return atspi_resolve_path(app, path)


def atspi_preferred_action_index(node: Any) -> int | None:
    preferred = {"click", "press", "activate", "default.activate", "invoke", "select", "toggle", "open"}
    count = int(atspi_safe(node.get_n_actions, 0) or 0)
    fallback = None
    for index in range(count):
        name = str(atspi_safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(atspi_safe(lambda i=index: node.get_action_description(i), "") or "")
        lower = (name or description).lower()
        if lower in preferred:
            return index
        if fallback is None and ("activate" in lower or "click" in lower or "press" in lower):
            fallback = index
    return fallback


def atspi_do_action(node: Any, action_name: str | None = None) -> bool:
    if node is None:
        return False
    if action_name is None:
        index = atspi_preferred_action_index(node)
        return bool(index is not None and atspi_safe(lambda: node.do_action(index), False))
    normalized = action_name.lower()
    count = int(atspi_safe(node.get_n_actions, 0) or 0)
    for index in range(count):
        name = str(atspi_safe(lambda i=index: node.get_action_name(i), "") or "")
        description = str(atspi_safe(lambda i=index: node.get_action_description(i), "") or "")
        if normalized in {name.lower(), description.lower()}:
            return bool(atspi_safe(lambda: node.do_action(index), False))
    return False


def atspi_find_first(root: Any, predicate: Any) -> Any:
    if root is None:
        return None
    if predicate(root):
        return root
    for index in range(atspi_child_count(root)):
        found = atspi_find_first(atspi_child_at(root, index), predicate)
        if found is not None:
            return found
    return None


def atspi_insert_text(snapshot: dict[str, Any], text: str) -> bool:
    resolved = atspi_resolve_window(snapshot.get("window") or {})
    if not resolved:
        return False
    _, _, window_node = resolved

    def editable(node: Any) -> bool:
        return bool(atspi_safe(node.is_editable_text, False)) and bool(atspi_safe(node.is_text, False))

    node = atspi_find_first(window_node, editable)
    if node is None:
        return False
    editable_iface = atspi_safe(node.get_editable_text_iface)
    text_iface = atspi_safe(node.get_text_iface)
    if editable_iface is None or text_iface is None:
        return False
    offset = int(atspi_safe(lambda: _ATSPI.Text.get_character_count(text_iface), 0) or 0)
    return bool(atspi_safe(lambda: _ATSPI.EditableText.insert_text(editable_iface, offset, text, len(text)), False))


def atspi_set_element_value(snapshot: dict[str, Any], element: dict[str, Any], value: str) -> bool:
    node = atspi_node_for_element(snapshot, element)
    if node is None:
        return False
    if bool(atspi_safe(node.is_editable_text, False)):
        editable_iface = atspi_safe(node.get_editable_text_iface)
        if editable_iface is not None and bool(atspi_safe(lambda: _ATSPI.EditableText.set_text_contents(editable_iface, value), False)):
            return True
    value_iface = atspi_safe(node.get_value_iface)
    if value_iface is not None:
        try:
            return bool(_ATSPI.Value.set_current_value(value_iface, float(value)))
        except Exception:
            return False
    return False


def atspi_snapshot(window: dict[str, Any], screenshot: dict[str, Any]) -> dict[str, Any]:
    init_error = atspi_init_error()
    if init_error is not None:
        return {"status": "unavailable", "error": init_error, "elements": [], "treeLines": [], "windowBounds": None}
    resolved = atspi_resolve_window(window)
    if not resolved:
        return {"status": "not-found", "error": "No matching AT-SPI app/window for this Hyprland client.", "elements": [], "treeLines": [], "windowBounds": None}
    app, window_index, window_node = resolved
    elements, tree_lines, bounds = atspi_render_tree(window_node, [window_index], screenshot)
    return {
        "status": "ok",
        "appName": atspi_name(app),
        "appPid": atspi_pid(app),
        "windowTitle": atspi_name(window_node),
        "windowBounds": bounds,
        "elements": elements,
        "treeLines": tree_lines,
    }


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


def synthetic_elements(window: dict[str, Any], screenshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    width = float(screenshot.get("width") or window_geometry(window)["width"])
    height = float(screenshot.get("height") or window_geometry(window)["height"])
    title = str(window.get("title") or window.get("class") or "window")
    element = {
        "index": 0,
        "runtimeId": ["hyprland", str(window.get("address") or "")],
        "automationId": str(window.get("address") or ""),
        "name": title,
        "controlType": "window",
        "localizedControlType": "window",
        "className": str(window.get("class") or ""),
        "value": "",
        "nativeWindowHandle": 0,
        "frame": {"x": 0.0, "y": 0.0, "width": width, "height": height},
        "actions": [],
        "source": "hyprland",
    }
    line = "\t0 window {0} Frame: {{x: 0, y: 0, width: {1}, height: {2}}}".format(title, round(width), round(height))
    return [element], [line]


def build_app_snapshot(app_query: str) -> dict[str, Any]:
    window = resolve_hypr_window(app_query)
    screenshot, png_base64 = screenshot_for_window(window)
    atspi = atspi_snapshot(window, screenshot)
    elements = atspi["elements"] if atspi["status"] == "ok" and atspi["elements"] else []
    tree_lines = atspi["treeLines"] if atspi["status"] == "ok" and atspi["treeLines"] else []
    if not elements:
        elements, tree_lines = synthetic_elements(window, screenshot)

    app = {
        "name": str(window.get("class") or window.get("initialClass") or window.get("title") or "unknown"),
        "bundleIdentifier": str(window.get("class") or window.get("initialClass") or ""),
        "pid": int(window.get("pid") or 0),
    }
    snapshot = {
        "app": app,
        "windowTitle": str(window.get("title") or ""),
        "windowBounds": screenshot.get("logicalBounds") or window_geometry(window),
        "target": window_selector(window),
        "window": window,
        "screenshot": screenshot,
        "screenshotPngBase64": png_base64,
        "treeLines": tree_lines,
        "elements": elements,
        "accessibility": {k: v for k, v in atspi.items() if k not in {"elements", "treeLines"}},
    }
    remember_snapshot(app_query, snapshot)
    return snapshot


def render_snapshot_text(snapshot: dict[str, Any]) -> str:
    app = snapshot.get("app") or {}
    window = snapshot.get("window") or {}
    app_ref = app.get("bundleIdentifier") or app.get("name") or "unknown"
    lines = [
        f"App={app_ref} (pid {app.get('pid', 0)})",
        'Window: "{0}", App: {1}.'.format(snapshot.get("windowTitle") or app.get("name") or "", app.get("name") or "unknown"),
        "Hyprland: target={0}, class={1}, workspace={2}, xwayland={3}.".format(
            snapshot.get("target"),
            window.get("class") or "",
            (window.get("workspace") or {}).get("name", (window.get("workspace") or {}).get("id", "")),
            bool(window.get("xwayland")),
        ),
    ]
    lines.extend(str(line) for line in snapshot.get("treeLines") or [])
    accessibility = snapshot.get("accessibility") or {}
    if accessibility.get("status") != "ok":
        lines.extend(["", "Accessibility: {0}. {1}".format(accessibility.get("status", "unavailable"), accessibility.get("error", ""))])
    return "\n".join(lines)


def tool_list_apps(_: dict[str, Any]) -> dict[str, Any]:
    windows = list_hypr_windows()
    return mcp_text(list_apps_text(windows), structured={"windows": windows})


def tool_launch_app(args: dict[str, Any]) -> dict[str, Any]:
    parts, match_query = launch_parts(args)
    reuse_existing = args.get("reuse_existing")
    timeout_value = args.get("timeout", 8)
    timeout = float(timeout_value) if isinstance(timeout_value, (int, float)) else 8.0

    if reuse_existing:
        try:
            existing = resolve_hypr_window(match_query)
            result = {
                "ok": True,
                "reused": True,
                "app": match_query,
                "target": window_selector(existing),
                "window": existing,
                "next": f'Call get_app_state with app="{window_selector(existing)}" or app="{match_query}".',
            }
            return mcp_text(json.dumps(result, ensure_ascii=False), structured=result)
        except Exception:
            pass

    before = list_hypr_windows()
    before_ids = window_identities(before)
    command = launch_command_string(parts)
    output = hyprctl_exec(command)
    window, new_windows = wait_for_launch_window(before_ids, match_query, timeout)
    result: dict[str, Any] = {
        "ok": True,
        "reused": False,
        "app": match_query,
        "command": command,
        "argv": parts,
        "hyprctlOutput": output,
        "accessibility": {
            "environment": A11Y_LAUNCH_ENV,
            "chromiumLikeFlagsApplied": launch_is_chromium_like(parts),
        },
        "newWindows": new_windows,
    }
    if window is not None:
        result["target"] = window_selector(window)
        result["window"] = window
        result["next"] = f'Call get_app_state with app="{window_selector(window)}" or app="{match_query}".'
    else:
        result["warning"] = "No Hyprland window appeared before timeout; use list_apps to inspect current windows."
    return mcp_text(json.dumps(result, ensure_ascii=False), structured=result)


def tool_get_app_state(args: dict[str, Any]) -> dict[str, Any]:
    app = args.get("app")
    if not isinstance(app, str) or not app:
        raise RuntimeError("Missing required argument: app")
    return mcp_snapshot_result(build_app_snapshot(app))


def tool_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    cmd = ["screenshot", "--base64"]
    app = args.get("app")
    target = args.get("target")
    if isinstance(app, str) and app:
        target = window_selector(resolve_hypr_window(app))
    if isinstance(target, str) and target:
        cmd.extend(["--target", target])
    if args.get("show_cursor") is False:
        cmd.append("--no-cursor")
    cursor_source = args.get("cursor_source")
    if isinstance(cursor_source, str) and cursor_source:
        cmd.extend(["--cursor-source", cursor_source])
    info = call_ctl(cmd)
    data = info.pop("pngBase64")
    return {
        "content": [
            {"type": "text", "text": json.dumps(info, ensure_ascii=False)},
            {"type": "image", "mimeType": "image/png", "data": data},
        ],
        "structuredContent": info,
        "isError": False,
    }


def tool_get_cursor_position(args: dict[str, Any]) -> dict[str, Any]:
    source = str(args.get("source") or "auto").lower()
    if source not in {"auto", "hyprland", "agent"}:
        raise RuntimeError("source must be auto, hyprland, or agent")

    hypr = hyprland_cursor_position()
    agent = agent_cursor_position()
    selected = hypr
    selected_source = "hyprland"
    if source == "agent" or (source == "auto" and agent is not None):
        if agent is None:
            raise RuntimeError("no agent cursor position is recorded yet")
        selected = {"x": agent["x"], "y": agent["y"]}
        selected_source = "agent"

    global_x = float(selected["x"])
    global_y = float(selected["y"])
    result: dict[str, Any] = {
        "source": selected_source,
        "monitor": monitor_position(global_x, global_y),
        "agentCursor": agent,
    }

    app = args.get("app")
    if isinstance(app, str) and app:
        snapshot = SNAPSHOTS.get(normalize(app)) or build_app_snapshot(app)
        result["app"] = {
            "name": (snapshot.get("app") or {}).get("name"),
            "target": snapshot.get("target"),
            "windowTitle": snapshot.get("windowTitle"),
        }
        result["position"] = snapshot_position(snapshot, global_x, global_y)
    else:
        result["position"] = result["monitor"]

    if args.get("include_global"):
        result["global"] = {"x": global_x, "y": global_y, "coordinateSpace": "global"}

    return mcp_text(json.dumps(result, ensure_ascii=False), structured=result)


def semantic_click(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    snapshot = current_snapshot(app)
    target = str(snapshot["target"])
    button = str(args.get("mouse_button") or "left")
    click_count = int(args.get("click_count") or 1)

    element_index = args.get("element_index")
    if isinstance(element_index, str) and element_index:
        element = lookup_element(snapshot, element_index)
        if button == "left" and element.get("source") == "atspi":
            node = atspi_node_for_element(snapshot, element)
            if atspi_do_action(node):
                refreshed = build_app_snapshot(app)
                return mcp_snapshot_result(refreshed)
        x, y = element_center(element)
        coordinate_space = "screenshot"
    else:
        point = action_point(snapshot, args)
        x, y = point
        coordinate_space = args.get("coordinate_space") or "screenshot"

    global_x, global_y = point_to_global(snapshot, float(x), float(y), coordinate_space)
    action = "doubleclick" if click_count > 1 and button == "left" else "click"
    for index in range(max(1, click_count)):
        if action == "doubleclick":
            break
        call_ctl(["pointer", "--json", target, str(global_x), str(global_y), "click", button])
        if index + 1 < click_count:
            time.sleep(0.12)
    if action == "doubleclick":
        call_ctl(["pointer", "--json", target, str(global_x), str(global_y), "doubleclick", button])
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_perform_secondary_action(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    element = lookup_element(current_snapshot(app), str(args.get("element_index") or ""))
    action = str(args.get("action") or "")
    if not action:
        raise RuntimeError("Missing required argument: action")
    node = atspi_node_for_element(current_snapshot(app), element)
    if not atspi_do_action(node, action):
        raise RuntimeError(f"{action} is not a valid secondary action for element")
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_scroll(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    snapshot = current_snapshot(app)
    direction = str(args.get("direction") or "down").lower()
    pages = float(args.get("pages") or 1.0)
    if pages <= 0:
        raise RuntimeError("pages must be > 0")
    element_index = args.get("element_index")
    if isinstance(element_index, str) and element_index:
        x, y = element_center(lookup_element(snapshot, element_index))
        coordinate_space = "screenshot"
    else:
        x, y = action_point(snapshot, args, default_center=True)
        coordinate_space = args.get("coordinate_space") or "screenshot"
    global_x, global_y = point_to_global(snapshot, x, y, coordinate_space)
    ticks = max(1.0, pages * 5.0)
    dx = 0.0
    dy = 0.0
    if direction == "up":
        dy = ticks
    elif direction == "down":
        dy = -ticks
    elif direction == "left":
        dx = ticks
    elif direction == "right":
        dx = -ticks
    else:
        raise RuntimeError(f"Invalid scroll direction: {direction}")
    call_ctl(["pointer", "--json", str(snapshot["target"]), str(global_x), str(global_y), "scroll", str(dy), "--dx", str(dx)])
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_drag(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    snapshot = current_snapshot(app)
    start = point_from_args(args, prefix="from", coordinate_key="start_coordinate")
    end = point_from_args(args, prefix="to", coordinate_key="coordinate")
    if start is None or end is None:
        raise RuntimeError("drag requires start_coordinate/coordinate or from_x/from_y/to_x/to_y")
    coordinate_space = args.get("coordinate_space") or "screenshot"
    from_x, from_y = point_to_global(snapshot, start[0], start[1], coordinate_space)
    to_x, to_y = point_to_global(snapshot, end[0], end[1], coordinate_space)
    duration = float(args.get("duration") or 0.2)
    call_ctl(["pointer", "--json", str(snapshot["target"]), str(from_x), str(from_y), "drag", "left", str(to_x), str(to_y), "--duration", str(max(0.0, min(duration, 3.0)))])
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_hover(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    snapshot = current_snapshot(app)
    x, y = action_point(snapshot, args)
    global_x, global_y = point_to_global(snapshot, x, y, args.get("coordinate_space") or "screenshot")
    call_ctl(["pointer", "--json", str(snapshot["target"]), str(global_x), str(global_y), "move", "left"])
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_type_text(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    text = args.get("text")
    if not isinstance(text, str) or not text:
        raise RuntimeError("Missing required argument: text")
    snapshot = current_snapshot(app)
    if atspi_insert_text(snapshot, text):
        return mcp_snapshot_result(build_app_snapshot(app))
    type_text(str(snapshot["target"]), text, {"method": "auto"})
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_press_key(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    key = args.get("key") or args.get("text")
    if not isinstance(key, str) or not key:
        raise RuntimeError("Missing required argument: key")
    snapshot = current_snapshot(app)
    parts = [part for part in key.replace("-", "+").split("+") if part]
    if not parts:
        raise RuntimeError("Missing required argument: key")
    repeat = args.get("repeat", 1)
    if not isinstance(repeat, int) or repeat < 1:
        repeat = 1
    for _ in range(min(repeat, 100)):
        keyboard(str(snapshot["target"]), parts[-1], "+".join(parts[:-1]))
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_set_value(args: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app") or "")
    value = args.get("value")
    if not isinstance(value, str):
        raise RuntimeError("Missing required argument: value")
    snapshot = current_snapshot(app)
    element = lookup_element(snapshot, str(args.get("element_index") or ""))
    if not atspi_set_element_value(snapshot, element, value):
        raise RuntimeError("Cannot set a value for an element that is not settable")
    return mcp_snapshot_result(build_app_snapshot(app))


def semantic_wait(args: dict[str, Any]) -> dict[str, Any]:
    duration = args.get("duration", 1)
    if not isinstance(duration, (int, float)):
        raise RuntimeError("wait requires numeric duration")
    time.sleep(max(0.0, min(float(duration), 30.0)))
    return result_text({"ok": True, "duration": duration})


def alias_click(button: str, click_count: int) -> Any:
    def call(args: dict[str, Any]) -> dict[str, Any]:
        next_args = dict(args)
        next_args["mouse_button"] = button
        next_args["click_count"] = click_count
        return semantic_click(next_args)

    return call


def accessibility_diagnostics(target: str | None = None) -> dict[str, Any]:
    resolved_env = hyprctl_environment()
    diag: dict[str, Any] = {
        "pythonGI": shutil.which("python3") is not None,
        "atspi": {"available": atspi_available(), "error": atspi_init_error()},
        "session": {
            "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", ""),
            "DISPLAY": os.environ.get("DISPLAY", ""),
        },
        "hyprland": {
            "XDG_RUNTIME_DIR": resolved_env.get("XDG_RUNTIME_DIR", ""),
            "HYPRLAND_INSTANCE_SIGNATURE": resolved_env.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
            "WAYLAND_DISPLAY": resolved_env.get("WAYLAND_DISPLAY", ""),
        },
        "recommendations": [
            "For Qt apps launched after configuration, set QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1.",
            "For Chromium/Chrome launched after configuration, add --force-renderer-accessibility.",
            "Keep Hyprland screenshots enabled; AT-SPI is an enhancement, not the capture source.",
        ],
    }
    proc = subprocess.run(["busctl", "--user", "--list"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    diag["org.a11y.Bus"] = proc.returncode == 0 and "org.a11y.Bus" in proc.stdout
    gsettings = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "toolkit-accessibility"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if gsettings.returncode == 0:
        diag["toolkitAccessibility"] = gsettings.stdout.strip()
    if target:
        try:
            window = resolve_hypr_window(target)
            env_path = pathlib.Path("/proc") / str(int(window.get("pid") or 0)) / "environ"
            values = {}
            for entry in env_path.read_bytes().split(b"\0"):
                if b"=" not in entry:
                    continue
                key, value = entry.split(b"=", 1)
                decoded_key = key.decode("utf-8", "ignore")
                if decoded_key in {"QT_LINUX_ACCESSIBILITY_ALWAYS_ON", "NO_AT_BRIDGE", "GTK_MODULES", "GTK_USE_PORTAL", "QT_QPA_PLATFORM", "WAYLAND_DISPLAY", "DISPLAY"}:
                    values[decoded_key] = value.decode("utf-8", "ignore")
            diag["target"] = {"window": window, "environment": values}
        except Exception as exc:
            diag["targetError"] = str(exc)
    return diag


SEMANTIC_TOOLS = {
    "list_apps": tool_list_apps,
    "list_windows": tool_list_apps,
    "launch_app": tool_launch_app,
    "open_app": tool_launch_app,
    "get_app_state": tool_get_app_state,
    "read_app_state": tool_get_app_state,
    "screenshot": tool_screenshot,
    "get_screenshot": tool_screenshot,
    "get_cursor_position": tool_get_cursor_position,
    "click": semantic_click,
    "left_click": alias_click("left", 1),
    "right_click": alias_click("right", 1),
    "middle_click": alias_click("middle", 1),
    "double_click": alias_click("left", 2),
    "triple_click": alias_click("left", 3),
    "perform_secondary_action": semantic_perform_secondary_action,
    "scroll": semantic_scroll,
    "drag": semantic_drag,
    "left_click_drag": semantic_drag,
    "hover": semantic_hover,
    "move_mouse": semantic_hover,
    "type_text": semantic_type_text,
    "type": semantic_type_text,
    "press_key": semantic_press_key,
    "key": semantic_press_key,
    "set_value": semantic_set_value,
    "wait": semantic_wait,
}


def computer(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "")

    if action in {"launch", "launch_app", "open_app"}:
        return tool_launch_app(args)

    app = args.get("app")
    if isinstance(app, str) and app:
        if action == "get_cursor_position":
            return tool_get_cursor_position(args)
        if action == "left_click":
            return alias_click("left", 1)(args)
        if action == "right_click":
            return alias_click("right", 1)(args)
        if action == "middle_click":
            return alias_click("middle", 1)(args)
        if action == "double_click":
            return alias_click("left", 2)(args)
        if action == "triple_click":
            return alias_click("left", 3)(args)
        if action == "hover":
            return semantic_hover(args)
        if action == "left_click_drag":
            return semantic_drag(args)
        if action == "click":
            return semantic_click(args)
        if action == "doubleclick":
            next_args = dict(args)
            next_args["click_count"] = 2
            return semantic_click(next_args)
        if action == "move":
            return semantic_hover(args)
        if action == "scroll":
            next_args = dict(args)
            if "direction" not in next_args and isinstance(args.get("scroll_direction"), str):
                next_args["direction"] = args.get("scroll_direction")
            if "pages" not in next_args and isinstance(args.get("scroll_amount"), (int, float)):
                next_args["pages"] = max(1.0, float(args["scroll_amount"]) / 5.0)
            return semantic_scroll(next_args)
        if action == "drag":
            return semantic_drag(args)
        if action == "type":
            return semantic_type_text(args)
        if action == "key":
            return semantic_press_key(args)

    if action == "get_cursor_position":
        return tool_get_cursor_position(args)
    if action in {"left_click", "right_click", "middle_click", "double_click", "triple_click", "hover"}:
        mapped = dict(args)
        mapped["action"] = "move" if action == "hover" else ("doubleclick" if action in {"double_click", "triple_click"} else "click")
        mapped["button"] = {
            "left_click": "left",
            "right_click": "right",
            "middle_click": "middle",
            "double_click": "left",
            "triple_click": "left",
            "hover": "left",
        }[action]
        args = mapped
        action = str(args["action"])

    if action == "left_click_drag":
        start = coordinate_pair(args.get("start_coordinate"))
        end = coordinate_pair(args.get("coordinate"))
        if start is None or end is None:
            raise RuntimeError("left_click_drag requires start_coordinate and coordinate")
        mapped = dict(args)
        mapped.update({"action": "drag", "x": start[0], "y": start[1], "x2": end[0], "y2": end[1], "button": "left"})
        args = mapped
        action = "drag"

    if action == "screenshot":
        cmd = ["screenshot", "--base64"]
        target = args.get("target")
        app = args.get("app")
        if (not isinstance(target, str) or not target) and isinstance(app, str) and app:
            target = window_selector(resolve_hypr_window(app))
        if isinstance(target, str) and target:
            cmd.extend(["--target", target])
        show_cursor = args.get("show_cursor")
        if show_cursor is False:
            cmd.append("--no-cursor")
        cursor_source = args.get("cursor_source")
        if isinstance(cursor_source, str) and cursor_source:
            cmd.extend(["--cursor-source", cursor_source])
        elif show_cursor is True:
            cmd.extend(["--cursor-source", "auto"])
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
        amount = args.get("scroll_amount", 1)
        if not isinstance(amount, (int, float)):
            amount = 1
        direction = str(args.get("scroll_direction") or "").lower()
        dy = args.get("dy", amount)
        dx = args.get("dx", 0)
        if direction == "up":
            dy, dx = abs(float(amount)), 0
        elif direction == "down":
            dy, dx = -abs(float(amount)), 0
        elif direction == "left":
            dx, dy = abs(float(amount)), 0
        elif direction == "right":
            dx, dy = -abs(float(amount)), 0
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
        info = call_ctl(["pointer", "--json", target, str(x), str(y), "drag", str(button), str(x2), str(y2), "--duration", str(max(0.0, min(float(duration), 3.0)))])
        info.update({"from": {"x": x, "y": y}, "to": {"x": x2, "y": y2}})
        return result_text(info)

    if action == "key":
        target = require_target(args)
        key, modifiers = key_from_args(args)
        x = args.get("x")
        y = args.get("y")
        repeat = args.get("repeat", 1)
        if not isinstance(repeat, int) or repeat < 1:
            repeat = 1
        info = {}
        for _ in range(min(repeat, 100)):
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

    if action == "doctor":
        target = args.get("target")
        return result_text(accessibility_diagnostics(target if isinstance(target, str) and target else None))

    raise RuntimeError(f"unsupported action: {action}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    req_id = message.get("id")

    if method == "initialize":
        return response(
            req_id,
            {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "hypr-agent-protal", "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )

    if method == "tools/list":
        return response(req_id, {"tools": tool_definitions()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name != "computer" and name not in SEMANTIC_TOOLS:
            return response(req_id, {"content": [{"type": "text", "text": "unknown tool"}], "isError": True})
        try:
            if name == "computer":
                return response(req_id, computer(args))
            return response(req_id, SEMANTIC_TOOLS[str(name)](args))
        except Exception as exc:
            return response(req_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})

    if method and method.startswith("notifications/"):
        if method in {"notifications/turn-ended", "notifications/cancelled"}:
            SNAPSHOTS.clear()
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
