#include "plugin/screenshot_capture.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

#define private public
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#undef private

#include <algorithm>
#include <any>
#include <charconv>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <linux/input-event-codes.h>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>
#include <wayland-server-protocol.h>

inline HANDLE g_pluginHandle = nullptr;

namespace {

template <typename T>
T configValue(const std::string& name, T fallback) {
    const auto value = HyprlandAPI::getConfigValue(g_pluginHandle, name);
    if (!value)
        return fallback;

    try {
        return std::any_cast<T>(value->getValue());
    } catch (const std::bad_any_cast&) {
        return fallback;
    }
}

bool configBool(const std::string& suffix, bool fallback) {
    return configValue<Hyprlang::INT>("plugin:hyprcum:" + suffix, fallback ? 1 : 0) != 0;
}

std::string trim(std::string_view value) {
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front())))
        value.remove_prefix(1);
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back())))
        value.remove_suffix(1);
    return std::string(value);
}

std::vector<std::string> splitCsv(std::string_view input) {
    std::vector<std::string> parts;
    std::string             current;
    bool                    escaped = false;
    bool                    quoted = false;

    for (const char ch : input) {
        if (escaped) {
            current.push_back(ch);
            escaped = false;
            continue;
        }
        if (ch == '\\') {
            escaped = true;
            continue;
        }
        if (ch == '"') {
            quoted = !quoted;
            continue;
        }
        if (ch == ',' && !quoted) {
            parts.push_back(trim(current));
            current.clear();
            continue;
        }
        current.push_back(ch);
    }
    parts.push_back(trim(current));
    return parts;
}

std::optional<double> parseDouble(const std::string& raw) {
    double value = 0.0;
    const auto begin = raw.data();
    const auto end = raw.data() + raw.size();
    const auto [ptr, ec] = std::from_chars(begin, end, value);
    if (ec != std::errc{} || ptr != end || !std::isfinite(value))
        return std::nullopt;
    return value;
}

std::string lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::vector<std::string> splitCombo(std::string_view input) {
    std::vector<std::string> parts;
    std::string             current;

    for (const char ch : input) {
        if (ch == '+' || ch == '-' || ch == ' ') {
            const auto part = trim(current);
            if (!part.empty())
                parts.push_back(lower(part));
            current.clear();
            continue;
        }
        current.push_back(ch);
    }

    const auto part = trim(current);
    if (!part.empty())
        parts.push_back(lower(part));
    return parts;
}

std::optional<uint32_t> pointerButton(std::string raw) {
    raw = lower(trim(raw));
    if (raw.empty() || raw == "left")
        return BTN_LEFT;
    if (raw == "right")
        return BTN_RIGHT;
    if (raw == "middle")
        return BTN_MIDDLE;
    if (raw == "side")
        return BTN_SIDE;
    if (raw == "extra")
        return BTN_EXTRA;

    uint32_t value = 0;
    const auto [ptr, ec] = std::from_chars(raw.data(), raw.data() + raw.size(), value);
    if (ec == std::errc{} && ptr == raw.data() + raw.size())
        return value;
    return std::nullopt;
}

