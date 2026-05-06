#pragma once

#include <filesystem>
#include <string>

namespace hyprcum {

struct ScreenshotResult {
    bool        success = false;
    std::string error;
};

ScreenshotResult captureScreenshotSession(const std::filesystem::path& outputJsonPath);

} // namespace hyprcum
