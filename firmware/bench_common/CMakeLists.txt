# The measurement core — ONE copy, compiled by both targets.
#
# This component fixes WHAT THE NUMBER MEANS: the single byte-counting point
# (pipe_note_sent() in pipe.c, called once by the sink task and nowhere else), the paced
# source accuracy, the latency stamp instant, the wire format, and the control protocol.
#
# It is shared rather than duplicated on purpose. Two copies of this code, however
# carefully reviewed, can drift — and a drift here would look exactly like a silicon
# difference while nothing in the data revealed it. One copy makes that impossible by
# construction, which is stronger than any checker.
#
# What is NOT here, and must not be: anything that only changes HOW BIG the number is.
# Every tuning lever lives in host/rungs/<chip>/, and target-specific ingress and pin
# maps live in firmware/<target>/main/. Each chip is tuned independently — that
# divergence is the experiment.
include("${CMAKE_CURRENT_LIST_DIR}/../bench_options.cmake")

set(SRCS
    "pipe.c"
    "control.c"
    "pacing.c"
    "instr.c"
    "sink_udp.c"
    "sink_tcp.c"
)

# The paced synthetic source is part of the measurement contract (it is the Stage 1
# control arm), so it ships here and is selected by the target's BENCH_INGRESS.
if(BENCH_INGRESS STREQUAL "synth")
    list(APPEND SRCS "source_synth.c")
# The wired sources (Stage 2) sit beside the core without changing it: same pipe, same
# sink, same counting point. The target's main/ supplies the pins and the entry point.
elseif(BENCH_INGRESS STREQUAL "qspi")
    list(APPEND SRCS "ingress_common.c" "ingress_qspi.c")
elseif(BENCH_INGRESS STREQUAL "sdio")
    list(APPEND SRCS "ingress_common.c" "ingress_sdio.c")
endif()

# The esp_driver_* requirements are unconditional on purpose: IDF expands requirements
# in a script context that does not see BENCH_INGRESS (see bench_options.cmake), so a
# requirement made conditional on it would silently be dropped.
idf_component_register(
    SRCS ${SRCS}
    INCLUDE_DIRS "include"
    REQUIRES esp_wifi esp_timer esp_netif lwip nvs_flash esp_event esp_app_format
             esp_hw_support driver esp_driver_spi esp_driver_gpio esp_driver_sdio
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    INGRESS_NAME="${BENCH_INGRESS}"
    RUNG_NAME="${BENCH_RUNG}"
)
