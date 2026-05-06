# Hypr-ComputerUse-MCP

Hypr-ComputerUse-MCP is an experimental Hyprland plugin plus MCP bridge for background Computer Use.

It exposes three compositor dispatchers:

```ini
hyprctl dispatch HyprCUM:screenshot /tmp/hyprcum-session.json
hyprctl dispatch HyprCUM:pointer 'address:0x1234,930,520,click,left'
hyprctl dispatch HyprCUM:keyboard 'address:0x1234,tap,v,ctrl'
```

The screenshot dispatcher renders active monitor workspaces into RGBA artifacts from inside Hyprland, then writes a JSON session file. The pointer dispatcher resolves the target with `g_pCompositor->getWindowByRegex()`, focuses the target surface only for the injected pointer events, sends motion/button/frame events through `g_pSeatManager`, then restores the previous pointer focus.

For XWayland windows, the dispatcher sends to the main `wlSurface()` resource and scales surface-local coordinates by `m_X11SurfaceScaledBy`. For native Wayland windows, it uses `vectorWindowToSurface(globalPos, window, localPos)` so subsurfaces receive local coordinates.

The keyboard dispatcher temporarily focuses the target surface, sends key events and modifier state through `g_pSeatManager`, then restores the previous keyboard focus. The MCP layer uses that for shortcuts and text/file/image paste flows.

## Build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

Install or load `build/libhyprcum.so` as a Hyprland plugin. With hyprpm:

```sh
hyprpm add .
hyprpm enable hyprcum
hyprpm reload
```

## MCP

The repository includes a Codex plugin manifest and a stdio MCP server:

```sh
python3 mcp/hyprcum_mcp.py
```

The MCP server exposes one tool named `computer` with these actions:

- `screenshot`: captures a compositor screenshot and returns PNG image content plus monitor/window metadata.
- `windows`: lists Hyprland clients with addresses, classes, titles, geometry, and workspace data.
- `move`, `click`, `doubleclick`, `press`, `release`: sends pointer input to a target window selector such as `address:0x1234`.
- `scroll`: sends wheel axis events to a target window.
- `drag`: presses, moves, and releases on the target window.
- `key`: sends a shortcut such as `ctrl+v`, `enter`, or `escape` to a target window. It also accepts raw evdev keycodes through `keycode` for ydotool-style fallback.
- `type`, `paste_text`, `paste_file`, `paste_image`: writes clipboard data and sends a background paste shortcut to the target window.
- `copy_text`: writes text to the clipboard without sending input.
- `wait`: sleeps briefly between UI actions.

The command-line bridge is also usable directly:

```sh
scripts/hyprcumctl screenshot --base64
scripts/hyprcumctl windows
scripts/hyprcumctl pointer 'address:0x1234' 930 520 click left
scripts/hyprcumctl pointer 'address:0x1234' 930 520 scroll -3
scripts/hyprcumctl keyboard 'address:0x1234' tap v ctrl
scripts/hyprcumctl keyboard 'address:0x1234' tap 28
```

## Config

```ini
plugin {
  hyprcum {
    allow_screenshot = 1
    allow_pointer = 1
    allow_keyboard = 1
  }
}
```
