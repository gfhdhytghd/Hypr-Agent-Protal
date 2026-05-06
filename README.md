# Hypr-ComputerUse-MCP

Hypr-ComputerUse-MCP is an experimental Hyprland plugin plus MCP bridge for background Computer Use.

It exposes two compositor dispatchers:

```ini
hyprctl dispatch HyprCUM:screenshot /tmp/hyprcum-session.json
hyprctl dispatch HyprCUM:pointer 'address:0x1234,930,520,click,left'
```

The screenshot dispatcher renders active monitor workspaces into RGBA artifacts from inside Hyprland, then writes a JSON session file. The pointer dispatcher resolves the target with `g_pCompositor->getWindowByRegex()`, focuses the target surface only for the injected pointer events, sends motion/button/frame events through `g_pSeatManager`, then restores the previous pointer focus.

For XWayland windows, the dispatcher sends to the main `wlSurface()` resource and scales surface-local coordinates by `m_X11SurfaceScaledBy`. For native Wayland windows, it uses `vectorWindowToSurface(globalPos, window, localPos)` so subsurfaces receive local coordinates.

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
- `move`, `click`, `doubleclick`, `press`, `release`: sends pointer input to a target window selector such as `address:0x1234`.

The command-line bridge is also usable directly:

```sh
scripts/hyprcumctl screenshot --base64
scripts/hyprcumctl pointer 'address:0x1234' 930 520 click left
```

## Config

```ini
plugin {
  hyprcum {
    allow_screenshot = 1
    allow_pointer = 1
  }
}
```