std::optional<uint32_t> keyboardKey(std::string raw) {
    raw = lower(trim(raw));
    if (raw.empty())
        return std::nullopt;

    static const std::unordered_map<std::string, uint32_t> keys = {
        {"a", KEY_A},
        {"b", KEY_B},
        {"c", KEY_C},
        {"d", KEY_D},
        {"e", KEY_E},
        {"f", KEY_F},
        {"g", KEY_G},
        {"h", KEY_H},
        {"i", KEY_I},
        {"j", KEY_J},
        {"k", KEY_K},
        {"l", KEY_L},
        {"m", KEY_M},
        {"n", KEY_N},
        {"o", KEY_O},
        {"p", KEY_P},
        {"q", KEY_Q},
        {"r", KEY_R},
        {"s", KEY_S},
        {"t", KEY_T},
        {"u", KEY_U},
        {"v", KEY_V},
        {"w", KEY_W},
        {"x", KEY_X},
        {"y", KEY_Y},
        {"z", KEY_Z},
        {"esc", KEY_ESC},
        {"escape", KEY_ESC},
        {"enter", KEY_ENTER},
        {"return", KEY_ENTER},
        {"tab", KEY_TAB},
        {"backspace", KEY_BACKSPACE},
        {"delete", KEY_DELETE},
        {"del", KEY_DELETE},
        {"insert", KEY_INSERT},
        {"ins", KEY_INSERT},
        {"space", KEY_SPACE},
        {"minus", KEY_MINUS},
        {"-", KEY_MINUS},
        {"equal", KEY_EQUAL},
        {"=", KEY_EQUAL},
        {"leftbrace", KEY_LEFTBRACE},
        {"[", KEY_LEFTBRACE},
        {"rightbrace", KEY_RIGHTBRACE},
        {"]", KEY_RIGHTBRACE},
        {"backslash", KEY_BACKSLASH},
        {"\\", KEY_BACKSLASH},
        {"semicolon", KEY_SEMICOLON},
        {";", KEY_SEMICOLON},
        {"apostrophe", KEY_APOSTROPHE},
        {"quote", KEY_APOSTROPHE},
        {"'", KEY_APOSTROPHE},
        {"grave", KEY_GRAVE},
        {"`", KEY_GRAVE},
        {"comma", KEY_COMMA},
        {",", KEY_COMMA},
        {"dot", KEY_DOT},
        {"period", KEY_DOT},
        {".", KEY_DOT},
        {"slash", KEY_SLASH},
        {"/", KEY_SLASH},
        {"up", KEY_UP},
        {"down", KEY_DOWN},
        {"left", KEY_LEFT},
        {"right", KEY_RIGHT},
        {"home", KEY_HOME},
        {"end", KEY_END},
        {"pageup", KEY_PAGEUP},
        {"pgup", KEY_PAGEUP},
        {"pagedown", KEY_PAGEDOWN},
        {"pgdn", KEY_PAGEDOWN},
        {"capslock", KEY_CAPSLOCK},
        {"leftctrl", KEY_LEFTCTRL},
        {"ctrl", KEY_LEFTCTRL},
        {"control", KEY_LEFTCTRL},
        {"leftshift", KEY_LEFTSHIFT},
        {"shift", KEY_LEFTSHIFT},
        {"leftalt", KEY_LEFTALT},
        {"alt", KEY_LEFTALT},
        {"option", KEY_LEFTALT},
        {"leftmeta", KEY_LEFTMETA},
        {"meta", KEY_LEFTMETA},
        {"super", KEY_LEFTMETA},
        {"win", KEY_LEFTMETA},
        {"cmd", KEY_LEFTMETA},
        {"command", KEY_LEFTMETA},
        {"rightctrl", KEY_RIGHTCTRL},
        {"rightshift", KEY_RIGHTSHIFT},
        {"rightalt", KEY_RIGHTALT},
        {"rightmeta", KEY_RIGHTMETA},
    };

    if (raw.size() == 1) {
        const char ch = raw.front();
        if (ch >= '1' && ch <= '9')
            return KEY_1 + static_cast<uint32_t>(ch - '1');
        if (ch == '0')
            return KEY_0;
    }

    if (raw.size() > 1 && raw.front() == 'f') {
        uint32_t n = 0;
        const auto [ptr, ec] = std::from_chars(raw.data() + 1, raw.data() + raw.size(), n);
        if (ec == std::errc{} && ptr == raw.data() + raw.size() && n >= 1 && n <= 12)
            return KEY_F1 + n - 1;
    }

    if (const auto it = keys.find(raw); it != keys.end())
        return it->second;

    uint32_t value = 0;
    const auto [ptr, ec] = std::from_chars(raw.data(), raw.data() + raw.size(), value);
    if (ec == std::errc{} && ptr == raw.data() + raw.size() && value <= KEY_MAX)
        return value;
    return std::nullopt;
}

struct KeyboardModifier {
    uint32_t key;
    uint32_t mask;
};

std::optional<KeyboardModifier> keyboardModifier(std::string raw) {
    raw = lower(trim(raw));
    if (raw == "ctrl" || raw == "control")
        return KeyboardModifier{.key = KEY_LEFTCTRL, .mask = 1U << 2};
    if (raw == "shift")
        return KeyboardModifier{.key = KEY_LEFTSHIFT, .mask = 1U << 0};
    if (raw == "alt" || raw == "option")
        return KeyboardModifier{.key = KEY_LEFTALT, .mask = 1U << 3};
    if (raw == "meta" || raw == "super" || raw == "win" || raw == "cmd" || raw == "command")
        return KeyboardModifier{.key = KEY_LEFTMETA, .mask = 1U << 6};
    return std::nullopt;
}

