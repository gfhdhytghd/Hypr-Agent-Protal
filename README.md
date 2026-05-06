# hypr-agent-protal

hypr-agent-protal is an experimental Hyprland plugin plus MCP bridge for background agent control.

`hyprcum` was the old pre-rename prototype. New Codex sessions should use only
the `hypr-agent-protal` MCP server/namespace and Hyprland dispatchers. If both
`mcp__hypr_agent_protal__` and `mcp__hyprcum__` are visible, the Codex
configuration is stale; disable or remove the old `hyprcum` plugin/server.

It exposes four compositor dispatchers:

```ini
hyprctl dispatch hypr-agent-protal:screenshot /tmp/hypr-agent-protal-session.json
hyprctl dispatch hypr-agent-protal:screenshot '/tmp/hypr-agent-protal-session.json,address:0x1234'
hyprctl dispatch hypr-agent-protal:pointer 'address:0x1234,930,520,click,left'
hyprctl dispatch hypr-agent-protal:pointer 'address:0x1234,930,520,drag,left,1180,760,0.2'
hyprctl dispatch hypr-agent-protal:keyboard 'address:0x1234,tap,v,ctrl'
hyprctl dispatch hypr-agent-protal:session 'begin,address:0x1234'
```

The screenshot dispatcher renders active monitor workspaces into RGBA artifacts from inside Hyprland, then writes a JSON session file. When a window selector is supplied, it renders that window directly into an offscreen framebuffer, so the artifact is not occluded by other windows. The pointer dispatcher resolves the target with `g_pCompositor->getWindowByRegex()`, focuses the target surface only for the injected pointer events, sends motion/button/frame events through `g_pSeatManager`, then restores the previous pointer focus. Successful background pointer actions also render a non-interactive Codex-style cursor overlay with the target window's render pass, so it appears on the controlled app when that app is visible instead of being drawn as a global topmost layer.

For XWayland windows, the dispatcher sends to the `wlSurface()` resource and scales surface-local coordinates by `m_X11SurfaceScaledBy`. If the requested global coordinate lands on a same-process XWayland helper window, such as a search popup, pointer and keyboard dispatch are automatically routed to that related window. For native Wayland windows, it uses `vectorWindowToSurface(globalPos, window, localPos)` so subsurfaces receive local coordinates.

The keyboard dispatcher temporarily focuses the target surface, sends key events and modifier state through `g_pSeatManager`, then restores the previous keyboard focus. The MCP layer uses that for shortcuts and text/file/image paste flows.

For apps that spawn visible XWayland helper windows during background control, `hypr-agent-protal:session begin,<target>` records the target window workspace. New same-process related windows opened during the session are moved back to that workspace instead of appearing on the agent's current workspace.

## Build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

Install or load `build/libhypr-agent-protal.so` as a Hyprland plugin. With hyprpm:

```sh
hyprpm add .
hyprpm enable hypr-agent-protal
hyprpm reload
```

## MCP

The repository includes a Codex plugin manifest and a stdio MCP server:

```sh
python3 mcp/hypr-agent-protal-mcp.py
```

Recommended agent workflow:

1. Call `list_apps` first and select the target app/window.
2. If the requested app is not in `list_apps`, call `launch_app` or `open_app`.
   Do not guess a shell command outside the MCP tool. The launcher uses
   `hyprctl dispatch exec`, waits for the Hyprland window, and returns a
   `target` selector plus the next `get_app_state` hint.
3. Call `get_app_state` for semantic state, or `screenshot` with `app` for an
   image-only refresh.
4. Prefer `element_index` from `get_app_state`. When coordinates are needed,
   use `coordinate_space=screenshot` with screenshot pixels, or
   `coordinate_space=window` with target-window-relative logical coordinates.
5. Use `computer` with `target` and global `x/y` only as a low-level fallback.

Apps launched through `launch_app`/`open_app` automatically get accessibility
environment variables:

