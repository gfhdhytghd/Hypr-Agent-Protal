#!/usr/bin/env python3
import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP = ROOT / "mcp" / "hypr-agent-protal-mcp.py"


def load_mcp():
    spec = importlib.util.spec_from_file_location("hypr_agent_protal_mcp", MCP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def near(actual: float, expected: float, epsilon: float = 0.001) -> None:
    assert abs(actual - expected) <= epsilon, f"{actual} != {expected}"


def main() -> int:
    mcp = load_mcp()
    window = {"at": [1772, 2108], "size": [1416, 828]}
    screenshot = {
        "width": 2862,
        "height": 1686,
        "scale": 2.0,
        "logicalBounds": {"x": 1764.5, "y": 2100.5, "width": 1431.0, "height": 843.0},
    }
    root_local = {"x": 0.0, "y": 0.0, "width": 1416.0, "height": 828.0}
    file_menu_local = {"x": 0.0, "y": 0.0, "width": 57.0, "height": 22.0}

    frame = mcp.atspi_bounds_to_screenshot_frame(file_menu_local, root_local, screenshot, window)
    near(frame["x"], 15.0)
    near(frame["y"], 15.0)
    near(frame["width"], 114.0)
    near(frame["height"], 44.0)

    root_frame = mcp.atspi_bounds_to_screenshot_frame(root_local, root_local, screenshot, window)
    near(root_frame["x"], 15.0)
    near(root_frame["y"], 15.0)
    near(root_frame["width"], 2832.0)
    near(root_frame["height"], 1656.0)

    root_global = {"x": 1772.0, "y": 2108.0, "width": 1416.0, "height": 828.0}
    file_menu_global = {"x": 1772.0, "y": 2108.0, "width": 57.0, "height": 22.0}
    global_frame = mcp.atspi_bounds_to_screenshot_frame(file_menu_global, root_global, screenshot, window)
    assert global_frame == frame

    snapshot = {"window": window, "screenshot": screenshot}
    near(mcp.window_point_to_global(snapshot, 0, 0)[0], 1772.0)
    near(mcp.window_point_to_global(snapshot, 0, 0)[1], 2108.0)
    near(mcp.screenshot_point_to_global(snapshot, 15, 15)[0], 1772.0)
    near(mcp.screenshot_point_to_global(snapshot, 15, 15)[1], 2108.0)
    pos = mcp.snapshot_position(snapshot, 1772.0, 2108.0)
    near(pos["window"]["x"], 0.0)
    near(pos["window"]["y"], 0.0)
    near(pos["screenshot"]["x"], 15.0)
    near(pos["screenshot"]["y"], 15.0)
    shadow_pos = mcp.snapshot_position(snapshot, 3190.0, 2108.0)
    assert shadow_pos["insideScreenshot"] is True
    assert shadow_pos["insideWindow"] is False

    original_related_windows_for = mcp.related_windows_for
    try:
        mcp.related_windows_for = lambda target: [
            {"address": "0x1", "hyprAgentProtalRelation": "self", "class": "libreoffice-calc", "mapped": True, "hidden": False},
            {
                "address": "0x2",
                "hyprAgentProtalRelation": "related",
                "hyprAgentProtalWindowKind": "related",
                "class": "libreoffice-calc",
                "mapped": True,
                "hidden": False,
                "floating": False,
            },
            {
                "address": "0x3",
                "hyprAgentProtalRelation": "related",
                "hyprAgentProtalWindowKind": "related",
                "class": "soffice",
                "mapped": True,
                "hidden": False,
                "floating": True,
            },
        ]
        related = mcp.related_popups_for("address:0x1")
        assert [item["address"] for item in related] == ["0x3"]
        selected, meta = mcp.prefer_related_target("address:0x1")
        assert selected == "address:0x3"
        assert meta and meta["address"] == "0x3"
    finally:
        mcp.related_windows_for = original_related_windows_for
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
