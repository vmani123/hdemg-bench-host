/* bench_pins.h — the Stage 2 link GPIOs, for the wiring check (`pins`, protocol v2).
 *
 * The wiring "hello world" (docs/CAMPAIGN_PROTOCOL.md §4) drives each wire from one end
 * and reads it at the other, with no link driver involved, so "the wire is wrong" is
 * settled before "the protocol is wrong" ever comes up. The ESP side of that is here:
 * every link GPIO an input with the pull-down on, or exactly one of them driven.
 *
 * Only a synth build answers: in a wired build the link driver owns these pins. */
#pragma once
#include <stddef.h>

#define BENCH_PIN_MAX_ROLES 2

typedef struct {
    int         gpio;
    const char *roles[BENCH_PIN_MAX_ROLES];   /* e.g. "qspi_cs", "sdio_cmd"; unused slots NULL */
} bench_link_pin_t;

/* Supplied by each target (firmware/<target>/main/app_main.c) from its pins_<chip>.h,
 * in wire order. host/bench/wiring.py (WIRES) is the canonical map; the two must agree. */
const bench_link_pin_t *bench_link_pins(size_t *n);

/* The `pins` command. `args` is everything after "pins" (modified in place); the JSON
 * reply is written to out. */
void bench_pins_command(char *args, char *out, size_t out_sz);