```sh
NO_AT_BRIDGE=0
QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
GTK_MODULES=gail:atk-bridge
```

Chromium/Chrome/Electron-like launches also get
`--force-renderer-accessibility`; browser launches with `new_window=true` add
`--new-window` and open `about:blank` when no URL is supplied. To open a browser
directly, use for example:

```json
{"app": "chromium", "url": "https://google.com", "new_window": true}
```

Avoid the obsolete `hyprcum` MCP server and namespace. Its tool schema lacks the
new app-state, screenshot-relative coordinates, related-window session handling,
cursor-position support, and Codex compatibility aliases.

The MCP server exposes the compatibility tool `computer` plus Codex-style app-state tools:

- `list_apps`: lists running Hyprland windows with stable selectors, classes, titles, pid, workspace, geometry, and XWayland status.
- `launch_app` / `open_app`: starts apps through Hyprland, applies accessibility environment/flags, waits for a new window, and returns its selector.
- `get_app_state`: captures an unoccluded screenshot for a selected app/window and returns a semantic tree. AT-SPI nodes are included when the target exposes accessibility; otherwise the result still includes screenshot metadata and synthetic window elements for coordinate fallback.
- `get_cursor_position`: returns the current agent or compositor cursor in monitor-relative coordinates, and in screenshot/window-relative coordinates when `app` is supplied.
- `click`, `scroll`, `drag`, `type_text`, `press_key`, `set_value`, `perform_secondary_action`: operate on the last app-state snapshot by `element_index` where possible, and fall back to screenshot/window-relative coordinates plus the native background input dispatchers.
- Compatibility aliases: `read_app_state`, `list_windows`, `open_app`, `screenshot`, `get_screenshot`, `left_click`, `right_click`, `middle_click`, `double_click`, `triple_click`, `hover`, `move_mouse`, `left_click_drag`, `type`, `key`, and `wait`.

The app-state coordinate contract hides Hyprland global logical coordinates from semantic tools. Pass `coordinate_space=screenshot` for screenshot pixels from `get_app_state`, or `coordinate_space=window` for logical coordinates relative to the captured target window. `coordinate: [x, y]` is accepted by click/hover/scroll aliases; `start_coordinate` plus `coordinate` is accepted by drag aliases. The MCP bridge converts these values to the compositor coordinates internally before dispatch.

### AT-SPI App State

Linux does not expose a system-wide accessibility model as consistently as macOS Accessibility. `get_app_state` therefore treats AT-SPI as a semantic enhancement on top of compositor screenshots, not as the only source of truth.

Expected coverage:

- GTK/GNOME apps usually expose the best trees once `org.gnome.desktop.interface toolkit-accessibility` is enabled.
- Qt/KDE apps can expose useful trees, but they normally need to be launched with `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`.
- Firefox and LibreOffice generally expose meaningful document and control trees.
- Chromium, Chrome, Electron, and VS Code often need `--force-renderer-accessibility`; without it they may only expose a top-level frame or nothing useful.
- XWayland does not by itself prevent AT-SPI. The deciding factor is whether the app toolkit publishes an AT-SPI tree.
- Custom-rendered apps, games, SDL/OpenGL surfaces, Flutter apps, and many proprietary chat clients often expose little or no semantic state. For those, use the screenshot image plus coordinate fallback.

Recommended session setup for better app-state trees:

```sh
gsettings set org.gnome.desktop.interface toolkit-accessibility true
systemctl --user start at-spi-dbus-bus.service
systemctl --user set-environment QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
dbus-update-activation-environment --systemd QT_LINUX_ACCESSIBILITY_ALWAYS_ON
```

Persist `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` in the compositor environment before launching Qt apps, and add `--force-renderer-accessibility` to Chromium/Electron app flags where available. Apps usually need to be restarted after these settings change.

The compatibility `computer` tool still exposes these lower-level actions:

