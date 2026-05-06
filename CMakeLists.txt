cmake_minimum_required(VERSION 3.19)
project(HyprComputerUseMCP VERSION "0.2.0")

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

add_library(hyprcum SHARED
  src/plugin/main.cpp
  src/plugin/screenshot_capture.cpp
)

target_include_directories(hyprcum PRIVATE src ${HYPR_DEPS_INCLUDE_DIRS})
target_link_directories(hyprcum PRIVATE ${HYPR_DEPS_LIBRARY_DIRS})
target_link_libraries(hyprcum PRIVATE ${HYPR_DEPS_LIBRARIES} nlohmann_json::nlohmann_json)
target_link_options(hyprcum PRIVATE -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack)

install(TARGETS hyprcum LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(PROGRAMS scripts/hyprcumctl mcp/hyprcum_mcp.py DESTINATION ${CMAKE_INSTALL_BINDIR})

if(BUILD_TESTING AND Python3_Interpreter_FOUND)
  add_test(
    NAME hyprcum-python-compile
    COMMAND ${Python3_EXECUTABLE} -m py_compile
      ${CMAKE_SOURCE_DIR}/scripts/hyprcumctl
      ${CMAKE_SOURCE_DIR}/mcp/hyprcum_mcp.py
      ${CMAKE_SOURCE_DIR}/tests/mcp_smoke.py
  )
  add_test(
    NAME hyprcum-mcp-smoke
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/mcp_smoke.py ${CMAKE_SOURCE_DIR}/mcp/hyprcum_mcp.py
  )
endif()
