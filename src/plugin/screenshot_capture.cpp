#include "plugin/screenshot_capture.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

#define private public
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/Renderer.hpp>
#undef private

#include <GLES2/gl2.h>
#include <drm_fourcc.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace hyprcum {
namespace {

using Json = nlohmann::ordered_json;

constexpr int         MAX_RGBA_READBACK_DIMENSION = 32768;
constexpr std::size_t MAX_RGBA_READBACK_BYTES = 512ULL * 1024ULL * 1024ULL;
constexpr std::size_t RGBA_BYTES_PER_PIXEL = 4;

struct RgbaReadbackRegion {
    int         outputCropX = 0;
    int         outputCropTopY = 0;
    int         outputWidth = 0;
    int         outputHeight = 0;
    int         srcX = 0;
    int         srcTopY = 0;
    int         srcWidth = 0;
    int         srcHeight = 0;
    int         dstX = 0;
    int         dstY = 0;
    std::size_t outputBytes = 0;
    std::size_t sourceBytes = 0;
};

struct RgbaReadback {
    std::vector<unsigned char> pixels;
    int                        width = 0;
    int                        height = 0;
};

std::string pointerId(const void* ptr) {
    std::ostringstream out;
    out << std::hex << reinterpret_cast<std::uintptr_t>(ptr);
    return out.str();
}

std::string boundedString(const std::string& value, std::size_t maxBytes) {
    if (value.size() <= maxBytes)
        return value;
    return value.substr(0, maxBytes);
}

int positiveRoundedIntFromDouble(double value) {
    if (!std::isfinite(value) || value <= 0.0)
        return 1;
    if (value >= static_cast<double>(std::numeric_limits<int>::max()))
        return std::numeric_limits<int>::max();
    return std::max(1, static_cast<int>(std::lround(value)));
}

int clampToFramebuffer(std::int64_t value, int framebufferExtent) {
    if (value <= 0)
        return 0;
    if (value >= framebufferExtent)
        return framebufferExtent;
    return static_cast<int>(value);
}

bool checkedRgbaByteSize(int width, int height, std::size_t& bytes) {
    bytes = 0;
    if (width <= 0 || height <= 0 || width > MAX_RGBA_READBACK_DIMENSION || height > MAX_RGBA_READBACK_DIMENSION)
        return false;

    const auto w = static_cast<std::size_t>(width);
    const auto h = static_cast<std::size_t>(height);
    if (w > std::numeric_limits<std::size_t>::max() / h)
        return false;

    const auto pixels = w * h;
    if (pixels > std::numeric_limits<std::size_t>::max() / RGBA_BYTES_PER_PIXEL)
        return false;

    bytes = pixels * RGBA_BYTES_PER_PIXEL;
    return bytes <= MAX_RGBA_READBACK_BYTES;
}

bool prepareRgbaReadbackRegion(int framebufferWidth,
                               int framebufferHeight,
                               int cropX,
                               int cropTopY,
                               int cropWidth,
                               int cropHeight,
                               RgbaReadbackRegion& region) {
    region = {};
    if (framebufferWidth <= 0 || framebufferHeight <= 0 || cropWidth <= 0 || cropHeight <= 0)
        return false;

    const auto cropLeft = static_cast<std::int64_t>(cropX);
    const auto cropTop = static_cast<std::int64_t>(cropTopY);
    const auto cropRight = cropLeft + static_cast<std::int64_t>(cropWidth);
    const auto cropBottom = cropTop + static_cast<std::int64_t>(cropHeight);

    region.srcX = clampToFramebuffer(cropLeft, framebufferWidth);
    region.srcTopY = clampToFramebuffer(cropTop, framebufferHeight);
    const int srcRight = clampToFramebuffer(cropRight, framebufferWidth);
    const int srcBottom = clampToFramebuffer(cropBottom, framebufferHeight);
    region.srcWidth = srcRight - region.srcX;
    region.srcHeight = srcBottom - region.srcTopY;

    if (!checkedRgbaByteSize(cropWidth, cropHeight, region.outputBytes))
        return false;
    if (region.srcWidth > 0 && region.srcHeight > 0 && !checkedRgbaByteSize(region.srcWidth, region.srcHeight, region.sourceBytes))
        return false;

    region.outputCropX = cropX;
    region.outputCropTopY = cropTopY;
    region.outputWidth = cropWidth;
    region.outputHeight = cropHeight;
    region.dstX = region.srcX - cropX;
    region.dstY = region.srcTopY - cropTopY;
    return true;
}

RgbaReadback readRgbaFramebufferRegion(CFramebuffer& framebuffer, int cropX, int cropTopY, int cropWidth, int cropHeight) {
    const int framebufferWidth = positiveRoundedIntFromDouble(framebuffer.m_size.x);
    const int framebufferHeight = positiveRoundedIntFromDouble(framebuffer.m_size.y);
    RgbaReadbackRegion region;
    if (!prepareRgbaReadbackRegion(framebufferWidth, framebufferHeight, cropX, cropTopY, cropWidth, cropHeight, region))
        return {};

    RgbaReadback readback;
    readback.width = region.outputWidth;
    readback.height = region.outputHeight;
    readback.pixels.assign(region.outputBytes, 0);

    if (region.srcWidth <= 0 || region.srcHeight <= 0)
        return readback;

    std::vector<unsigned char> rows(region.sourceBytes);
    GLint previousReadFramebuffer = 0;
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &previousReadFramebuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer.getFBID());
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    const int readY = framebufferHeight - region.srcTopY - region.srcHeight;
    glReadPixels(region.srcX, readY, region.srcWidth, region.srcHeight, GL_RGBA, GL_UNSIGNED_BYTE, rows.data());
    glBindFramebuffer(GL_READ_FRAMEBUFFER, previousReadFramebuffer);

