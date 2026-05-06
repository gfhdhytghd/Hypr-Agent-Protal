cmake_minimum_required(VERSION 3.19)
project(HyprAgentProtal VERSION "0.3.26")

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(CTest)
include(GNUInstallDirs)

add_compile_definitions(WLR_USE_UNSTABLE)
add_compile_options(
  -g
  -Wall
  -Wextra
  -Wno-unused-parameter
  -Wno-unused-value
  -Wno-missing-field-initializers
  -Wno-narrowing
  -Wno-pointer-arith
  --no-gnu-unique
)

find_package(PkgConfig REQUIRED)
pkg_check_modules(HYPR_DEPS REQUIRED hyprland)
find_package(nlohmann_json REQUIRED)
find_package(Python3 COMPONENTS Interpreter)

add_library(hypr_agent_protal SHARED
  src/plugin/main.cpp
  src/plugin/screenshot_capture.cpp
)

set_target_properties(hypr_agent_protal PROPERTIES OUTPUT_NAME "hypr-agent-protal")

target_include_directories(hypr_agent_protal PRIVATE src ${HYPR_DEPS_INCLUDE_DIRS})
target_link_directories(hypr_agent_protal PRIVATE ${HYPR_DEPS_LIBRARY_DIRS})
target_link_libraries(hypr_agent_protal PRIVATE ${HYPR_DEPS_LIBRARIES} nlohmann_json::nlohmann_json)
target_link_options(hypr_agent_protal PRIVATE -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack)

install(TARGETS hypr_agent_protal LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(PROGRAMS scripts/hypr-agent-protalctl scripts/install-codex-cursor-asset mcp/hypr-agent-protal-mcp.py DESTINATION ${CMAKE_INSTALL_BINDIR})

if(BUILD_TESTING AND Python3_Interpreter_FOUND)
  add_test(
    NAME hypr-agent-protal-python-compile
    COMMAND ${Python3_EXECUTABLE} -m py_compile
      ${CMAKE_SOURCE_DIR}/scripts/hypr-agent-protalctl
      ${CMAKE_SOURCE_DIR}/scripts/install-codex-cursor-asset
      ${CMAKE_SOURCE_DIR}/mcp/hypr-agent-protal-mcp.py
      ${CMAKE_SOURCE_DIR}/tests/mcp_smoke.py
      ${CMAKE_SOURCE_DIR}/tests/mcp_geometry.py
      ${CMAKE_SOURCE_DIR}/tests/cursor_render.py
  )
  add_test(
    NAME hypr-agent-protal-cursor-render
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/cursor_render.py
  )
  add_test(
    NAME hypr-agent-protal-mcp-smoke
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/mcp_smoke.py ${CMAKE_SOURCE_DIR}/mcp/hypr-agent-protal-mcp.py
  )
  add_test(
    NAME hypr-agent-protal-mcp-geometry
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/mcp_geometry.py
  )
endif()
