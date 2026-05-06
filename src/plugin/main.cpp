#include "plugin/screenshot_capture.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

#define private public
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopManager.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopTimer.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/pass/RectPassElement.hpp>
#include <hyprland/src/xwayland/XSurface.hpp>
#undef private

#include <hyprutils/signal/Listener.hpp>

#include <algorithm>
#include <any>
#include <array>
#include <charconv>
#include <chrono>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <iterator>
#include <limits>
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

std::vector<SP<CEventLoopTimer>> g_pointerRestoreTimers;
std::vector<SP<CEventLoopTimer>> g_workspaceRestackTimers;
SP<CEventLoopTimer>              g_indicatorHideTimer;
SP<CEventLoopTimer>              g_indicatorAnimationTimer;
CHyprSignalListener              g_windowOpenListener;
CHyprSignalListener              g_renderStageListener;
std::optional<Vector2D>          g_agentPointerPosition;
std::optional<Time::steady_tp>   g_agentPointerUpdated;
std::string                      g_agentPointerAction;

constexpr std::array<std::string_view, 17> CODEX_CURSOR_BITMAP = {
    "X              ",
    "XX             ",
    "XOX            ",
    "XOOX           ",
    "XOOOX          ",
    "XOOOOX         ",
    "XOOOOOX        ",
    "XOOOOOOX       ",
    "XOOOOOOOX      ",
    "XOOOOOOOOX     ",
    "XOOOOXXXXX     ",
    "XOOX           ",
    "XOXX           ",
    "XX XX          ",
    "X   XX         ",
    "     XX        ",
    "      XX       ",
};

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
    return configValue<Hyprlang::INT>("plugin:hypr-agent-protal:" + suffix, fallback ? 1 : 0) != 0;
}

int configInt(const std::string& suffix, int fallback) {
    return static_cast<int>(configValue<Hyprlang::INT>("plugin:hypr-agent-protal:" + suffix, fallback));
}

CBox agentIndicatorBounds(const Vector2D& globalPos) {
    return CBox{globalPos.x - 14.0, globalPos.y - 14.0, 76.0, 76.0};
}

void damageAgentIndicator() {
    if (!g_pHyprRenderer || !g_agentPointerPosition)
        return;

    g_pHyprRenderer->damageBox(agentIndicatorBounds(*g_agentPointerPosition));
}

Time::steady_dur indicatorTimeout() {
    return std::chrono::milliseconds(std::clamp(configInt("indicator_timeout_ms", 2600), 250, 15000));
}

bool cursorBitmapCellFilled(int x, int y) {
    if (y < 0 || y >= static_cast<int>(CODEX_CURSOR_BITMAP.size()))
        return false;
    const auto row = CODEX_CURSOR_BITMAP[static_cast<size_t>(y)];
    if (x < 0 || x >= static_cast<int>(row.size()))
        return false;
    return row[static_cast<size_t>(x)] != ' ';
}

void addIndicatorRect(const CBox& box, const CHyprColor& color, int round = 0) {
    if (!g_pHyprRenderer || box.w <= 0.0 || box.h <= 0.0 || color.a <= 0.0F)
        return;

    CRectPassElement::SRectData data;
    data.box   = box;
    data.color = color;
    data.round = round;
    g_pHyprRenderer->m_renderPass.add(makeUnique<CRectPassElement>(data));
}

void addIndicatorCircle(const Vector2D& center, double diameter, const CHyprColor& color) {
    if (diameter <= 0.0)
        return;

    addIndicatorRect(CBox{center.x - diameter / 2.0, center.y - diameter / 2.0, diameter, diameter}, color, static_cast<int>(std::lround(diameter / 2.0)));
}