    for (int y = 0; y < region.srcHeight; ++y) {
        const auto* src = rows.data() + static_cast<std::size_t>(y) * region.srcWidth * RGBA_BYTES_PER_PIXEL;
        auto*       dst = readback.pixels.data() +
            (static_cast<std::size_t>(region.dstY + y) * region.outputWidth + region.dstX) * RGBA_BYTES_PER_PIXEL;
        std::copy(src, src + static_cast<std::size_t>(region.srcWidth) * RGBA_BYTES_PER_PIXEL, dst);
    }

    return readback;
}

bool writeFileExclusive(const std::filesystem::path& path, const std::vector<unsigned char>& bytes) {
    const auto native = path.string();
    const int  fd = open(native.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0)
        return false;

    const auto* data = reinterpret_cast<const char*>(bytes.data());
    std::size_t written = 0;
    while (written < bytes.size()) {
        const ssize_t chunk = write(fd, data + written, bytes.size() - written);
        if (chunk < 0) {
            if (errno == EINTR)
                continue;
            close(fd);
            return false;
        }
        if (chunk == 0) {
            close(fd);
            return false;
        }
        written += static_cast<std::size_t>(chunk);
    }

    return close(fd) == 0;
}

bool renderMonitorArtifact(const PHLMONITOR& monitor, const Time::steady_tp& frozenTime, const std::filesystem::path& path, int& width, int& height) {
    if (!monitor || !monitor->m_activeWorkspace || !g_pHyprRenderer || !g_pHyprOpenGL)
        return false;

    width = positiveRoundedIntFromDouble(monitor->m_pixelSize.x);
    height = positiveRoundedIntFromDouble(monitor->m_pixelSize.y);
    std::size_t framebufferBytes = 0;
    if (!checkedRgbaByteSize(width, height, framebufferBytes))
        return false;

    CFramebuffer framebuffer;
    const auto   drmFormat = monitor->m_output && monitor->m_output->state ? monitor->m_output->state->state().drmFormat : DRM_FORMAT_ABGR8888;
    if (!framebuffer.alloc(width, height, drmFormat) && !framebuffer.alloc(width, height, DRM_FORMAT_ABGR8888))
        return false;

    const bool previousBlockFeedback = g_pHyprRenderer->m_bBlockSurfaceFeedback;
    const bool previousBlockShader = g_pHyprOpenGL->m_renderData.blockScreenShader;
    CRegion    fakeDamage{0, 0, width, height};

    g_pHyprRenderer->makeEGLCurrent();
    g_pHyprRenderer->m_bBlockSurfaceFeedback = true;
    if (!g_pHyprRenderer->beginRender(monitor, fakeDamage, RENDER_MODE_FULL_FAKE, nullptr, &framebuffer)) {
        g_pHyprRenderer->m_bBlockSurfaceFeedback = previousBlockFeedback;
        return false;
    }

    g_pHyprOpenGL->clear(CHyprColor{0.0, 0.0, 0.0, 1.0});
    g_pHyprRenderer->renderWorkspace(monitor, monitor->m_activeWorkspace, frozenTime, CBox{0, 0, static_cast<double>(width), static_cast<double>(height)});
    g_pHyprOpenGL->m_renderData.blockScreenShader = true;
    g_pHyprRenderer->endRender();
    g_pHyprOpenGL->m_renderData.blockScreenShader = previousBlockShader;
    g_pHyprRenderer->m_bBlockSurfaceFeedback = previousBlockFeedback;

    const auto readback = readRgbaFramebufferRegion(framebuffer, 0, 0, width, height);
    return !readback.pixels.empty() && writeFileExclusive(path, readback.pixels);
}

