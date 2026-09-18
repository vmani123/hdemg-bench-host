cmake_minimum_required(VERSION 3.16)

# The shared measurement core is pulled in as an out-of-tree component. Both targets
# point at the SAME directory — there is no second copy to drift.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../bench_common")

set(BENCH_INGRESS "synth" CACHE STRING "synth | qspi")
set(BENCH_RUNG    "r0-baseline" CACHE STRING "tuning rung this binary was built with")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(hdemg_esp32s3_uplink)