void renderCodexCursorBitmap(const Vector2D& tip, double monitorScale, double fade) {
    const double unit = std::max(1.0, 1.65 * monitorScale);
    const auto   fill = CHyprColor(0.98F, 0.97F, 1.00F, static_cast<float>(0.96 * fade));
    const auto   outline = CHyprColor(0.42F, 0.18F, 0.58F, static_cast<float>(0.82 * fade));
    const auto   shadow = CHyprColor(0.0F, 0.0F, 0.0F, static_cast<float>(0.20 * fade));

    const auto drawCell = [&](int x, int y, const Vector2D& offset, const CHyprColor& color, double expand = 0.35) {
        addIndicatorRect(
            CBox{tip.x + offset.x + x * unit, tip.y + offset.y + y * unit, unit + expand * monitorScale, unit + expand * monitorScale},
            color);
    };

    const int rows = static_cast<int>(CODEX_CURSOR_BITMAP.size());
    for (int y = 0; y < rows; ++y) {
        const int cols = static_cast<int>(CODEX_CURSOR_BITMAP[static_cast<size_t>(y)].size());
        for (int x = 0; x < cols; ++x) {
            if (cursorBitmapCellFilled(x, y))
                drawCell(x, y, Vector2D{1.4 * monitorScale, 2.0 * monitorScale}, shadow, 0.15);
        }
    }

    for (int y = -1; y <= rows; ++y) {
        for (int x = -1; x <= 16; ++x) {
            if (cursorBitmapCellFilled(x, y))
                continue;

            bool adjacent = false;
            for (int oy = -1; oy <= 1 && !adjacent; ++oy) {
                for (int ox = -1; ox <= 1; ++ox) {
                    if (cursorBitmapCellFilled(x + ox, y + oy)) {
                        adjacent = true;
                        break;
                    }
                }
            }

            if (adjacent)
                drawCell(x, y, {}, outline, 0.15);
        }
    }

    for (int y = 0; y < rows; ++y) {
        const int cols = static_cast<int>(CODEX_CURSOR_BITMAP[static_cast<size_t>(y)].size());
        for (int x = 0; x < cols; ++x) {
            if (cursorBitmapCellFilled(x, y))
                drawCell(x, y, {}, fill, 0.15);
        }
    }
}

void renderAgentIndicator(eRenderStage stage) {
    if (stage != RENDER_LAST_MOMENT || !configBool("show_indicator", true) || !g_agentPointerPosition || !g_agentPointerUpdated || !g_pHyprOpenGL)
        return;

    const auto monitor = g_pHyprOpenGL->m_renderData.pMonitor.lock();
    if (!monitor)
        return;

    const auto global = *g_agentPointerPosition;
    if (global.x < monitor->m_position.x - 64.0 || global.y < monitor->m_position.y - 64.0 ||
        global.x > monitor->m_position.x + monitor->m_size.x + 64.0 || global.y > monitor->m_position.y + monitor->m_size.y + 64.0)
        return;

    const auto   now = Time::steadyNow();
    const double ageMs = static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(now - *g_agentPointerUpdated).count());
    const double timeoutMs = static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(indicatorTimeout()).count());
    if (ageMs > timeoutMs + 50.0)
        return;

    const double fade = std::clamp((timeoutMs - ageMs) / 450.0, 0.0, 1.0);
    if (fade <= 0.0)
        return;

    const double scale = std::max(1.0, static_cast<double>(monitor->m_scale));
    const Vector2D tip{
        (global.x - monitor->m_position.x) * scale,
        (global.y - monitor->m_position.y) * scale,
    };

    const bool   clickLike = g_agentPointerAction == "click" || g_agentPointerAction == "doubleclick" || g_agentPointerAction == "double-click" ||
        g_agentPointerAction == "press" || g_agentPointerAction == "down" || g_agentPointerAction == "release" || g_agentPointerAction == "up";
    const double pulse = std::clamp(1.0 - ageMs / (clickLike ? 420.0 : 620.0), 0.0, 1.0);
    const double breathe = 0.5 + 0.5 * std::sin(ageMs / 165.0);
    const Vector2D haloCenter = tip + Vector2D{12.0 * scale, 10.5 * scale};
    const double outerDiameter = (35.0 + 8.0 * pulse + 2.0 * breathe) * scale;
    const double innerDiameter = std::max(1.0, outerDiameter - 9.0 * scale);

    addIndicatorCircle(haloCenter + Vector2D{1.8 * scale, 2.4 * scale}, outerDiameter + 1.5 * scale,
                       CHyprColor(0.0F, 0.0F, 0.0F, static_cast<float>(0.18 * fade)));
    addIndicatorCircle(haloCenter, outerDiameter + 2.0 * scale, CHyprColor(1.0F, 0.96F, 1.0F, static_cast<float>((0.24 + 0.16 * pulse) * fade)));
    addIndicatorCircle(haloCenter, outerDiameter, CHyprColor(0.73F, 0.39F, 0.96F, static_cast<float>((0.38 + 0.22 * pulse) * fade)));
    addIndicatorCircle(haloCenter, innerDiameter, CHyprColor(0.92F, 0.64F, 1.0F, static_cast<float>(0.20 * fade)));

    renderCodexCursorBitmap(tip, scale, fade);
}

