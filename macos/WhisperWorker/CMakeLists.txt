cmake_minimum_required(VERSION 3.16)
project(DictateWhisper LANGUAGES C CXX)

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)

if(APPLE)
    set(GGML_METAL ON CACHE BOOL "" FORCE)
    set(GGML_METAL_EMBED_LIBRARY ON CACHE BOOL "" FORCE)
endif()

add_subdirectory("${WHISPER_SOURCE_DIR}" whisper)
add_executable(DictateWhisper main.cpp)
target_compile_features(DictateWhisper PRIVATE cxx_std_17)
target_link_libraries(DictateWhisper PRIVATE whisper)
