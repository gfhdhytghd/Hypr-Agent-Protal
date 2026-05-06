# hypr-agent-protal Agent Notes

When a task says to use `hypr-agent-protal`, use the `hypr-agent-protal` MCP tools, not Browser MCP, shell GUI automation, or the obsolete `hyprcum` namespace.

For browser/app-control tasks:

1. If the user asks to open, launch, or create a new app/window, call `launch_app` first. For browser tasks, pass the requested URL and `new_window=true`.
2. Use the returned `target` selector, usually `address:0x...`, for the rest of the task.
3. Call `get_app_state` after launch and after each navigation/action that changes the page.
4. Prefer `element_index` actions from the app-state tree. Use screenshot coordinates only when the tree is missing or ambiguous.
5. For shortcuts, use `press_key`/`key` with `key`, `keys`, `modifiers`, or `keycode`; examples: `enter`, `alt+left`, `{"key":"left","modifiers":"alt"}`.
6. If direct tools are not exposed, the compatibility `computer` tool supports equivalent actions, including `launch_app`, `get_app_state`, `click`, `type`, `key`, `scroll`, `drag`, and `wait`.
7. Do not invent app-specific shortcuts or search-result heuristics; refresh `get_app_state` and act on visible elements or screenshot/window-relative coordinates.

Do not switch to Browser MCP just because the target app is a browser; `hypr-agent-protal` controls Chromium through Hyprland background screenshots, AT-SPI app state, and background input.
