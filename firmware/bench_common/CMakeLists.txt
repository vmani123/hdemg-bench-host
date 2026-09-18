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
endif()

idf_component_register(
    SRCS ${SRCS}
    INCLUDE_DIRS "include"
    REQUIRES esp_wifi esp_timer esp_netif lwip nvs_flash esp_event esp_app_format
             esp_hw_support driver
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    INGRESS_NAME="${BENCH_INGRESS}"
    RUNG_NAME="${BENCH_RUNG}"
)
