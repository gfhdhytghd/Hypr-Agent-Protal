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
    if (!button && action != "move" && action != "motion")
        return {.success = false, .error = "unknown pointer button"};

    PointerFocusRestore restore;
    g_pSeatManager->setPointerFocus(target->surface, target->local);
    g_pSeatManager->sendPointerMotion(nowMs(), target->local);

    if (action == "move" || action == "motion") {
        g_pSeatManager->sendPointerFrame();
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

SDispatchResult dispatchScreenshot(const std::string& args) {
    if (!configBool("allow_screenshot", true))
        return {.success = false, .error = "HyprCUM screenshot dispatch is disabled"};

    const auto path = trim(args);
    if (path.empty())
        return {.success = false, .error = "usage: HyprCUM:screenshot <output-session-json-path>"};

    const auto result = hyprcum::captureScreenshotSession(std::filesystem::path(path));
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
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hyprcum:allow_screenshot", Hyprlang::INT{1});

    HyprlandAPI::addDispatcherV2(g_pluginHandle, "HyprCUM:pointer", dispatchPointer);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hyprcum:pointer", dispatchPointer);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "HyprCUM:screenshot", dispatchScreenshot);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hyprcum:screenshot", dispatchScreenshot);
    HyprlandAPI::reloadConfig();

    return {
        .name = "Hypr-ComputerUse-MCP",
        .description = "Background screenshot and pointer primitives for Hyprland Computer Use",
        .author = "wilf",
        .version = "0.1.0",
    };
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_pluginHandle = nullptr;
}
