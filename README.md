# hypr-agent-protal

hypr-agent-protal is an experimental Hyprland plugin plus MCP bridge for background agent control.

`hyprcum` was the old pre-rename prototype. New Codex sessions should use only
the `hypr-agent-protal` MCP server/namespace and Hyprland dispatchers. If both
`mcp__hypr_agent_protal__` and `mcp__hyprcum__` are visible, the Codex
configuration is stale; disable or remove the old `hyprcum` plugin/server.

It exposes five compositor dispatchers:

```ini
hyprctl dispatch hypr-agent-protal:screenshot /tmp/hypr-agent-protal-session.json
hyprctl dispatch hypr-agent-protal:screenshot '/tmp/hypr-agent-protal-session.json,address:0x1234'
hyprctl dispatch hypr-agent-protal:pointer 'address:0x1234,930,520,click,left'
hyprctl dispatch hypr-agent-protal:pointer 'address:0x1234,930,520,drag,left,1180,760,0.2'
hyprctl dispatch hypr-agent-protal:indicator 'address:0x1234,930,520,type'
hyprctl dispatch hypr-agent-protal:keyboard 'address:0x1234,tap,v,ctrl'
hyprctl dispatch hypr-agent-protal:session 'begin,address:0x1234'
```

The screenshot dispatcher renders active monitor workspaces into RGBA artifacts from inside Hyprland, then writes a JSON session file. When a window selector is supplied, it renders that window directly into an offscreen framebuffer, so the artifact is not occluded by other windows. The pointer dispatcher resolves the target with `g_pCompositor->getWindowByRegex()`, focuses the target surface only for the injected pointer events, sends motion/button/frame events through `g_pSeatManager`, then restores the previous pointer focus. Successful background pointer actions also render a non-interactive Codex-style cursor overlay with the target window's render pass, so it appears on the controlled app when that app is visible instead of being drawn as a global topmost layer.

For XWayland windows, the dispatcher sends to the `wlSurface()` resource and scales surface-local coordinates by `m_X11SurfaceScaledBy`. If the requested global coordinate lands on a same-process XWayland helper window, such as a search popup, pointer and keyboard dispatch are automatically routed to that related window. For native Wayland windows, it uses `vectorWindowToSurface(globalPos, window, localPos)` so subsurfaces receive local coordinates.

The keyboard dispatcher temporarily focuses the target surface, sends key events and modifier state through `g_pSeatManager`, then restores the previous keyboard focus. The MCP layer uses that for shortcuts and text/file/image paste flows.

For apps that spawn visible helper windows or dialogs during background control, `hypr-agent-protal:session begin,<target>` records the target window workspace. New same-process related windows opened during the session are moved back to that workspace instead of appearing on the agent's current workspace. Paste actions begin and sync this session automatically; if a paste opens a related dialog, the MCP result and the next `get_app_state` output include the dialog's `address:0x...` target so the agent can operate that dialog before returning to the root window.

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

1. If the user explicitly asks for `hypr-agent-protal`, do not use Browser MCP
   or the old `hyprcum` namespace.
2. Unless the user explicitly asks to open, launch, create, or use a new
   app/window/instance, call `list_apps` first and select an existing matching
   target.
3. If the user asks to open or launch an app, call `launch_app` or `open_app`.
   These tools reuse existing matching windows by default. Set
   `reuse_existing=false` or `new_window=true` only when the user explicitly
   asks for a new instance/window.
4. If the requested app is not in `list_apps`, call `launch_app` or `open_app`.
   Do not guess a shell command outside the MCP tool. The launcher uses
   `hyprctl dispatch exec`, waits for the Hyprland window, and returns a
   `target` selector plus the next `get_app_state` hint.
5. Call `get_app_state` for semantic state, or `screenshot` with `app` for an
   image-only refresh.
6. If `get_app_state` reports `ACTIVE RELATED POPUP DETECTED`, operate the
   shown `target=address:0x...` popup/dialog before continuing with the root
   window. The popup screenshot is attached before the root-window screenshot.
7. Prefer `element_index` from `get_app_state`. When coordinates are needed,
   use `coordinate_space=screenshot` with screenshot pixels, or
   `coordinate_space=window` with target-window-relative logical coordinates.
8. Use `paste_text` for multiline, tabular, CSV/TSV, Unicode-heavy, or long
   text. Do not enter datasets with repeated `type_text`/`key` calls unless
   paste is unavailable. For grid-like targets, bulk paste first exits cell
   edit mode so TSV/CSV expands into cells instead of becoming one cell's text.
