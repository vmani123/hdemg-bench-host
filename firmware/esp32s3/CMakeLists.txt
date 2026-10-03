cmake_minimum_required(VERSION 3.16)

# The shared measurement core is pulled in as an out-of-tree component. Both targets
# point at the SAME directory — there is no second copy to drift.
#
# protocol_examples_common (for example_connect()) ships inside ESP-IDF and is referenced
# by path, NOT through idf_component.yml: the component manager cannot resolve ${IDF_PATH}
# in a `path:` field, and adding an `idf:` version constraint there rejects any dev build
# of IDF outright (v6.0-dev sorts below 6.0.0 under semver). The required IDF version is
# documented in the README and recorded in every ledger record instead of being enforced
# here, where it only breaks working toolchains.
set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../bench_common"
    "$ENV{IDF_PATH}/examples/common_components/protocol_examples_common")

set(BENCH_INGRESS "synth" CACHE STRING "synth | qspi")
set(BENCH_RUNG    "r0-baseline" CACHE STRING "tuning rung this binary was built with")

# Wi-Fi credentials and anything else machine-specific live in sdkconfig.local, which is
# gitignored. Putting them there rather than in sdkconfig means they survive a
# set-target or a deleted sdkconfig — losing the SSID silently partway through a bench
# session is exactly the kind of thing that wastes an evening.
#
# Guarded: hostrun.sh passes SDKCONFIG_DEFAULTS explicitly when applying a tuning rung,
# and that must win.
if(NOT DEFINED SDKCONFIG_DEFAULTS)
    set(_defaults "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")
    if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.local")
        list(APPEND _defaults "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.local")
    endif()
    set(SDKCONFIG_DEFAULTS "${_defaults}")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(hdemg_esp32s3_uplink)