void stopIndicatorTimer(SP<CEventLoopTimer>& timer) {
    if (timer && g_pEventLoopManager)
        g_pEventLoopManager->removeTimer(timer);
    timer.reset();
}

void scheduleIndicatorAnimation() {
    if (!g_pEventLoopManager || g_indicatorAnimationTimer)
        return;

    g_indicatorAnimationTimer = makeShared<CEventLoopTimer>(
        std::chrono::milliseconds(33),
        [](SP<CEventLoopTimer> self, void*) {
            if (!g_agentPointerPosition || !g_pEventLoopManager) {
                if (g_pEventLoopManager)
                    g_pEventLoopManager->removeTimer(self);
                if (g_indicatorAnimationTimer.get() == self.get())
                    g_indicatorAnimationTimer.reset();
                return;
            }

            damageAgentIndicator();
            self->updateTimeout(std::chrono::milliseconds(33));
        },
        nullptr);
    g_pEventLoopManager->addTimer(g_indicatorAnimationTimer);
}

void scheduleIndicatorHide() {
    if (!g_pEventLoopManager)
        return;

    stopIndicatorTimer(g_indicatorHideTimer);
    g_indicatorHideTimer = makeShared<CEventLoopTimer>(
        indicatorTimeout(),
        [](SP<CEventLoopTimer> self, void*) {
            damageAgentIndicator();
            g_agentPointerPosition.reset();
            g_agentPointerUpdated.reset();
            g_agentPointerAction.clear();

            if (g_pEventLoopManager)
                g_pEventLoopManager->removeTimer(self);
            if (g_indicatorHideTimer.get() == self.get())
                g_indicatorHideTimer.reset();
            stopIndicatorTimer(g_indicatorAnimationTimer);
        },
        nullptr);
    g_pEventLoopManager->addTimer(g_indicatorHideTimer);
}

