# Target-specific only: entry point, pin map, and the wired-ingress implementations.
# Everything that decides what the number MEANS lives in firmware/common.
# BENCH_INGRESS: synth | qspi | sdio
set(BENCH_VALID_INGRESS synth qspi sdio)
include("${CMAKE_CURRENT_LIST_DIR}/../../bench_options.cmake")

set(SRCS "app_main.c")

if(BENCH_INGRESS STREQUAL "qspi")
    list(APPEND SRCS "source_qspi.c")
elseif(BENCH_INGRESS STREQUAL "sdio")
    list(APPEND SRCS "source_sdio.c")
endif()

idf_component_register(SRCS ${SRCS} INCLUDE_DIRS "."
                       REQUIRES bench_common protocol_examples_common esp_wifi)

# A wired source's entry points are referenced only from bench_common, which is linked
# AFTER this component, so the linker would never pull source_*.c out of this archive.
# Name one of its symbols as undefined up front to force it in.
if(NOT BENCH_INGRESS STREQUAL "synth")
    target_link_libraries(${COMPONENT_LIB} INTERFACE "-u source_start")
endif()

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    INGRESS_NAME="${BENCH_INGRESS}"
    RUNG_NAME="${BENCH_RUNG}"
)
