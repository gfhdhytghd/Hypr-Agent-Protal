# hypr-agent-protal Agent Notes

When a task says to use `hypr-agent-protal`, use the `hypr-agent-protal` MCP tools, not Browser MCP, shell GUI automation, or the obsolete `hyprcum` namespace.

For browser/app-control tasks:

1. If the user asks to open, launch, or create a new app/window, call `launch_app` first. For browser tasks, pass the requested URL and `new_window=true`.
2. Use the returned `target` selector, usually `address:0x...`, for the rest of the task.
3. Call `get_app_state` after launch and after each navigation/action that changes the page.
4. Prefer `element_index` actions from the app-state tree. Use screenshot coordinates only when the tree is missing or ambiguous.
5. Use `paste_text` for multiline, tabular, CSV/TSV, Unicode-heavy, or long text. Do not enter datasets with repeated `type_text`/`key` calls unless paste is unavailable. On grid-like targets, bulk paste exits cell edit mode before pasting so TSV/CSV expands into cells.
6. For shortcuts, use `press_key`/`key` with `key`, `keys`, `modifiers`, or `keycode`; examples: `enter`, `alt+left`, `{"key":"left","modifiers":"alt"}`.
7. If direct tools are not exposed, the compatibility `computer` tool supports equivalent actions, including `launch_app`, `get_app_state`, `click`, `type`, `paste_text`, `key`, `scroll`, `drag`, and `wait`.
8. Read `uiHints` before acting on menus, tabs, or toolbars. `controlType=menu` is an AT-SPI menu entry, not a tab/ribbon page; do not substitute a same-named menu for a requested tab. If the visible tab/ribbon label is not exposed as a tab element, use screenshot/window-relative coordinates on the visible label and refresh `get_app_state`.
9. Do not invent app-specific shortcuts or search-result heuristics; refresh `get_app_state` and act on visible elements or screenshot/window-relative coordinates.

Do not switch to Browser MCP just because the target app is a browser; `hypr-agent-protal` controls Chromium through Hyprland background screenshots, AT-SPI app state, and background input.