- `screenshot`: captures compositor screenshots and returns PNG image content plus metadata. Pass `target` for unoccluded target-window capture. Screenshot cursor drawing is a debug option and is off by default; use `show_cursor=true` or `cursor_source` values `auto`, `agent`, `hyprland`, `none` to draw it into the returned PNG.
- `windows`: lists Hyprland clients with addresses, classes, titles, geometry, and workspace data. Pass `related_to` to return the selected client plus same-process related windows such as XWayland popups.
- `move`, `click`, `doubleclick`, `press`, `release`: sends pointer input to a target window selector such as `address:0x1234`.
- `scroll`: sends wheel axis events to a target window.
- `drag`: presses, moves, and releases on the target window through the native pointer dispatcher.
- `key`: sends a shortcut such as `ctrl+v`, `enter`, or `escape` to a target window. It also accepts raw evdev keycodes through `keycode` for ydotool-style fallback.
- `type`: sends text to the target input. Use `method` values `auto`, `keys`, or `paste`; by default it types short ASCII text as key events and uses clipboard paste for Unicode or longer text.
- `paste_text`, `paste_file`, `paste_image`: writes clipboard data and sends a background paste shortcut to the target window.
- Text paste actions prefer same-process XWayland popup windows, such as WeChat search results, and restore the previous text clipboard after paste when possible.
- `copy_text`: writes text to the clipboard without sending input.
- `session`: begins, syncs, or ends a related-window workspace guard session. Use `session_action` values `begin`, `sync`, or `end`.
- `wait`: sleeps briefly between UI actions.
- `doctor`: reports AT-SPI/session diagnostics and target accessibility environment hints.
- `launch`, `launch_app`, `open_app`: launches an app from the compatibility `computer` tool using the same accessibility environment and Chromium/Electron flags as the direct `launch_app` tool.
- Compatibility action aliases inside `computer`: `left_click`, `right_click`, `middle_click`, `double_click`, `triple_click`, `hover`, `left_click_drag`, and `get_cursor_position`.

The command-line bridge is also usable directly:

```sh
scripts/hypr-agent-protalctl screenshot --base64
scripts/hypr-agent-protalctl screenshot --target 'address:0x1234' --base64
scripts/hypr-agent-protalctl screenshot --cursor-source agent --base64
scripts/hypr-agent-protalctl windows
scripts/hypr-agent-protalctl windows --related-to 'address:0x1234'
scripts/hypr-agent-protalctl pointer 'address:0x1234' 930 520 click left
scripts/hypr-agent-protalctl pointer 'address:0x1234' 930 520 scroll -3
scripts/hypr-agent-protalctl pointer 'address:0x1234' 930 520 drag left 1180 760 --duration 0.2
scripts/hypr-agent-protalctl keyboard 'address:0x1234' tap v ctrl
scripts/hypr-agent-protalctl keyboard 'address:0x1234' tap 28
scripts/hypr-agent-protalctl session begin 'address:0x1234'
scripts/hypr-agent-protalctl session end 'address:0x1234'
```

## Known Issues

- 2026-05-05: Native Wayland Chrome/Discord accepts background pointer focus and
  individual key events, but MCP paste actions that set the clipboard and send
  `ctrl+v` did not paste into the Discord composer. Use `type` or explicit key
  events as a temporary fallback until modifier/clipboard paste delivery is
  fixed.

## Config

```ini
plugin {
  hypr-agent-protal {
    allow_screenshot = 1
    allow_pointer = 1
    allow_keyboard = 1
    allow_session = 1
    show_indicator = 1
    indicator_timeout_ms = 30000
    # cursor_texture_path = ~/.config/hypr-agent-protal/codex-cursor-252.abgr
  }
}
```

The visible cursor uses `~/.config/hypr-agent-protal/codex-cursor-252.abgr` when present, and otherwise falls back to a procedural texture. Install an extracted Codex Computer Use cursor PNG into that local raw format with:

```sh
scripts/install-codex-cursor-asset
```
