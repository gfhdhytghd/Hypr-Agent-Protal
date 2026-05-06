# hypr-agent-protal Agent Notes

When a task says to use `hypr-agent-protal`, use the `hypr-agent-protal` MCP tools, not Browser MCP, shell GUI automation, or the obsolete `hyprcum` namespace.

For browser/app-control tasks:

1. Unless the user explicitly asks to open, launch, create, or use a new app/window/instance, call `list_apps` first and reuse an existing matching target.
2. If the user asks to open or launch an app, call `launch_app`/`open_app`; these tools reuse existing windows by default. Set `reuse_existing=false` or `new_window=true` only when the user explicitly asks for a new instance/window.
3. Use the returned `target` selector, usually `address:0x...`, for the rest of the task.
4. Call `get_app_state` after launch and after each navigation/action that changes the page.
5. Prefer `element_index` actions from the app-state tree. Use screenshot coordinates only when the tree is missing or ambiguous.
6. Use `paste_text` for multiline, tabular, CSV/TSV, Unicode-heavy, or long text. Do not enter datasets with repeated `type_text`/`key` calls unless paste is unavailable. On grid-like targets, bulk paste exits cell edit mode before pasting so TSV/CSV expands into cells.
7. For shortcuts, use `press_key`/`key` with `key`, `keys`, `modifiers`, or `keycode`; examples: `enter`, `alt+left`, `{"key":"left","modifiers":"alt"}`.
8. If direct tools are not exposed, the compatibility `computer` tool supports equivalent actions, including `launch_app`, `get_app_state`, `click`, `type`, `paste_text`, `key`, `scroll`, `drag`, and `wait`.
9. Read `uiHints` before acting on menus, tabs, or toolbars. `controlType=menu` is a toolkit role and may be a classic menu, command label, or ribbon/notebookbar page selector; verify the current screenshot/app state instead of assuming the visual meaning.
10. If `get_app_state` exposes `globalMenu` actions, use `activate_menu_item` with the returned `menu_index` for app-menu commands. If no global menu item is exposed, use visible elements or screenshot/window-relative coordinates.
11. Do not invent app-specific shortcuts or search-result heuristics; refresh `get_app_state` and act on visible elements or screenshot/window-relative coordinates.

Do not switch to Browser MCP just because the target app is a browser; `hypr-agent-protal` controls Chromium through Hyprland background screenshots, AT-SPI app state, and background input.
