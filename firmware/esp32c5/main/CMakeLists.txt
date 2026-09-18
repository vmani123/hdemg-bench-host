# Target-specific only: entry point, pin map, and the wired-ingress implementations.
# Everything that decides what the number MEANS lives in firmware/common.
# BENCH_INGRESS: synth | qspi | sdio
set(SRCS "app_main.c")

if(BENCH_INGRESS STREQUAL "qspi")
    list(APPEND SRCS "source_qspi.c")
elseif(BENCH_INGRESS STREQUAL "sdio")
    list(APPEND SRCS "source_sdio.c")
elseif(NOT BENCH_INGRESS STREQUAL "synth")
    message(FATAL_ERROR "unknown BENCH_INGRESS '${BENCH_INGRESS}' (want: synth | qspi | sdio)")
endif()

idf_component_register(SRCS ${SRCS} INCLUDE_DIRS "." REQUIRES bench_common)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    INGRESS_NAME="${BENCH_INGRESS}"
    RUNG_NAME="${BENCH_RUNG}"
)