Json rectJson(const CBox& box) {
    return Json{{"x", box.x}, {"y", box.y}, {"width", box.w}, {"height", box.h}};
}

Json monitorRectJson(const PHLMONITOR& monitor) {
    return Json{{"x", monitor->m_position.x}, {"y", monitor->m_position.y}, {"width", monitor->m_size.x}, {"height", monitor->m_size.y}};
}

bool isWindowVisible(const PHLWINDOW& window) {
    if (!window || !window->m_isMapped || window->isHidden() || !window->m_workspace)
        return false;
    if (g_pHyprRenderer)
        return g_pHyprRenderer->shouldRenderWindow(window);
    return window->m_pinned || window->m_workspace->isVisible();
}

Json windowJson(const PHLWINDOW& window) {
    CBox visible{window->m_realPosition ? window->m_realPosition->goal().x : window->m_position.x,
                 window->m_realPosition ? window->m_realPosition->goal().y : window->m_position.y,
                 window->m_realSize ? window->m_realSize->goal().x : window->m_size.x,
                 window->m_realSize ? window->m_realSize->goal().y : window->m_size.y};
    const auto full = window->getFullWindowBoundingBox();

    return Json{
        {"address", "0x" + pointerId(window.get())},
        {"title", boundedString(window->m_title, 4096)},
        {"class", boundedString(window->m_class, 4096)},
        {"initialClass", boundedString(window->m_initialClass, 4096)},
        {"visible", isWindowVisible(window)},
        {"xwayland", window->m_isX11},
        {"geometry", rectJson(visible)},
        {"fullGeometry", rectJson(full)},
    };
}

std::string sessionId() {
    const auto now = Time::millis(Time::steadyNow());
    std::ostringstream out;
    out << std::hex << now << "-" << getpid();
    return out.str();
}

} // namespace

ScreenshotResult captureScreenshotSession(const std::filesystem::path& outputJsonPath) {
    if (!g_pCompositor || !g_pHyprRenderer || !g_pHyprOpenGL)
        return {.success = false, .error = "Hyprland renderer is not ready"};
    if (outputJsonPath.empty())
        return {.success = false, .error = "missing output json path"};

    std::error_code ec;
    const auto      parent = outputJsonPath.parent_path();
    if (!parent.empty())
        std::filesystem::create_directories(parent, ec);
    if (ec)
        return {.success = false, .error = "failed to create output directory: " + ec.message()};

    const auto id = sessionId();
    const auto artifactRoot = parent / id;
    std::filesystem::create_directories(artifactRoot, ec);
    if (ec)
        return {.success = false, .error = "failed to create artifact directory: " + ec.message()};

    Json root;
    root["id"] = id;
    root["monitors"] = Json::array();
    root["windows"] = Json::array();

    if (g_pInputManager) {
        const auto cursor = g_pInputManager->getMouseCoordsInternal();
        root["cursorPosition"] = Json{{"x", cursor.x}, {"y", cursor.y}};
    }

    const auto frozenTime = Time::steadyNow();
    int        monitorIndex = 0;
    for (const auto& monitor : g_pCompositor->m_monitors) {
        if (!monitor)
            continue;

        int        width = 0;
        int        height = 0;
        const auto artifactPath = artifactRoot / ("monitor-" + std::to_string(monitorIndex) + ".rgba");
        const bool rendered = renderMonitorArtifact(monitor, frozenTime, artifactPath, width, height);

        root["monitors"].push_back(Json{
            {"name", boundedString(monitor->m_name, 4096)},
            {"geometry", monitorRectJson(monitor)},
            {"scale", monitor->m_scale},
            {"transform", static_cast<int>(monitor->m_transform)},
            {"artifactPath", rendered ? artifactPath.string() : ""},
            {"artifactWidth", rendered ? width : 0},
            {"artifactHeight", rendered ? height : 0},
            {"artifactTopDown", true},
        });
        ++monitorIndex;
    }

    for (const auto& window : g_pCompositor->m_windows) {
        if (!window || !window->m_isMapped)
            continue;
        root["windows"].push_back(windowJson(window));
    }

    const auto serialized = root.dump(2, ' ', false, Json::error_handler_t::replace);
    std::vector<unsigned char> bytes(serialized.begin(), serialized.end());
    if (!writeFileExclusive(outputJsonPath, bytes))
        return {.success = false, .error = "failed to write output json"};

    return {.success = true};
}

} // namespace hyprcum
