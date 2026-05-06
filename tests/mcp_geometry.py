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


class DummyActionNode:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    def get_n_actions(self) -> int:
        return len(self.actions)

    def get_action_name(self, index: int) -> str:
        return self.actions[index]

    def get_action_description(self, index: int) -> str:
        return ""


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

    assert mcp.atspi_preferred_action_index(DummyActionNode(["showContextMenu", "jump"])) == 1
    assert mcp.element_has_primary_atspi_action({"source": "atspi", "actions": ["jump"]}) is True
    assert mcp.element_has_primary_atspi_action({"source": "synthetic", "actions": ["jump"]}) is False
    assert mcp.scroll_action_for_direction("down") == "scrollDown"
    assert mcp.element_supports_scroll_direction({"source": "atspi", "actions": ["scrollDown"]}, "down") is True

    scroll_snapshot = {
        "screenshot": {"width": 2862, "height": 1686},
        "elements": [
            {"source": "atspi", "controlType": "document web", "actions": ["scrollDown"], "frame": {"x": 15.0, "y": 363.0, "width": 5664.0, "height": 2964.0}},
            {"source": "atspi", "controlType": "section", "actions": ["scrollDown"], "frame": {"x": 15.0, "y": 363.0, "width": 200.0, "height": 200.0}},
        ],
    }
    scroll_element = mcp.best_scroll_element(scroll_snapshot, "down")
    assert scroll_element is scroll_snapshot["elements"][0]
    center = mcp.visible_element_center(scroll_snapshot, scroll_element)
    near(center[0], 1438.5)
    near(center[1], 1024.5)

    hints_snapshot = {
        "screenshot": {"width": 1000, "height": 600},
    }
    hints = mcp.ui_hints_for_elements(
        hints_snapshot,
        [
            {"index": 1, "source": "atspi", "controlType": "menu", "name": "Insert", "frame": {"x": 100.0, "y": 0.0, "width": 80.0, "height": 36.0}},
            {"index": 2, "source": "atspi", "controlType": "page tab", "name": "Insert", "frame": {"x": 200.0, "y": 0.0, "width": 90.0, "height": 36.0}},
            {"index": 3, "source": "atspi", "controlType": "tool bar", "name": "Formatting", "frame": {"x": 0.0, "y": 40.0, "width": 1000.0, "height": 80.0}},
            {"index": 4, "source": "atspi", "controlType": "table", "name": "Sheet", "frame": {"x": 0.0, "y": 120.0, "width": 1000.0, "height": 400.0}},
        ],
    )
    assert hints["visibleMenus"][0]["index"] == 1
    assert hints["visibleTabs"][0]["index"] == 2
    assert all(item["index"] != 4 for item in hints["visibleTabs"])
    assert hints["visibleToolbars"][0]["index"] == 3
    assert "can be a classic menu" in hints["notes"][0]
    action_hints = mcp.ui_hints_for_elements(
        hints_snapshot,
        [
            {"index": 5, "source": "atspi", "controlType": "push button", "name": "Chart", "frame": {"x": 100.0, "y": 40.0, "width": 80.0, "height": 32.0}},
        ],
    )
    assert action_hints["visibleActions"][0]["index"] == 5
    menu_snapshot = {
        "app": {"name": "demo", "bundleIdentifier": "demo", "pid": 42},
        "window": {"class": "demo", "workspace": {"name": "1"}, "xwayland": False},
        "target": "address:0x1",
        "windowTitle": "Demo",
        "treeLines": [],
        "globalMenu": {
            "providers": [{"provider": "dbusmenu", "service": ":1.2", "objectPath": "/com/canonical/dbusmenu", "itemCount": 2}],
            "items": [
                {"menuIndex": "menu:0", "provider": "dbusmenu", "label": "File", "depth": 0, "enabled": True},
                {"menuIndex": "menu:1", "provider": "dbusmenu", "label": "Open", "depth": 1, "enabled": True},
            ],
        },
        "uiHints": {},
        "accessibility": {"status": "ok"},
    }
    rendered = mcp.render_snapshot_text(menu_snapshot)
    assert "Global menu models:" in rendered
    assert "menu:1 Open" in rendered
    assert mcp.find_global_menu_item(menu_snapshot, "menu:1")["label"] == "Open"
    gmenu_action = {"objectPath": "/org/example/window/1/menus/menubar", "action": "win.insert-chart"}
    assert mcp.gtk_action_candidates_for_menu(gmenu_action)[0] == ("/org/example/window/1", "insert-chart")
    assert mcp.text_is_bulk_paste_candidate("A\tB\n1\t2") is True
    assert mcp.text_is_bulk_paste_candidate("short") is False
    assert mcp.snapshot_has_grid_target({"elements": [{"controlType": "table cell"}]}) is True

    original_list_hypr_windows = mcp.list_hypr_windows
    try:
        existing_window = {"address": "0xold", "class": "chromium", "title": "Old Chromium"}
        mcp.list_hypr_windows = lambda: [existing_window]
        reused, reused_new = mcp.wait_for_launch_window({"address:0xold"}, "chromium", 0.0, allow_existing_fallback=True)
        assert reused is existing_window
        assert reused_new == []
        strict, strict_new = mcp.wait_for_launch_window({"address:0xold"}, "chromium", 0.0, allow_existing_fallback=False)
        assert strict is None
        assert strict_new == []

        unrelated_new = {"address": "0xnew", "class": "splash", "title": "Starting"}
        mcp.list_hypr_windows = lambda: [existing_window, unrelated_new]
        selected, selected_new = mcp.wait_for_launch_window({"address:0xold"}, "chromium", 0.0, allow_existing_fallback=False)
        assert selected is unrelated_new
        assert selected_new == [unrelated_new]
    finally:
        mcp.list_hypr_windows = original_list_hypr_windows
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