uint32_t nowMs() {
    return static_cast<uint32_t>(Time::millis(Time::steadyNow()) & 0xFFFFFFFFU);
}

struct TargetSurface {
    PHLWINDOW              window;
    SP<CWLSurfaceResource> surface;
    Vector2D               local;
};

CBox windowMainSurfaceGoalBox(const PHLWINDOW& window) {
    if (!window)
        return {};

    return CBox{window->m_realPosition ? window->m_realPosition->goal().x : window->m_position.x,
                window->m_realPosition ? window->m_realPosition->goal().y : window->m_position.y,
                window->m_realSize ? window->m_realSize->goal().x : window->m_size.x,
                window->m_realSize ? window->m_realSize->goal().y : window->m_size.y};
}

std::optional<TargetSurface> resolveTargetSurface(const std::string& targetRegex, const Vector2D& globalPos) {
    if (!g_pCompositor)
        return std::nullopt;

    const auto window = g_pCompositor->getWindowByRegex(targetRegex);
    if (!window || !window->m_isMapped)
        return std::nullopt;

    if (window->m_isX11) {
        if (!window->wlSurface() || !window->wlSurface()->resource())
            return std::nullopt;

        const auto mainBox = windowMainSurfaceGoalBox(window);
        Vector2D   local = globalPos - mainBox.pos();
        const auto scale = window->m_X11SurfaceScaledBy <= 0.0F ? 1.0F : window->m_X11SurfaceScaledBy;
        local = local * scale;
        return TargetSurface{.window = window, .surface = window->wlSurface()->resource(), .local = local};
    }

    Vector2D local;
    auto     surface = g_pCompositor->vectorWindowToSurface(globalPos, window, local);
    if (!surface && window->wlSurface() && window->wlSurface()->resource()) {
        const auto mainBox = windowMainSurfaceGoalBox(window);
        local = globalPos - mainBox.pos();
        surface = window->wlSurface()->resource();
    }

    if (!surface)
        return std::nullopt;
    return TargetSurface{.window = window, .surface = surface, .local = local};
}

std::optional<TargetSurface> resolveTargetMainSurface(const std::string& targetRegex) {
    if (!g_pCompositor)
        return std::nullopt;

    const auto window = g_pCompositor->getWindowByRegex(targetRegex);
    if (!window || !window->m_isMapped || !window->wlSurface() || !window->wlSurface()->resource())
        return std::nullopt;

    const auto mainBox = windowMainSurfaceGoalBox(window);
    return TargetSurface{.window = window, .surface = window->wlSurface()->resource(), .local = mainBox.middle()};
}

struct PointerFocusRestore {
    SP<CWLSurfaceResource> previousSurface;
    Vector2D               previousLocal;

    PointerFocusRestore() {
        if (!g_pSeatManager)
            return;
        previousSurface = g_pSeatManager->m_state.pointerFocus.lock();
        previousLocal = g_pSeatManager->m_lastLocalCoords;
    }

    ~PointerFocusRestore() {
        if (!g_pSeatManager)
            return;
        g_pSeatManager->setPointerFocus(previousSurface, previousLocal);
        g_pSeatManager->sendPointerFrame();
    }

    PointerFocusRestore(const PointerFocusRestore&) = delete;
    PointerFocusRestore& operator=(const PointerFocusRestore&) = delete;
};

struct KeyboardFocusRestore {
    SP<CWLSurfaceResource> previousSurface;

    KeyboardFocusRestore() {
        if (!g_pSeatManager)
            return;
        previousSurface = g_pSeatManager->m_state.keyboardFocus.lock();
    }

    ~KeyboardFocusRestore() {
        if (!g_pSeatManager)
            return;
        g_pSeatManager->sendKeyboardMods(0, 0, 0, 0);
        g_pSeatManager->setKeyboardFocus(previousSurface);
    }

    KeyboardFocusRestore(const KeyboardFocusRestore&) = delete;
    KeyboardFocusRestore& operator=(const KeyboardFocusRestore&) = delete;
};