void showAgentIndicator(const Vector2D& globalPos, std::string_view action) {
    if (!configBool("show_indicator", true))
        return;

    damageAgentIndicator();
    g_agentPointerPosition = globalPos;
    g_agentPointerUpdated = Time::steadyNow();
    g_agentPointerAction = std::string(action);
    damageAgentIndicator();

    scheduleIndicatorHide();
    scheduleIndicatorAnimation();
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

struct WorkspaceSession {
    PHLWINDOWREF root;
    pid_t        pid = -1;
    std::string  className;
    std::string  initialClassName;
    PHLWORKSPACE targetWorkspace;
};

std::vector<WorkspaceSession> g_workspaceSessions;

CBox windowMainSurfaceGoalBox(const PHLWINDOW& window) {
    if (!window)
        return {};

    return CBox{window->m_realPosition ? window->m_realPosition->goal().x : window->m_position.x,
                window->m_realPosition ? window->m_realPosition->goal().y : window->m_position.y,
                window->m_realSize ? window->m_realSize->goal().x : window->m_size.x,
                window->m_realSize ? window->m_realSize->goal().y : window->m_size.y};
}

bool sameXWaylandClientFamily(const PHLWINDOW& root, const PHLWINDOW& candidate) {
    if (!root || !candidate || root == candidate || !root->m_isX11 || !candidate->m_isX11)
        return false;

    const auto transientFor = candidate->x11TransientFor();
    if (transientFor && transientFor == root)
        return true;

    const auto rootPid = root->getPID();
    const auto candidatePid = candidate->getPID();
    if (rootPid <= 0 || candidatePid != rootPid)
        return false;

    if (!root->m_class.empty() && root->m_class == candidate->m_class)
        return true;
    return !root->m_initialClass.empty() && root->m_initialClass == candidate->m_initialClass;
}

PHLWINDOW xwaylandRelatedWindowAt(const PHLWINDOW& root, const Vector2D& globalPos) {
    if (!g_pCompositor || !root || !root->m_isX11)
        return root;

    PHLWINDOW best = root;
    double    bestArea = std::numeric_limits<double>::infinity();

    for (const auto& candidate : g_pCompositor->m_windows) {
        if (!candidate || !candidate->m_isMapped || candidate->isHidden() || !sameXWaylandClientFamily(root, candidate))
            continue;

        const auto box = windowMainSurfaceGoalBox(candidate);
        if (!box.containsPoint(globalPos))
            continue;

        const double area = std::max(1.0, box.w) * std::max(1.0, box.h);
        if (area < bestArea) {
            best = candidate;
            bestArea = area;
        }
    }

    return best;
}

PHLWORKSPACE workspaceSessionTarget(const WorkspaceSession& session) {
    const auto root = session.root.lock();
    if (root && root->m_workspace)
        return root->m_workspace;
    return session.targetWorkspace;
}

bool workspaceSessionMatchesWindow(const WorkspaceSession& session, const PHLWINDOW& window) {
    if (!window || !window->m_isMapped)
        return false;

    const auto root = session.root.lock();
    if (root && window == root)
        return true;
    if (root && sameXWaylandClientFamily(root, window))
        return true;

    if (!window->m_isX11 || session.pid <= 0 || window->getPID() != session.pid)
        return false;
    if (!session.className.empty() && window->m_class == session.className)
        return true;
    return !session.initialClassName.empty() && window->m_initialClass == session.initialClassName;
}

void restackRelatedWindowWithRoot(const PHLWINDOW& root, const PHLWINDOW& window) {
    if (!g_pCompositor || !root || !window || root == window)
        return;

    auto& windows = g_pCompositor->m_windows;
    auto  windowIt = std::find(windows.begin(), windows.end(), window);
    if (windowIt == windows.end())
        return;

    const auto moved = *windowIt;
    windows.erase(windowIt);

    auto rootIt = std::find(windows.begin(), windows.end(), root);
    if (rootIt == windows.end()) {
        windows.push_back(moved);
        return;
    }

    windows.insert(std::next(rootIt), moved);

    if (g_pHyprRenderer) {
        g_pHyprRenderer->damageWindow(root);
        g_pHyprRenderer->damageWindow(window);
    }
}

void scheduleRelatedWindowRestack(const PHLWINDOW& root, const PHLWINDOW& window) {
    if (!g_pEventLoopManager || !root || !window || root == window)
        return;

    PHLWINDOWREF rootRef{root};
    PHLWINDOWREF windowRef{window};
    auto         timer = makeShared<CEventLoopTimer>(
        std::chrono::milliseconds(75),
        [rootRef, windowRef](SP<CEventLoopTimer> self, void*) mutable {
            restackRelatedWindowWithRoot(rootRef.lock(), windowRef.lock());

            if (g_pEventLoopManager)
                g_pEventLoopManager->removeTimer(self);

            g_workspaceRestackTimers.erase(
                std::remove_if(g_workspaceRestackTimers.begin(), g_workspaceRestackTimers.end(), [&self](const auto& item) { return item.get() == self.get(); }),
                g_workspaceRestackTimers.end());
        },
        nullptr);

    g_workspaceRestackTimers.push_back(timer);
    g_pEventLoopManager->addTimer(timer);
}

void moveRelatedWindowToSessionWorkspace(WorkspaceSession& session, const PHLWINDOW& window) {
    if (!g_pCompositor || !window || !window->m_isMapped)
        return;

    const auto root = session.root.lock();
    if (root && window == root)
        return;

    const auto targetWorkspace = workspaceSessionTarget(session);
    if (!targetWorkspace || targetWorkspace->inert())
        return;

    if (window->m_workspace != targetWorkspace)
        g_pCompositor->moveWindowToWorkspaceSafe(window, targetWorkspace);

    if (root) {
        restackRelatedWindowWithRoot(root, window);
        scheduleRelatedWindowRestack(root, window);
    }
}

void syncWorkspaceSession(WorkspaceSession& session) {
    if (!g_pCompositor)
        return;

    for (const auto& window : g_pCompositor->m_windows) {
        if (workspaceSessionMatchesWindow(session, window))
            moveRelatedWindowToSessionWorkspace(session, window);
    }
}

void handleWorkspaceSessionWindowOpen(const PHLWINDOW& window) {
    if (!window || g_workspaceSessions.empty())
        return;

    for (auto& session : g_workspaceSessions) {
        if (workspaceSessionMatchesWindow(session, window))
            moveRelatedWindowToSessionWorkspace(session, window);
    }
}

std::optional<TargetSurface> resolveTargetSurface(const std::string& targetRegex, const Vector2D& globalPos) {
    if (!g_pCompositor)
        return std::nullopt;

    auto window = g_pCompositor->getWindowByRegex(targetRegex);
    if (!window || !window->m_isMapped)
        return std::nullopt;

    window = xwaylandRelatedWindowAt(window, globalPos);

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
    bool                   restored = false;

    PointerFocusRestore() {
        if (!g_pSeatManager)
            return;
        previousSurface = g_pSeatManager->m_state.pointerFocus.lock();
        previousLocal = g_pSeatManager->m_lastLocalCoords;
    }

    ~PointerFocusRestore() {
        restoreNow(false);
    }

    void restoreNow(bool resetCurrentXWaylandFocus) {
        if (restored || !g_pSeatManager)
            return;

        if (resetCurrentXWaylandFocus) {
            g_pSeatManager->m_state.pointerFocus.reset();
            g_pSeatManager->m_state.pointerFocusResource.reset();
        }

        restored = true;
        g_pSeatManager->setPointerFocus(previousSurface, previousLocal);
        g_pSeatManager->sendPointerFrame();
    }

    void restoreLater(Time::steady_dur delay, bool resetCurrentXWaylandFocus) {
        if (restored || !g_pEventLoopManager)
            return;

        auto previous = previousSurface;
        auto local = previousLocal;
        auto timer = makeShared<CEventLoopTimer>(
            delay,
            [previous, local, resetCurrentXWaylandFocus](SP<CEventLoopTimer> self, void*) {
                if (g_pSeatManager) {
                    if (resetCurrentXWaylandFocus) {
                        g_pSeatManager->m_state.pointerFocus.reset();
                        g_pSeatManager->m_state.pointerFocusResource.reset();
                    }
                    g_pSeatManager->setPointerFocus(previous, local);
                    g_pSeatManager->sendPointerFrame();
                }

                if (g_pEventLoopManager)
                    g_pEventLoopManager->removeTimer(self);

                g_pointerRestoreTimers.erase(
                    std::remove_if(g_pointerRestoreTimers.begin(), g_pointerRestoreTimers.end(), [&self](const auto& item) { return item.get() == self.get(); }),
                    g_pointerRestoreTimers.end());
            },
            nullptr);

        restored = true;
        g_pointerRestoreTimers.push_back(timer);
        g_pEventLoopManager->addTimer(timer);
    }

    void restoreForTarget(const TargetSurface& target) {
        if (!g_pSeatManager)
            return;

        if (target.window && target.window->m_isX11) {
            restoreLater(std::chrono::milliseconds(75), true);
            return;
        }

        restoreNow(false);
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

void activateXWaylandTarget(const TargetSurface& target) {
    if (!target.window || !target.window->m_isX11 || !target.window->m_xwaylandSurface)
        return;

    target.window->m_xwaylandSurface->activate(true);
}

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
        return {.success = false, .error = "hypr-agent-protal pointer dispatch is disabled"};
    if (!g_pSeatManager)
        return {.success = false, .error = "seat manager is not ready"};

    const auto parts = splitCsv(args);
    if (parts.size() < 4)
        return {.success = false, .error = "usage: hypr-agent-protal:pointer <window-regex>,<global-x>,<global-y>,<move|click|press|release>[,<button>]"};

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
    activateXWaylandTarget(*target);
    g_pSeatManager->setPointerFocus(target->surface, target->local);
    g_pSeatManager->sendPointerMotion(nowMs(), target->local);

    if (action == "move" || action == "motion") {
        g_pSeatManager->sendPointerFrame();
        showAgentIndicator(Vector2D{*x, *y}, action);
        restore.restoreForTarget(*target);
        return {.success = true};
    }

    if (action == "scroll") {
        const auto dy = parts.size() >= 5 ? parseDouble(parts[4]) : std::optional<double>{1.0};
        const auto dx = parts.size() >= 6 ? parseDouble(parts[5]) : std::optional<double>{0.0};
        if (!dx || !dy)
            return {.success = false, .error = "scroll dx/dy must be finite numbers"};
        sendPointerScroll(*dx, *dy);
        showAgentIndicator(Vector2D{*x, *y}, action);
        restore.restoreForTarget(*target);
        return {.success = true};
    }

    if (action == "click") {
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_PRESSED);
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_RELEASED);
        g_pSeatManager->sendPointerFrame();
        showAgentIndicator(Vector2D{*x, *y}, action);
        restore.restoreForTarget(*target);
        return {.success = true};
    }

    if (action == "doubleclick" || action == "double-click") {
        for (int i = 0; i < 2; ++i) {
            g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_PRESSED);
            g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_RELEASED);
        }
        g_pSeatManager->sendPointerFrame();
        showAgentIndicator(Vector2D{*x, *y}, action);
        restore.restoreForTarget(*target);
        return {.success = true};
    }

    if (action == "press" || action == "down") {
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_PRESSED);
        g_pSeatManager->sendPointerFrame();
        showAgentIndicator(Vector2D{*x, *y}, action);
        restore.restoreForTarget(*target);
        return {.success = true};
    }

    if (action == "release" || action == "up") {
        g_pSeatManager->sendPointerButton(nowMs(), *button, WL_POINTER_BUTTON_STATE_RELEASED);
        g_pSeatManager->sendPointerFrame();
        showAgentIndicator(Vector2D{*x, *y}, action);
        restore.restoreForTarget(*target);
        return {.success = true};
    }

    return {.success = false, .error = "unknown pointer action"};
}