9. Read `uiHints` before acting on menus, tabs, or toolbars. `controlType=menu`
   is a toolkit role and may mean a classic menu, command label, or
   ribbon/notebookbar page selector. Verify the screenshot and refreshed app
   state instead of assuming the visual meaning.
10. If `get_app_state` exposes `globalMenu` actions, use `activate_menu_item`
   with the returned `menu_index` for app-menu commands. If no global menu item
   is exposed, use visible elements or screenshot/window-relative coordinates.
11. If using the compatibility `computer` tool, pass `app` when possible. When
   only `target` is available, `coordinate_space=screenshot` and
   `coordinate_space=window` still use target-relative coordinates; use
   `coordinate_space=global` only for deliberate low-level fallback.

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
{"app": "chromium", "url": "https://example.com", "reuse_existing": false, "new_window": true}
```

For browser or app-control tasks, reuse an existing matching window unless the
user asked for a new one. The intended sequence is `list_apps` or `launch_app`,
`get_app_state`, then element-index actions where possible. Refresh
`get_app_state` after navigation or major UI changes, and use screenshot/window
coordinates only when the accessibility tree is missing or ambiguous.

The visible agent cursor is a compositor-side indicator, not a side effect of
moving the real pointer. Pointer actions update it through
`hypr-agent-protal:pointer`; semantic AT-SPI, keyboard, and text actions update
the same indicator through `hypr-agent-protal:indicator` before acting, so users
can see which app/region the agent is controlling regardless of the input
backend.

Avoid the obsolete `hyprcum` MCP server and namespace. Its tool schema lacks the
new app-state, screenshot-relative coordinates, related-window session handling,
cursor-position support, and Codex compatibility aliases.

The MCP server exposes the compatibility tool `computer` plus Codex-style app-state tools:

- `list_apps`: lists running Hyprland windows with stable selectors, classes, titles, pid, workspace, geometry, and XWayland status.
- `launch_app` / `open_app`: starts apps through Hyprland, applies accessibility environment/flags, waits for a new window, and returns its selector.
- `get_app_state`: captures an unoccluded screenshot for a selected app/window and returns a semantic tree plus `uiHints` for menus, tabs, and toolbars. AT-SPI nodes are included when the target exposes accessibility; otherwise the result still includes screenshot metadata and synthetic window elements for coordinate fallback. AT-SPI frames are normalized to screenshot pixels, including target-window captures that contain compositor shadow/border margins. When the target process exposes DBusMenu or GMenu app-menu models, the result also includes `globalMenu` providers and `menu_index` actions.
- Active related popups/dialogs: when a same-process popup or floating dialog is open for the target, `get_app_state` adds an `ACTIVE RELATED POPUP DETECTED` notice, `activeRelatedTarget`, and an extra popup screenshot before the root-window screenshot. Agents should switch to that popup target first.
- `get_cursor_position`: returns the current agent or compositor cursor in monitor-relative coordinates, and in screenshot/window-relative coordinates when `app` is supplied.
- `click`, `scroll`, `drag`, `type_text`, `paste_text`, `press_key`, `set_value`, `perform_secondary_action`, `activate_menu_item`: operate on the last app-state snapshot by `element_index` or `menu_index` where possible, and fall back to screenshot/window-relative coordinates plus the native background input dispatchers. Use `paste_text` for bulk text and datasets; on grid/table targets it exits cell edit mode before pasting so tabular text can expand into cells. `type_text` is for short literal typing and accepts `method=auto`, `paste`, `keys`, or explicit `atspi`.
- Compatibility aliases: `read_app_state`, `list_windows`, `open_app`, `screenshot`, `get_screenshot`, `left_click`, `right_click`, `middle_click`, `double_click`, `triple_click`, `hover`, `move_mouse`, `left_click_drag`, `type`, `key`, and `wait`.

The app-state coordinate contract hides Hyprland global logical coordinates from semantic tools. Pass `coordinate_space=screenshot` for screenshot pixels from `get_app_state`, or `coordinate_space=window` for logical coordinates relative to the captured target window. `coordinate: [x, y]` is accepted by click/hover/scroll aliases; `start_coordinate` plus `coordinate` is accepted by drag aliases. The MCP bridge converts these values to the compositor coordinates internally before dispatch. Compatibility calls that provide `target` instead of `app` use the same target-relative conversion unless `coordinate_space=global` is explicit.

### AT-SPI App State

Linux does not expose a system-wide accessibility model as consistently as macOS Accessibility. `get_app_state` therefore treats AT-SPI as a semantic enhancement on top of compositor screenshots, not as the only source of truth.

The returned tree is limited to the current screen state. Hidden menu subtrees are filtered out, and huge table controls such as spreadsheets are sampled through the AT-SPI table interface so visible cells are returned without walking millions of off-screen cells. If traversal hits a time or record budget, `accessibility.treeTruncated` reports it and coordinate fallback remains available.

AT-SPI role names are toolkit reports, not the user's visual intent. Some apps
draw ribbon or notebook-style page selectors while exposing top labels as
`menu` roles. In that case agents should not treat a same-named `menu` as proof
that a tab/page is active; they should use `uiHints`, the screenshot, and
window-relative coordinates to click the visible tab label, then refresh
`get_app_state` before selecting controls revealed by that page.

### Global App Menus

Some Linux apps expose semantic app-menu models outside AT-SPI. `get_app_state`
best-effort loads KDE's `appmenu` kded module and starts
`plasma-gmenudbusmenuproxy.service`, then scans D-Bus services owned by the
target window PID for:

- DBusMenu providers such as `/com/canonical/dbusmenu`.
- GMenu providers such as `org.gtk.Menus` paths ending in `/menus/menubar`.

Discovered entries are returned as `globalMenu.items` and rendered as "Global
menu actions" with stable `menu_index` values for the current snapshot. Use
`activate_menu_item` with that `menu_index` to trigger DBusMenu/GMenu commands
without relying on visual menu popups or AT-SPI role names.

This is an opportunistic provider. Some apps expose a menu service with no
items, expose only media/status menus, or do not publish a menu model for the
current window. In those cases `globalMenu.status` is `unavailable` or the
provider has `itemCount=0`; agents should continue with the visible app state
and screenshot/window-coordinate controls.

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
- `windows`: lists Hyprland clients with addresses, classes, titles, geometry, and workspace data. Pass `related_to` to return the selected client plus same-process related windows such as dialogs or helper popups.
- `move`, `click`, `doubleclick`, `press`, `release`: sends pointer input to a target window selector such as `address:0x1234`; screenshot/window coordinate spaces are converted relative to that target.
- `scroll`: sends wheel axis events to a target window.
- `drag`: presses, moves, and releases on the target window through the native pointer dispatcher; screenshot/window coordinate spaces are converted relative to that target.
- `key`: sends a shortcut such as `ctrl+v`, `enter`, `alt+left`, or `escape` to a target window. It accepts `key`, `keys`, `modifiers`, and raw evdev `keycode` for ydotool-style fallback.
- `type`: sends short text to the target input. Use `method` values `auto`, `keys`, `paste`, or `atspi`; by default it uses background key/paste input. Prefer `paste_text` for datasets, multiline text, CSV/TSV, or anything long.
- `paste_text`, `paste_file`, `paste_image`: writes clipboard data and sends a background paste shortcut to the target window.
- Text paste actions prefer same-process related popup/dialog windows, keep the target session active while a related dialog is open, and restore the previous text clipboard after paste when possible.
- `copy_text`: writes text to the clipboard without sending input.
- `session`: begins, syncs, or ends a related-window workspace guard session. Use `session_action` values `begin`, `sync`, or `end`.
- `wait`: sleeps briefly between UI actions.
- `doctor`: reports AT-SPI/session diagnostics and target accessibility environment hints.
- `activate_menu_item`: activates a `globalMenu` app-menu action by `menu_index` when the target exposes DBusMenu or GMenu.
- `launch`, `launch_app`, `open_app`: opens an app from the compatibility `computer` tool using the same accessibility environment and Chromium/Electron flags as the direct `launch_app` tool. Existing matching windows are reused by default; pass `reuse_existing=false` only for an explicitly requested new instance.
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
scripts/hypr-agent-protalctl indicator 'address:0x1234' 930 520 type
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
    # Keep keyboard focus briefly after modified shortcuts such as Ctrl+V so
    # clients can finish asynchronous clipboard reads before focus is restored.
    keyboard_restore_delay_ms = 700
    # cursor_texture_path = ~/.config/hypr-agent-protal/codex-cursor-252.abgr
  }
}
```

The visible cursor uses `~/.config/hypr-agent-protal/codex-cursor-252.abgr` when present, and otherwise falls back to a procedural texture. Install an extracted Codex Computer Use cursor PNG into that local raw format with:

```sh
scripts/install-codex-cursor-asset
```
