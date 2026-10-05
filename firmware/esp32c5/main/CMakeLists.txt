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

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    INGRESS_NAME="${BENCH_INGRESS}"
    RUNG_NAME="${BENCH_RUNG}"
    # 2 = 2.4 GHz only, 5 = 5 GHz only, 0 = either (see app_main.c). The C5 is the
    # dual-band chip, so it is the one that has to be told.
    BENCH_BAND_PIN=${BENCH_BAND_PIN}
)