SDispatchResult dispatchKeyboard(const std::string& args) {
    if (!configBool("allow_keyboard", true))
        return {.success = false, .error = "hypr-agent-protal keyboard dispatch is disabled"};
    if (!g_pSeatManager)
        return {.success = false, .error = "seat manager is not ready"};

    const auto parts = splitCsv(args);
    if (parts.size() < 3)
        return {.success = false, .error = "usage: hypr-agent-protal:keyboard <window-regex>,<tap|press|release>,<key>[,<modifiers>][,<global-x>,<global-y>]"};

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
    activateXWaylandTarget(*target);
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
        return {.success = false, .error = "hypr-agent-protal screenshot dispatch is disabled"};

    const auto parts = splitCsv(args);
    const auto path = parts.empty() ? std::string{} : trim(parts[0]);
    if (path.empty())
        return {.success = false, .error = "usage: hypr-agent-protal:screenshot <output-session-json-path>[,<window-regex>]"};

    const auto target = parts.size() >= 2 ? trim(parts[1]) : std::string{};
    const auto result = hypr_agent_protal::captureScreenshotSession(std::filesystem::path(path), target);
    if (!result.success)
        return {.success = false, .error = result.error};
    return {.success = true};
}

SDispatchResult dispatchSession(const std::string& args) {
    if (!configBool("allow_session", true))
        return {.success = false, .error = "hypr-agent-protal session dispatch is disabled"};
    if (!g_pCompositor)
        return {.success = false, .error = "compositor is not ready"};

    const auto parts = splitCsv(args);
    if (parts.empty())
        return {.success = false, .error = "usage: hypr-agent-protal:session <begin|sync|end>[,<window-regex>]"};

    const auto action = lower(parts[0]);
    if (action == "begin") {
        if (parts.size() < 2)
            return {.success = false, .error = "session begin requires a target window selector"};

        const auto root = g_pCompositor->getWindowByRegex(parts[1]);
        if (!root || !root->m_isMapped)
            return {.success = false, .error = "target window not found"};

        auto existing = std::find_if(g_workspaceSessions.begin(), g_workspaceSessions.end(), [&root](const auto& session) { return session.root.lock() == root; });
        if (existing == g_workspaceSessions.end()) {
            g_workspaceSessions.push_back({
                .root = PHLWINDOWREF{root},
                .pid = root->getPID(),
                .className = root->m_class,
                .initialClassName = root->m_initialClass,
                .targetWorkspace = root->m_workspace,
            });
            existing = std::prev(g_workspaceSessions.end());
        } else {
            existing->targetWorkspace = root->m_workspace;
        }

        syncWorkspaceSession(*existing);
        return {.success = true};
    }

    if (action == "sync") {
        if (parts.size() >= 2) {
            const auto root = g_pCompositor->getWindowByRegex(parts[1]);
            if (!root)
                return {.success = false, .error = "target window not found"};
            auto existing = std::find_if(g_workspaceSessions.begin(), g_workspaceSessions.end(), [&root](const auto& session) { return session.root.lock() == root; });
            if (existing == g_workspaceSessions.end())
                return {.success = false, .error = "target session not found"};
            syncWorkspaceSession(*existing);
        } else {
            for (auto& session : g_workspaceSessions)
                syncWorkspaceSession(session);
        }
        return {.success = true};
    }

    if (action == "end") {
        if (parts.size() >= 2) {
            const auto root = g_pCompositor->getWindowByRegex(parts[1]);
            if (!root)
                return {.success = false, .error = "target window not found"};

            const auto oldSize = g_workspaceSessions.size();
            g_workspaceSessions.erase(std::remove_if(g_workspaceSessions.begin(), g_workspaceSessions.end(), [&root](auto& session) { return session.root.lock() == root; }),
                                      g_workspaceSessions.end());
            if (g_workspaceSessions.size() == oldSize)
                return {.success = false, .error = "target session not found"};
        } else {
            g_workspaceSessions.clear();
        }
        return {.success = true};
    }

    return {.success = false, .error = "unknown session action"};
}

} // namespace

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    g_pluginHandle = handle;

    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hypr-agent-protal:allow_pointer", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hypr-agent-protal:allow_keyboard", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hypr-agent-protal:allow_screenshot", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hypr-agent-protal:allow_session", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hypr-agent-protal:show_indicator", Hyprlang::INT{1});
    HyprlandAPI::addConfigValue(g_pluginHandle, "plugin:hypr-agent-protal:indicator_timeout_ms", Hyprlang::INT{2600});

    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hypr-agent-protal:pointer", dispatchPointer);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hypr-agent-protal:keyboard", dispatchKeyboard);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hypr-agent-protal:screenshot", dispatchScreenshot);
    HyprlandAPI::addDispatcherV2(g_pluginHandle, "hypr-agent-protal:session", dispatchSession);
    g_windowOpenListener = Event::bus()->m_events.window.open.listen([](PHLWINDOW window) { handleWorkspaceSessionWindowOpen(window); });
    g_renderStageListener = Event::bus()->m_events.render.stage.listen([](eRenderStage stage) { renderAgentIndicator(stage); });
    HyprlandAPI::reloadConfig();

    return {
        .name = "hypr-agent-protal",
        .description = "Background screenshot, pointer, keyboard, workspace guard, and visible agent pointer primitives for Hyprland agents",
        .author = "wilf",
        .version = "0.2.3",
    };
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_workspaceSessions.clear();
    g_windowOpenListener.reset();
    g_renderStageListener.reset();

    if (g_pEventLoopManager) {
        for (const auto& timer : g_pointerRestoreTimers)
            g_pEventLoopManager->removeTimer(timer);
        for (const auto& timer : g_workspaceRestackTimers)
            g_pEventLoopManager->removeTimer(timer);
        if (g_indicatorHideTimer)
            g_pEventLoopManager->removeTimer(g_indicatorHideTimer);
        if (g_indicatorAnimationTimer)
            g_pEventLoopManager->removeTimer(g_indicatorAnimationTimer);
    }
    g_pointerRestoreTimers.clear();
    g_workspaceRestackTimers.clear();
    g_indicatorHideTimer.reset();
    g_indicatorAnimationTimer.reset();
    g_agentPointerPosition.reset();
    g_agentPointerUpdated.reset();
    g_agentPointerAction.clear();
    g_pluginHandle = nullptr;
}
