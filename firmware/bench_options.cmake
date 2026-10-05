# Build-time bench options, resolved in ONE place.
#
# Why this file exists: ESP-IDF expands component requirements in a SEPARATE cmake
# script context (component_get_requirements.cmake) that does NOT see the project's
# cache variables. A `set(... CACHE STRING ...)` in the project CMakeLists is therefore
# invisible during that pass, so BENCH_INGRESS read back as empty and the component's
# validation fired FATAL_ERROR before anything compiled.
#
# The fix is to give the options a default wherever they are read, and to reject only a
# value that was actually supplied and is wrong — never an unset one.
if(NOT DEFINED BENCH_INGRESS OR BENCH_INGRESS STREQUAL "")
    set(BENCH_INGRESS "synth")
endif()
if(NOT DEFINED BENCH_RUNG OR BENCH_RUNG STREQUAL "")
    set(BENCH_RUNG "r0-baseline")
endif()

# BENCH_BAND: which Wi-Fi band the build is pinned to — 2.4, 5, or auto (no pin).
# A cell is defined by its band, and a dual-band access point offers both under one
# SSID, so the band has to be fixed in the board rather than at the access point.
# Only a dual-band chip acts on it (the C5); the S3 has one band and ignores it.
if(NOT DEFINED BENCH_BAND OR BENCH_BAND STREQUAL "")
    set(BENCH_BAND "auto")
endif()
if(BENCH_BAND STREQUAL "2.4")
    set(BENCH_BAND_PIN 2)
elseif(BENCH_BAND STREQUAL "5")
    set(BENCH_BAND_PIN 5)
elseif(BENCH_BAND STREQUAL "auto")
    set(BENCH_BAND_PIN 0)
else()
    message(FATAL_ERROR "BENCH_BAND='${BENCH_BAND}' must be 2.4, 5 or auto")
endif()

# Validate only a supplied value. BENCH_TARGET_INGRESS is set by each target to the
# list it actually supports (the S3 has no SDIO slave).
if(DEFINED BENCH_VALID_INGRESS)
    if(NOT BENCH_INGRESS IN_LIST BENCH_VALID_INGRESS)
        message(FATAL_ERROR
            "BENCH_INGRESS='${BENCH_INGRESS}' is not supported by this target "
            "(valid: ${BENCH_VALID_INGRESS})")
    endif()
endif()
