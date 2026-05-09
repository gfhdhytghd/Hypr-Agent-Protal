#include "plugin/screenshot_capture.hpp"

#include <hyprland/src/config/ConfigValue.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>

#define private public
#define protected public
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/view/WLSurface.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/gl/GLFramebuffer.hpp>
#undef protected
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

using Render::GL::g_pHyprOpenGL;

namespace hypr_agent_protal {
namespace {

using Json = nlohmann::ordered_json;

constexpr int         MAX_RGBA_READBACK_DIMENSION = 32768;
constexpr std::size_t MAX_RGBA_READBACK_BYTES = 512ULL * 1024ULL * 1024ULL;
constexpr std::size_t RGBA_BYTES_PER_PIXEL = 4;
constexpr int         WINDOW_SHADOW_MAX_RGB = 64;
constexpr int         WINDOW_SHADOW_MAX_ALPHA = 249;

struct ShadowColorBytes {
    int r = 0;
    int g = 0;
    int b = 0;
    int a = 0;
};

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
    int                        cropX = 0;
    int                        cropTopY = 0;
    int                        width = 0;
    int                        height = 0;
};

struct PixelBounds {
    int x = 0;
    int y = 0;
    int width = 0;
    int height = 0;
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

int positiveIntFromDouble(double value) {
    if (!std::isfinite(value) || value <= 0.0)
        return 1;
    if (value >= static_cast<double>(std::numeric_limits<int>::max()))
        return std::numeric_limits<int>::max();
    return std::max(1, static_cast<int>(value));
}

int clampedIntFromDouble(double value) {
    if (!std::isfinite(value))
        return value < 0.0 ? std::numeric_limits<int>::min() : std::numeric_limits<int>::max();
    if (value <= static_cast<double>(std::numeric_limits<int>::min()))
        return std::numeric_limits<int>::min();
    if (value >= static_cast<double>(std::numeric_limits<int>::max()))
        return std::numeric_limits<int>::max();
    return static_cast<int>(value);
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

RgbaReadback readRgbaFramebufferRegion(Render::GL::CGLFramebuffer& framebuffer, int cropX, int cropTopY, int cropWidth, int cropHeight) {
    const int framebufferWidth = positiveRoundedIntFromDouble(framebuffer.m_size.x);
    const int framebufferHeight = positiveRoundedIntFromDouble(framebuffer.m_size.y);
    RgbaReadbackRegion region;
    if (!prepareRgbaReadbackRegion(framebufferWidth, framebufferHeight, cropX, cropTopY, cropWidth, cropHeight, region))
        return {};

    RgbaReadback readback;
    readback.cropX = region.outputCropX;
    readback.cropTopY = region.outputCropTopY;
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

unsigned char alphaAt(const RgbaReadback& readback, int x, int y) {
    const auto i = (static_cast<std::size_t>(y) * readback.width + x) * 4U + 3U;
    return readback.pixels[i];
}

bool findAlphaBounds(const RgbaReadback& readback, PixelBounds& bounds) {
    if (readback.width <= 0 || readback.height <= 0 || readback.pixels.empty())
        return false;

    int minX = readback.width;
    int minY = readback.height;
    int maxX = -1;
    int maxY = -1;

    for (int y = 0; y < readback.height; ++y) {
        for (int x = 0; x < readback.width; ++x) {
            if (alphaAt(readback, x, y) == 0)
                continue;
            minX = std::min(minX, x);
            minY = std::min(minY, y);
            maxX = std::max(maxX, x);
            maxY = std::max(maxY, y);
        }
    }

    if (maxX < minX || maxY < minY)
        return false;

    bounds = {.x = minX, .y = minY, .width = maxX - minX + 1, .height = maxY - minY + 1};
    return true;
}

RgbaReadback cropReadback(const RgbaReadback& readback, const PixelBounds& bounds) {
    if (bounds.width <= 0 || bounds.height <= 0 || readback.pixels.empty())
        return {};

    std::size_t pixelBytes = 0;
    if (!checkedRgbaByteSize(bounds.width, bounds.height, pixelBytes))
        return {};

    RgbaReadback cropped;
    cropped.cropX = readback.cropX + bounds.x;
    cropped.cropTopY = readback.cropTopY + bounds.y;
    cropped.width = bounds.width;
    cropped.height = bounds.height;
    cropped.pixels.assign(pixelBytes, 0);

    const std::size_t rowBytes = static_cast<std::size_t>(bounds.width) * RGBA_BYTES_PER_PIXEL;
    for (int y = 0; y < bounds.height; ++y) {
        const auto* src = readback.pixels.data() + (static_cast<std::size_t>(bounds.y + y) * readback.width + bounds.x) * RGBA_BYTES_PER_PIXEL;
        auto*       dst = cropped.pixels.data() + static_cast<std::size_t>(y) * rowBytes;
        std::copy(src, src + rowBytes, dst);
    }

    return cropped;
}

void unpremultiplyAlpha(RgbaReadback& readback) {
    if (readback.pixels.empty())
        return;

    for (std::size_t i = 0; i + 3 < readback.pixels.size(); i += 4U) {
        const auto alpha = readback.pixels[i + 3];
        if (alpha == 0) {
            readback.pixels[i] = 0;
            readback.pixels[i + 1] = 0;
            readback.pixels[i + 2] = 0;
            continue;
        }
        if (alpha == 255)
            continue;

        for (int channel = 0; channel < 3; ++channel) {
            const int straight = (static_cast<int>(readback.pixels[i + channel]) * 255 + alpha / 2) / alpha;
            readback.pixels[i + channel] = static_cast<unsigned char>(std::min(255, straight));
        }
    }
}

class FullSurfaceVisibleRegionOverride {
  public:
    explicit FullSurfaceVisibleRegionOverride(const PHLWINDOW& window) {
        if (!window || !window->wlSurface() || !window->wlSurface()->resource())
            return;

        window->wlSurface()->resource()->breadthfirst(
            [this](SP<CWLSurfaceResource> resource, const Vector2D&, void*) {
                auto surface = Desktop::View::CWLSurface::fromResource(resource);
                if (!surface)
                    return;

                m_records.push_back({.surface = surface, .visibleRegion = surface->m_visibleRegion});

                const int width = std::max(1, static_cast<int>(std::lround(resource->m_current.bufferSize.x > 0 ? resource->m_current.bufferSize.x :
                                                                                                                 resource->m_current.size.x)));
                const int height = std::max(1, static_cast<int>(std::lround(resource->m_current.bufferSize.y > 0 ? resource->m_current.bufferSize.y :
                                                                                                                   resource->m_current.size.y)));
                surface->m_visibleRegion = CRegion{0, 0, width, height};
            },
            nullptr);
    }

    ~FullSurfaceVisibleRegionOverride() {
        for (auto& record : m_records) {
            if (record.surface)
                record.surface->m_visibleRegion = record.visibleRegion;
        }
    }

    FullSurfaceVisibleRegionOverride(const FullSurfaceVisibleRegionOverride&) = delete;
    FullSurfaceVisibleRegionOverride& operator=(const FullSurfaceVisibleRegionOverride&) = delete;

  private:
    struct Record {
        SP<Desktop::View::CWLSurface> surface;
        CRegion                      visibleRegion;
    };

    std::vector<Record> m_records;
};

CBox renderedWindowBox(const PHLWINDOW& window, CBox box) {
    if (window->m_workspace && !window->m_pinned)
        box.translate(window->m_workspace->m_renderOffset->value());
    box.translate(window->m_floatingOffset);
    return box;
}

CBox renderedWindowGoalMainSurfaceBox(const PHLWINDOW& window) {
    if (!window || !window->m_realPosition || !window->m_realSize)
        return {};

    CBox box{window->m_realPosition->goal().x, window->m_realPosition->goal().y, window->m_realSize->goal().x, window->m_realSize->goal().y};
    return renderedWindowBox(window, box);
}

CBox inputWindowBox(const PHLWINDOW& window, CBox renderedBox) {
    if (window->m_workspace && !window->m_pinned)
        renderedBox.translate(-window->m_workspace->m_renderOffset->value());
    renderedBox.translate(-window->m_floatingOffset);
    return renderedBox;
}

class WindowAnimationGoalOverride {
  public:
    explicit WindowAnimationGoalOverride(const PHLWINDOW& window) : m_window(window) {
        if (!m_window || !m_window->m_realPosition || !m_window->m_realSize)
            return;

        m_position = m_window->m_realPosition->value();
        m_size = m_window->m_realSize->value();
        m_active = true;
        setPositionOffset({});
    }

    void setPositionOffset(const Vector2D& offset) {
        if (!m_active || !m_window || !m_window->m_realPosition || !m_window->m_realSize)
            return;

        m_window->m_realPosition->value() = m_window->m_realPosition->goal() + offset;
        m_window->m_realSize->value() = m_window->m_realSize->goal();
        m_window->updateWindowDecos();
    }

    ~WindowAnimationGoalOverride() {
        if (!m_active || !m_window || !m_window->m_realPosition || !m_window->m_realSize)
            return;

        m_window->m_realPosition->value() = m_position;
        m_window->m_realSize->value() = m_size;
        m_window->updateWindowDecos();
    }

    WindowAnimationGoalOverride(const WindowAnimationGoalOverride&) = delete;
    WindowAnimationGoalOverride& operator=(const WindowAnimationGoalOverride&) = delete;

  private:
    PHLWINDOW m_window;
    Vector2D  m_position;
    Vector2D  m_size;
    bool      m_active = false;
};

bool isShadowPixel(const unsigned char* px) {
    const int alpha = px[3];
    if (alpha <= 0 || alpha > WINDOW_SHADOW_MAX_ALPHA)
        return false;

    const int maxRgb = std::max({px[0], px[1], px[2]});
    return maxRgb <= WINDOW_SHADOW_MAX_RGB;
}

bool isDarkPixel(const unsigned char* px) {
    if (px[3] <= 0)
        return false;

    const int maxRgb = std::max({px[0], px[1], px[2]});
    return maxRgb <= WINDOW_SHADOW_MAX_RGB;
}

int colorByte(float value) {
    return std::clamp(static_cast<int>(std::lround(value * 255.0F)), 0, 255);
}

ShadowColorBytes shadowColorBytes(const PHLWINDOW& window) {
    const CHyprColor color = window && window->m_realShadowColor ? window->m_realShadowColor->value() : CHyprColor(0xee1a1a1a);
    return {
        .r = colorByte(color.r),
        .g = colorByte(color.g),
        .b = colorByte(color.b),
        .a = colorByte(color.a),
    };
}

int reconstructedShadowAlpha(const unsigned char* px, const ShadowColorBytes& color) {
    int alpha = 0;
    const auto channelAlpha = [&](int pixelChannel, int colorChannel) {
        if (colorChannel <= 0)
            return 0;
        return static_cast<int>(std::lround(static_cast<double>(pixelChannel) * px[3] / colorChannel));
    };

    alpha = std::max(alpha, channelAlpha(px[0], color.r));
    alpha = std::max(alpha, channelAlpha(px[1], color.g));
    alpha = std::max(alpha, channelAlpha(px[2], color.b));
    return std::clamp(alpha, 0, color.a);
}

double modifiedShadowLength(double x, double y, double roundingPower) {
    roundingPower = std::clamp(roundingPower, 1.0, 10.0);
    return std::pow(std::pow(std::abs(x), roundingPower) + std::pow(std::abs(y), roundingPower), 1.0 / roundingPower);
}

double hyprlandRoundedShadowMultiplier(double x,
                                       double y,
                                       double fullWidth,
                                       double fullHeight,
                                       double range,
                                       double rounding,
                                       double roundingPower,
                                       int    shadowPower) {
    if (range <= 0.0 || fullWidth <= 0.0 || fullHeight <= 0.0)
        return 0.0;

    const double radius = range + std::max(0.0, rounding);
    const double left = range + std::max(0.0, rounding);
    const double top = range + std::max(0.0, rounding);
    const double right = fullWidth - left;
    const double bottom = fullHeight - top;

    const auto roundedDistanceMultiplier = [&](double distanceToCorner) {
        if (distanceToCorner > radius)
            return 0.0;
        if (distanceToCorner > radius - range)
            return std::pow((radius - distanceToCorner) / range, shadowPower);
        return 1.0;
    };

    bool   corner = false;
    double multiplier = 1.0;
    if (x < left) {
        if (y < top) {
            multiplier = roundedDistanceMultiplier(modifiedShadowLength(x - left, y - top, roundingPower));
            corner = true;
        } else if (y > bottom) {
            multiplier = roundedDistanceMultiplier(modifiedShadowLength(x - left, y - bottom, roundingPower));
            corner = true;
        }
    } else if (x > right) {
        if (y < top) {
            multiplier = roundedDistanceMultiplier(modifiedShadowLength(x - right, y - top, roundingPower));
            corner = true;
        } else if (y > bottom) {
            multiplier = roundedDistanceMultiplier(modifiedShadowLength(x - right, y - bottom, roundingPower));
            corner = true;
        }
    }

    if (!corner) {
        const double smallest = std::min({y, fullHeight - y, x, fullWidth - x});
        if (smallest < range)
            multiplier = std::pow(std::clamp(smallest / range, 0.0, 1.0), shadowPower);
    }

    return std::clamp(multiplier, 0.0, 1.0);
}

double shadowRoundingPx(const PHLWINDOW& window, double scale) {
    if (!window)
        return 0.0;

    const double borderSize = window->m_X11DoesntWantBorders ? 0.0 : std::max(0, window->getRealBorderSize());
    const double roundingBase = std::max(0.0F, window->rounding());
    const double roundingPower = std::clamp(static_cast<double>(window->roundingPower()), 1.0, 10.0);
    const double correctionOffset = borderSize * (std::sqrt(2.0) - 1.0) * std::max(2.0 - roundingPower, 0.0);
    const double rounding = roundingBase > 0.0 ? (roundingBase + borderSize) - correctionOffset : 0.0;
    return std::max(0.0, rounding * scale);
}

double shadowBorderPx(const PHLWINDOW& window, double scale) {
    if (!window || window->m_X11DoesntWantBorders)
        return 0.0;
    return std::max(0.0, static_cast<double>(std::max(0, window->getRealBorderSize())) * scale);
}

bool outsideRoundedVisibleShape(int x,
                                int y,
                                int visibleLeft,
                                int visibleTop,
                                int visibleRight,
                                int visibleBottom,
                                double rounding,
                                double roundingPower) {
    if (rounding <= 0.0 || visibleRight <= visibleLeft || visibleBottom <= visibleTop)
        return false;

    const double px = x + 0.5;
    const double py = y + 0.5;
    const double leftCenter = visibleLeft + rounding;
    const double topCenter = visibleTop + rounding;
    const double rightCenter = visibleRight - rounding;
    const double bottomCenter = visibleBottom - rounding;

    if (px < leftCenter && py < topCenter)
        return modifiedShadowLength(px - leftCenter, py - topCenter, roundingPower) > rounding;
    if (px > rightCenter && py < topCenter)
        return modifiedShadowLength(px - rightCenter, py - topCenter, roundingPower) > rounding;
    if (px < leftCenter && py > bottomCenter)
        return modifiedShadowLength(px - leftCenter, py - bottomCenter, roundingPower) > rounding;
    if (px > rightCenter && py > bottomCenter)
        return modifiedShadowLength(px - rightCenter, py - bottomCenter, roundingPower) > rounding;

    return false;
}

int configuredShadowRangePx(double scale) {
    static auto PSHADOWRANGE = CConfigValue<Config::INTEGER>("decoration:shadow:range");
    return std::max(0, static_cast<int>(std::ceil(static_cast<double>(std::max(0, sc<int>(*PSHADOWRANGE))) * scale)));
}

void expandReadbackToShadowBounds(RgbaReadback& readback, CBox& artifactBox, const CBox& visibleBox, const PHLWINDOW& window) {
    const auto color = shadowColorBytes(window);
    if (readback.width <= 0 || readback.height <= 0 || readback.pixels.empty() || artifactBox.w <= 0.0 || artifactBox.h <= 0.0 || visibleBox.w <= 0.0 ||
        visibleBox.h <= 0.0 || color.a <= 0)
        return;

    const double scaleX = static_cast<double>(readback.width) / artifactBox.w;
    const double scaleY = static_cast<double>(readback.height) / artifactBox.h;
    if (!std::isfinite(scaleX) || !std::isfinite(scaleY) || scaleX <= 0.0 || scaleY <= 0.0)
        return;

    const double shadowScale = std::max(scaleX, scaleY);
    const double shadowPadding = configuredShadowRangePx(shadowScale) + shadowBorderPx(window, shadowScale) + 2.0;
    if (shadowPadding <= 0.0)
        return;

    const double visibleLeft = (visibleBox.x - artifactBox.x) * scaleX;
    const double visibleTop = (visibleBox.y - artifactBox.y) * scaleY;
    const double visibleRight = (visibleBox.x + visibleBox.w - artifactBox.x) * scaleX;
    const double visibleBottom = (visibleBox.y + visibleBox.h - artifactBox.y) * scaleY;
    if (!std::isfinite(visibleLeft) || !std::isfinite(visibleTop) || !std::isfinite(visibleRight) || !std::isfinite(visibleBottom))
        return;

    const int targetLeft = static_cast<int>(std::floor(visibleLeft - shadowPadding));
    const int targetTop = static_cast<int>(std::floor(visibleTop - shadowPadding));
    const int targetRight = static_cast<int>(std::ceil(visibleRight + shadowPadding));
    const int targetBottom = static_cast<int>(std::ceil(visibleBottom + shadowPadding));
    const int padLeft = std::max(0, -targetLeft);
    const int padTop = std::max(0, -targetTop);
    const int padRight = std::max(0, targetRight - readback.width);
    const int padBottom = std::max(0, targetBottom - readback.height);
    if (padLeft == 0 && padTop == 0 && padRight == 0 && padBottom == 0)
        return;

    const int newWidth = readback.width + padLeft + padRight;
    const int newHeight = readback.height + padTop + padBottom;
    std::size_t bytes = 0;
    if (!checkedRgbaByteSize(newWidth, newHeight, bytes))
        return;

    std::vector<unsigned char> expanded(bytes, 0);
    const std::size_t oldRowBytes = static_cast<std::size_t>(readback.width) * RGBA_BYTES_PER_PIXEL;
    const std::size_t newRowBytes = static_cast<std::size_t>(newWidth) * RGBA_BYTES_PER_PIXEL;
    for (int y = 0; y < readback.height; ++y) {
        const auto* src = readback.pixels.data() + static_cast<std::size_t>(y) * oldRowBytes;
        auto*       dst = expanded.data() + static_cast<std::size_t>(y + padTop) * newRowBytes + static_cast<std::size_t>(padLeft) * RGBA_BYTES_PER_PIXEL;
        std::copy(src, src + oldRowBytes, dst);
    }

    readback.cropX -= padLeft;
    readback.cropTopY -= padTop;
    readback.width = newWidth;
    readback.height = newHeight;
    readback.pixels = std::move(expanded);
    artifactBox.x -= static_cast<double>(padLeft) / scaleX;
    artifactBox.y -= static_cast<double>(padTop) / scaleY;
    artifactBox.w = static_cast<double>(newWidth) / scaleX;
    artifactBox.h = static_cast<double>(newHeight) / scaleY;
}

void repairTransparentShadow(RgbaReadback& readback, const CBox& artifactBox, const CBox& visibleBox, const PHLWINDOW& window) {
    const auto color = shadowColorBytes(window);
    if (readback.width <= 0 || readback.height <= 0 || readback.pixels.empty() || artifactBox.w <= 0.0 || artifactBox.h <= 0.0 || visibleBox.w <= 0.0 ||
        visibleBox.h <= 0.0 || color.a <= 0)
        return;

    const double scaleX = static_cast<double>(readback.width) / artifactBox.w;
    const double scaleY = static_cast<double>(readback.height) / artifactBox.h;
    if (!std::isfinite(scaleX) || !std::isfinite(scaleY) || scaleX <= 0.0 || scaleY <= 0.0)
        return;

    const int visibleLeft = std::clamp(static_cast<int>(std::floor((visibleBox.x - artifactBox.x) * scaleX)), 0, readback.width);
    const int visibleTop = std::clamp(static_cast<int>(std::floor((visibleBox.y - artifactBox.y) * scaleY)), 0, readback.height);
    const int visibleRight =
        std::clamp(static_cast<int>(std::ceil((visibleBox.x + visibleBox.w - artifactBox.x) * scaleX)), visibleLeft, readback.width);
    const int visibleBottom =
        std::clamp(static_cast<int>(std::ceil((visibleBox.y + visibleBox.h - artifactBox.y) * scaleY)), visibleTop, readback.height);
    if (visibleLeft <= 0 && visibleTop <= 0 && visibleRight >= readback.width && visibleBottom >= readback.height)
        return;

    static auto PSHADOWPOWER = CConfigValue<Config::INTEGER>("decoration:shadow:render_power");
    const double shadowScale = std::max(scaleX, scaleY);
    const double shadowRange = std::max(1.0, static_cast<double>(configuredShadowRangePx(shadowScale)));
    const double rounding = shadowRoundingPx(window, shadowScale);
    const double roundingPower = window ? std::clamp(static_cast<double>(window->roundingPower()), 1.0, 10.0) : 2.0;
    const int    shadowPower = std::clamp(sc<int>(*PSHADOWPOWER), 1, 4);
    const double shadowLeft = visibleLeft - shadowRange;
    const double shadowTop = visibleTop - shadowRange;
    const double shadowWidth = std::max(1.0, (visibleRight - visibleLeft) + 2.0 * shadowRange);
    const double shadowHeight = std::max(1.0, (visibleBottom - visibleTop) + 2.0 * shadowRange);

    for (int y = 0; y < readback.height; ++y) {
        for (int x = 0; x < readback.width; ++x) {
            const bool inVisibleRect = x >= visibleLeft && x < visibleRight && y >= visibleTop && y < visibleBottom;
            const bool inRoundedCornerShadow = inVisibleRect && outsideRoundedVisibleShape(x, y, visibleLeft, visibleTop, visibleRight, visibleBottom, rounding, roundingPower);
            if (inVisibleRect && !inRoundedCornerShadow)
                continue;

            const auto i = (static_cast<std::size_t>(y) * readback.width + x) * RGBA_BYTES_PER_PIXEL;
            auto*      px = readback.pixels.data() + i;
            const bool existingShadowPixel = isShadowPixel(px) || ((!inVisibleRect || inRoundedCornerShadow) && isDarkPixel(px));
            const bool transparentShadowPadding = !inVisibleRect && px[3] == 0;
            if (!existingShadowPixel && !transparentShadowPadding)
                continue;

            const double shadowX = x - shadowLeft + 0.5;
            const double shadowY = y - shadowTop + 0.5;
            int repairedAlpha = std::clamp(static_cast<int>(std::lround(color.a * hyprlandRoundedShadowMultiplier(shadowX, shadowY, shadowWidth, shadowHeight,
                                                                                                                  shadowRange, rounding, roundingPower, shadowPower))),
                                           0, color.a);
            if (repairedAlpha <= 0 && existingShadowPixel)
                repairedAlpha = reconstructedShadowAlpha(px, color);

            px[3] = static_cast<unsigned char>(repairedAlpha);
            if (px[3] <= 2) {
                px[0] = 0;
                px[1] = 0;
                px[2] = 0;
                px[3] = 0;
                continue;
            }
            px[0] = static_cast<unsigned char>(color.r);
            px[1] = static_cast<unsigned char>(color.g);
            px[2] = static_cast<unsigned char>(color.b);
        }
    }
}

bool renderMonitorArtifact(const PHLMONITOR& monitor, const Time::steady_tp& frozenTime, const std::filesystem::path& path, int& width, int& height) {
    if (!monitor || !monitor->m_activeWorkspace || !g_pHyprRenderer || !g_pHyprOpenGL)
        return false;

    width = positiveRoundedIntFromDouble(monitor->m_pixelSize.x);
    height = positiveRoundedIntFromDouble(monitor->m_pixelSize.y);
    std::size_t framebufferBytes = 0;
    if (!checkedRgbaByteSize(width, height, framebufferBytes))
        return false;

    auto       framebuffer = makeShared<Render::GL::CGLFramebuffer>();
    const auto   drmFormat = monitor->m_output && monitor->m_output->state ? monitor->m_output->state->state().drmFormat : DRM_FORMAT_ABGR8888;
    if (!framebuffer->alloc(width, height, drmFormat) && !framebuffer->alloc(width, height, DRM_FORMAT_ABGR8888))
        return false;

    const bool previousBlockFeedback = g_pHyprRenderer->m_bBlockSurfaceFeedback;
    const bool previousBlockShader = g_pHyprRenderer->m_renderData.blockScreenShader;
    CRegion    fakeDamage{0, 0, width, height};

    g_pHyprOpenGL->makeEGLCurrent();
    g_pHyprRenderer->m_bBlockSurfaceFeedback = true;
    if (!g_pHyprRenderer->beginRender(monitor, fakeDamage, Render::RENDER_MODE_FULL_FAKE, nullptr, framebuffer)) {
        g_pHyprRenderer->m_bBlockSurfaceFeedback = previousBlockFeedback;
        return false;
    }

    glClearColor(0.F, 0.F, 0.F, 1.F);
    glClear(GL_COLOR_BUFFER_BIT);
    g_pHyprRenderer->renderWorkspace(monitor, monitor->m_activeWorkspace, frozenTime, CBox{0, 0, static_cast<double>(width), static_cast<double>(height)});
    g_pHyprRenderer->m_renderData.blockScreenShader = true;
    g_pHyprRenderer->endRender();
    g_pHyprRenderer->m_renderData.blockScreenShader = previousBlockShader;
    g_pHyprRenderer->m_bBlockSurfaceFeedback = previousBlockFeedback;

    const auto readback = readRgbaFramebufferRegion(*framebuffer, 0, 0, width, height);
    return !readback.pixels.empty() && writeFileExclusive(path, readback.pixels);
}

bool renderWindowArtifact(const PHLWINDOW& window,
                          const PHLMONITOR& monitor,
                          const Time::steady_tp& frozenTime,
                          const std::filesystem::path& path,
                          int& width,
                          int& height,
                          CBox& artifactBox) {
    if (!window || !monitor || !g_pHyprRenderer || !g_pHyprOpenGL)
        return false;

    WindowAnimationGoalOverride windowGoal(window);
    const CBox fullBox = renderedWindowBox(window, window->getFullWindowBoundingBox());
    CBox sourceCropBox = fullBox.copy().translate(-monitor->m_position).scale(monitor->m_scale <= 0.0 ? 1.0 : monitor->m_scale).round();
    width = positiveIntFromDouble(sourceCropBox.w);
    height = positiveIntFromDouble(sourceCropBox.h);

    const int framebufferWidth = positiveRoundedIntFromDouble(monitor->m_pixelSize.x);
    const int framebufferHeight = positiveRoundedIntFromDouble(monitor->m_pixelSize.y);
    std::size_t framebufferBytes = 0;
    std::size_t cropBytes = 0;
    if (!checkedRgbaByteSize(framebufferWidth, framebufferHeight, framebufferBytes) || !checkedRgbaByteSize(width, height, cropBytes))
        return false;

    const double scale = monitor->m_scale <= 0.0 ? 1.0 : monitor->m_scale;
    const int targetCropX = width < framebufferWidth ? (framebufferWidth - width) / 2 : 0;
    const int targetCropY = height < framebufferHeight ? (framebufferHeight - height) / 2 : 0;
    const Vector2D renderOffset = monitor->m_position + Vector2D{targetCropX / scale, targetCropY / scale} - fullBox.pos();
    windowGoal.setPositionOffset(renderOffset);
    CBox renderCropBox = fullBox.copy().translate(renderOffset).translate(-monitor->m_position).scale(scale).round();

    auto       framebuffer = makeShared<Render::GL::CGLFramebuffer>();
    const auto   drmFormat = monitor->m_output && monitor->m_output->state ? monitor->m_output->state->state().drmFormat : DRM_FORMAT_ABGR8888;
    if (!framebuffer->alloc(framebufferWidth, framebufferHeight, DRM_FORMAT_ABGR8888) && !framebuffer->alloc(framebufferWidth, framebufferHeight, drmFormat))
        return false;

    const bool previousBlockFeedback = g_pHyprRenderer->m_bBlockSurfaceFeedback;
    const bool previousBlockShader = g_pHyprRenderer->m_renderData.blockScreenShader;
    const bool previousRenderingSnapshot = g_pHyprRenderer->m_bRenderingSnapshot;
    CRegion    fakeDamage{0, 0, framebufferWidth, framebufferHeight};

    g_pHyprOpenGL->makeEGLCurrent();
    g_pHyprRenderer->m_bBlockSurfaceFeedback = true;
    if (!g_pHyprRenderer->beginRender(monitor, fakeDamage, Render::RENDER_MODE_FULL_FAKE, nullptr, framebuffer)) {
        g_pHyprRenderer->m_bBlockSurfaceFeedback = previousBlockFeedback;
        return false;
    }

    g_pHyprRenderer->m_bRenderingSnapshot = true;
    {
        FullSurfaceVisibleRegionOverride fullVisibleRegion(window);
        glClearColor(0.F, 0.F, 0.F, 0.F);
        glClear(GL_COLOR_BUFFER_BIT);
        g_pHyprRenderer->renderWindow(window, monitor, frozenTime, true, Render::RENDER_PASS_ALL, false, false);
    }
    g_pHyprRenderer->m_bRenderingSnapshot = previousRenderingSnapshot;

    g_pHyprRenderer->m_renderData.blockScreenShader = true;
    g_pHyprRenderer->endRender();
    g_pHyprRenderer->m_renderData.blockScreenShader = previousBlockShader;
    g_pHyprRenderer->m_bBlockSurfaceFeedback = previousBlockFeedback;

    const int cropX = clampedIntFromDouble(renderCropBox.x);
    const int cropY = clampedIntFromDouble(renderCropBox.y);
    auto      readback = readRgbaFramebufferRegion(*framebuffer, cropX, cropY, width, height);
    if (readback.pixels.empty())
        return false;

    PixelBounds bounds;
    if (findAlphaBounds(readback, bounds))
        readback = cropReadback(readback, bounds);
    if (readback.pixels.empty())
        return false;

    width = readback.width;
    height = readback.height;
    artifactBox = CBox{fullBox.x + (readback.cropX - cropX) / scale, fullBox.y + (readback.cropTopY - cropY) / scale, width / scale, height / scale};

    unpremultiplyAlpha(readback);
    expandReadbackToShadowBounds(readback, artifactBox, renderedWindowGoalMainSurfaceBox(window), window);
    repairTransparentShadow(readback, artifactBox, renderedWindowGoalMainSurfaceBox(window), window);
    width = readback.width;
    height = readback.height;
    return writeFileExclusive(path, readback.pixels);
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
    const auto transientFor = window->m_isX11 ? window->x11TransientFor() : PHLWINDOW{};

    return Json{
        {"address", "0x" + pointerId(window.get())},
        {"title", boundedString(window->m_title, 4096)},
        {"class", boundedString(window->m_class, 4096)},
        {"initialClass", boundedString(window->m_initialClass, 4096)},
        {"pid", window->getPID()},
        {"transientFor", transientFor ? Json("0x" + pointerId(transientFor.get())) : Json(nullptr)},
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

ScreenshotResult captureScreenshotSession(const std::filesystem::path& outputJsonPath, std::string_view targetRegex) {
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
    root["mode"] = targetRegex.empty() ? "monitors" : "window";
    root["monitors"] = Json::array();
    root["windows"] = Json::array();

    if (g_pInputManager) {
        const auto cursor = g_pInputManager->getMouseCoordsInternal();
        root["cursorPosition"] = Json{{"x", cursor.x}, {"y", cursor.y}};
    }

    const auto frozenTime = Time::steadyNow();
    if (!targetRegex.empty()) {
        const auto window = g_pCompositor->getWindowByRegex(std::string(targetRegex));
        if (!window || !window->m_isMapped)
            return {.success = false, .error = "target window not found"};

        const auto monitor = window->m_monitor.lock();
        if (!monitor)
            return {.success = false, .error = "target window has no monitor"};

        int        width = 0;
        int        height = 0;
        CBox       artifactBox;
        const auto artifactPath = artifactRoot / ("window-" + pointerId(window.get()) + ".rgba");
        if (!renderWindowArtifact(window, monitor, frozenTime, artifactPath, width, height, artifactBox))
            return {.success = false, .error = "failed to render target window"};

        const double scale = monitor->m_scale <= 0.0 ? 1.0 : monitor->m_scale;
        root["target"] = windowJson(window);
        root["target"]["artifactPath"] = artifactPath.string();
        root["target"]["artifactWidth"] = width;
        root["target"]["artifactHeight"] = height;
        root["target"]["artifactTopDown"] = true;
        const auto inputBox = inputWindowBox(window, artifactBox);
        root["target"]["artifactGeometry"] = rectJson(artifactBox);
        root["target"]["inputGeometry"] = rectJson(inputBox);
        root["monitors"].push_back(Json{
            {"name", "window:" + boundedString(window->m_title, 4096)},
            {"geometry", rectJson(inputBox)},
            {"scale", scale},
            {"transform", 0},
            {"artifactPath", artifactPath.string()},
            {"artifactWidth", width},
            {"artifactHeight", height},
            {"artifactTopDown", true},
        });
        root["windows"].push_back(windowJson(window));

        const auto serialized = root.dump(2, ' ', false, Json::error_handler_t::replace);
        std::vector<unsigned char> bytes(serialized.begin(), serialized.end());
        if (!writeFileExclusive(outputJsonPath, bytes))
            return {.success = false, .error = "failed to write output json"};

        return {.success = true};
    }

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

} // namespace hypr_agent_protal