void sendPointerScroll(double dx, double dy) {
    const auto sendAxis = [](wl_pointer_axis axis, double ticks) {
        if (std::abs(ticks) < 0.001)
            return;
        const auto discrete = static_cast<int32_t>(ticks > 0 ? std::ceil(ticks) : std::floor(ticks));
        const auto value120 = static_cast<int32_t>(std::round(ticks * 120.0));
        g_pSeatManager->sendPointerAxis(nowMs(), axis, ticks * 15.0, discrete, value120, WL_POINTER_AXIS_SOURCE_WHEEL, WL_POINTER_AXIS_RELATIVE_DIRECTION_IDENTICAL);
    };

    sendAxis(WL_POINTER_AXIS_HORIZONTAL_SCROLL, dx);
    sendAxis(WL_POINTER_AXIS_VERTICAL_SCROLL, dy);
    g_pSeatManager->sendPointerFrame();
}

SDispatchResult dispatchPointer(const std::string& args) {
    if (!configBool("allow_pointer", true))
        return {.success = false, .error = "HyprCUM pointer dispatch is disabled"};
    if (!g_pSeatManager)
        return {.success = false, .error = "seat manager is not ready"};

    const auto parts = splitCsv(args);
    if (parts.size() < 4)
        return {.success = false, .error = "usage: HyprCUM:pointer <window-regex>,<global-x>,<global-y>,<move|click|press|release>[,<button>]"};

    const auto x = parseDouble(parts[1]);
    const auto y = parseDouble(parts[2]);
    if (!x || !y)
        return {.success = false, .error = "pointer coordinates must be finite numbers"};

    const auto target = resolveTargetSurface(parts[0], Vector2D{*x, *y});
    if (!target)
        return {.success = false, .error = "target window/surface not found"};

    const auto action = lower(parts[3]);
    const auto button = pointerButton(parts.size() >= 5 ? parts[4] : "left");
    if (!button && action != "move" && action != "motion" && action != "scroll")
        return {.success = false, .error = "unknown pointer button"};

    PointerFocusRestore restore;
    g_pSeatManager->setPointerFocus(target->surface, target->local);
    g_pSeatManager->sendPointerMotion(nowMs(), target->local);

    if (action == "move" || action == "motion") {
        g_pSeatManager->sendPointerFrame();
        return {.success = true};
    }

    if (action == "scroll") {
        const auto dy = parts.size() >= 5 ? parseDouble(parts[4]) : std::optional<double>{1.0};
        const auto dx = parts.size() >= 6 ? parseDouble(parts[5]) : std::optional<double>{0.0};
        if (!dx || !dy)
            return {.success = false, .error = "scroll dx/dy must be finite numbers"};
        sendPointerScroll(*dx, *dy);
        return {.success = true};
    }

    if (action == "click") {
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_PRESSED);
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_RELEASED);
        g_pSeatManager->sendPointerFrame();
        return {.success = true};
    }

    if (action == "doubleclick" || action == "double-click") {
        for (int i = 0; i < 2; ++i) {
            g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_PRESSED);
            g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_RELEASED);
        }
        g_pSeatManager->sendPointerFrame();
        return {.success = true};
    }

    if (action == "press" || action == "down") {
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_PRESSED);
        g_pSeatManager->sendPointerFrame();
        return {.success = true};
    }

    if (action == "release" || action == "up") {
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_RELEASED);
        g_pSeatManager->sendPointerFrame();
        return {.success = true};
    }

    return {.success = false, .error = "unknown pointer action"};
}

