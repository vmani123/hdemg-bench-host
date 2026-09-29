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

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(hdemg_esp32s3_uplink)
