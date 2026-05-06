# hypr-agent-protal

hypr-agent-protal is an experimental Hyprland plugin plus MCP bridge for background agent control.

It exposes four compositor dispatchers:

```ini
hyprctl dispatch hypr-agent-protal:screenshot /tmp/hypr-agent-protal-session.json
hyprctl dispatch hypr-agent-protal:screenshot '/tmp/hypr-agent-protal-session.json,address:0x1234'
hyprctl dispatch hypr-agent-protal:pointer 'address:0x1234,930,520,click,left'
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

The MCP server exposes one tool named `computer` with these actions:

- `screenshot`: captures compositor screenshots and returns PNG image content plus metadata. Pass `target` for unoccluded target-window capture. Screenshot cursor drawing is a debug option and is off by default; use `show_cursor=true` or `cursor_source` values `auto`, `agent`, `hyprland`, `none` to draw it into the returned PNG.
- `windows`: lists Hyprland clients with addresses, classes, titles, geometry, and workspace data. Pass `related_to` to return the selected client plus same-process related windows such as XWayland popups.
- `move`, `click`, `doubleclick`, `press`, `release`: sends pointer input to a target window selector such as `address:0x1234`.
- `scroll`: sends wheel axis events to a target window.
- `drag`: presses, moves, and releases on the target window.
- `key`: sends a shortcut such as `ctrl+v`, `enter`, or `escape` to a target window. It also accepts raw evdev keycodes through `keycode` for ydotool-style fallback.
- `type`: sends text to the target input. Use `method` values `auto`, `keys`, or `paste`; by default it types short ASCII text as key events and uses clipboard paste for Unicode or longer text.
- `paste_text`, `paste_file`, `paste_image`: writes clipboard data and sends a background paste shortcut to the target window.
- Text paste actions prefer same-process XWayland popup windows, such as WeChat search results, and restore the previous text clipboard after paste when possible.
- `copy_text`: writes text to the clipboard without sending input.
- `session`: begins, syncs, or ends a related-window workspace guard session. Use `session_action` values `begin`, `sync`, or `end`.
- `wait`: sleeps briefly between UI actions.

The command-line bridge is also usable directly:

```sh
scripts/hypr-agent-protalctl screenshot --base64
scripts/hypr-agent-protalctl screenshot --target 'address:0x1234' --base64
scripts/hypr-agent-protalctl screenshot --cursor-source agent --base64
scripts/hypr-agent-protalctl windows
scripts/hypr-agent-protalctl windows --related-to 'address:0x1234'
scripts/hypr-agent-protalctl pointer 'address:0x1234' 930 520 click left
scripts/hypr-agent-protalctl pointer 'address:0x1234' 930 520 scroll -3
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
    indicator_timeout_ms = 12000
  }
}
```