SDispatchResult dispatchKeyboard(const std::string& args) {
    if (!configBool("allow_keyboard", true))
        return {.success = false, .error = "HyprCUM keyboard dispatch is disabled"};
    if (!g_pSeatManager)
        return {.success = false, .error = "seat manager is not ready"};

    const auto parts = splitCsv(args);
    if (parts.size() < 3)
        return {.success = false, .error = "usage: HyprCUM:keyboard <window-regex>,<tap|press|release>,<key>[,<modifiers>][,<global-x>,<global-y>]"};

    std::optional<TargetSurface> target;
    if (parts.size() >= 6) {
        const auto x = parseDouble(parts[4]);
        const auto y = parseDouble(parts[5]);
        if (!x || !y)
            return {.success = false, .error = "keyboard focus coordinates must be finite numbers"};
        target = resolveTargetSurface(parts[0], Vector2D{*x, *y});
    } else {
        target = resolveTargetMainSurface(parts[0]);
    }
    if (!target)
        return {.success = false, .error = "target window/surface not found"};

    const auto action = lower(parts[1]);
    const auto key = keyboardKey(parts[2]);
    if (!key)
        return {.success = false, .error = "unknown keyboard key"};

    std::vector<KeyboardModifier> modifiers;
    uint32_t                      modifierMask = 0;
    if (parts.size() >= 4) {
        for (const auto& name : splitCombo(parts[3])) {
            auto mod = keyboardModifier(name);
            if (!mod)
                return {.success = false, .error = "unknown keyboard modifier"};
            modifierMask |= mod->mask;
            modifiers.push_back(*mod);
        }
    }

    KeyboardFocusRestore restore;
    g_pSeatManager->setKeyboardFocus(target->surface);

    const auto pressModifiers = [&] {
        for (const auto& mod : modifiers)
            g_pSeatManager->sendKeyboardKey(nowMs(), mod.key, WL_KEYBOARD_KEY_STATE_PRESSED);
        if (modifierMask != 0)
            g_pSeatManager->sendKeyboardMods(modifierMask, 0, 0, 0);
    };

    const auto releaseModifiers = [&] {
        if (modifierMask != 0)
            g_pSeatManager->sendKeyboardMods(0, 0, 0, 0);
        for (auto it = modifiers.rbegin(); it != modifiers.rend(); ++it)
            g_pSeatManager->sendKeyboardKey(nowMs(), it->key, WL_KEYBOARD_KEY_STATE_RELEASED);
    };

    if (action == "tap" || action == "press-release") {
        pressModifiers();
        g_pSeatManager->sendKeyboardKey(nowMs(), *key, WL_KEYBOARD_KEY_STATE_PRESSED);
        g_pSeatManager->sendKeyboardKey(nowMs(), *key, WL_KEYBOARD_KEY_STATE_RELEASED);
        releaseModifiers();
        return {.success = true};
    }

    if (action == "press" || action == "down") {
        pressModifiers();
        g_pSeatManager->sendKeyboardKey(nowMs(), *key, WL_KEYBOARD_KEY_STATE_PRESSED);
        return {.success = true};
    }

    if (action == "release" || action == "up") {
        g_pSeatManager->sendKeyboardKey(nowMs(), *key, WL_KEYBOARD_KEY_STATE_RELEASED);
        releaseModifiers();
        return {.success = true};
    }

    return {.success = false, .error = "unknown keyboard action"};
}

SDispatchResult dispatchScreenshot(const std::string& args) {
    if (!configBool("allow_screenshot", true))
        return {.success = false, .error = "HyprCUM screenshot dispatch is disabled"};

    const auto parts = splitCsv(args);
    const auto path = parts.empty() ? std::string{} : trim(parts[0]);
    if (path.empty())
        return {.success = false, .error = "usage: HyprCUM:screenshot <output-session-json-path>[,<window-regex>]"};

    const auto target = parts.size() >= 2 ? trim(parts[1]) : std::string{};
    const auto result = hyprcum::captureScreenshotSession(std::filesystem::path(path), target);
    if (!result.success)
        return {.success = false, .error = result.error};
    return {.success = true};
}

} // namespace

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    g_pluginHandle = handle;

    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hyprcum:allow_pointer", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hyprcum:allow_keyboard", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hyprcum:allow_screenshot", Hyprlang::INT{1});

    HyprlandAPI::addDispatcherV2(g_pluginHandle, "HyprCUM:pointer", dispatchPointer);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hyprcum:pointer", dispatchPointer);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "HyprCUM:keyboard", dispatchKeyboard);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hyprcum:keyboard", dispatchKeyboard);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "HyprCUM:screenshot", dispatchScreenshot);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hyprcum:screenshot", dispatchScreenshot);
    HyprlandAPI::reloadConfig();

    return {
        .name = "Hypr-ComputerUse-MCP",
        .description = "Background screenshot, pointer, and keyboard primitives for Hyprland Computer Use",
        .author = "wilf",
        .version = "0.2.1",
    };
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_pluginHandle = nullptr;
}
