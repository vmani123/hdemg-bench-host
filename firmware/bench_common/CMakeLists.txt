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
# What is NOT here, and must not be: any tuning VALUE. Every lever's setting lives in
# host/rungs/<chip>/, and target-specific ingress and pin maps live in
# firmware/<target>/main/. Each chip is tuned independently — that divergence is the
# experiment. The levers that live in code (UDP datagram size, pool geometry, channel
# width) are declared in this component's Kconfig so both targets share their names;
# a rung sets them like any ESP-IDF symbol.
include("${CMAKE_CURRENT_LIST_DIR}/../bench_options.cmake")

set(SRCS
    "pipe.c"
    "control.c"
    "pacing.c"
    "instr.c"
    "sink_udp.c"
    "sink_tcp.c"
    "wifi_pin.c"
    "linkdrop.c"
    "bench_pins.c"
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

# A wired link DMAs straight into pool buffers, and link DMA into PSRAM has not been
# validated on this bench, so a wired build with the pool in PSRAM is refused here rather
# than discovered as corrupt frames at 3 a.m. CONFIG_ values are only set once sdkconfig
# has been processed, and BENCH_INGRESS only outside IDF's early requirements pass (see
# bench_options.cmake), so in that pass this check is simply false. ingress_common.c
# carries an #error as a second line of defence.
if(CONFIG_BENCH_PIPE_POOL_PSRAM AND NOT BENCH_INGRESS STREQUAL "synth")
    message(FATAL_ERROR
        "CONFIG_BENCH_PIPE_POOL_PSRAM is set but BENCH_INGRESS='${BENCH_INGRESS}': a wired "
        "link DMAs straight into pool buffers, which must stay in internal RAM. Drop "
        "CONFIG_BENCH_PIPE_POOL_PSRAM from the rung, or build with BENCH_INGRESS=synth.")
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
