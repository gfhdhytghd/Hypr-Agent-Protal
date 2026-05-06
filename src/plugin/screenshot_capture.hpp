#pragma once

#include <filesystem>
#include <string>
#include <string_view>

namespace hypr_agent_protal {

struct ScreenshotResult {
    bool        success = false;
    std::string error;
};

ScreenshotResult captureScreenshotSession(const std::filesystem::path& outputJsonPath, std::string_view targetRegex = {});

} // namespace hypr_agent_protal
