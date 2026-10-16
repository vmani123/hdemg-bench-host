/* linkpins.h — the 18 Stage 2 link pins as wired once (docs/CAMPAIGN_PROTOCOL.md §2,
 * host/bench/wiring.py WIRES), in wire order.
 *
 * The links use this table to leave every pin they do not drive de-initialised (Hi-Z),
 * and the `pins` console verb uses it for the wiring check (§4): drive one pin with
 * every other one a pulled-down input, or read them all. Nothing here may be called
 * while a link is open; ctl.c refuses `pins` then. */
#pragma once
#include <stdint.h>
#include "stm32h7xx_hal.h"

#define LINKPINS_N 18

typedef struct {
    const char   *name;     /* "PB2", as the console and the host write it */
    GPIO_TypeDef *port;
    uint16_t      pin;      /* GPIO_PIN_x */
    const char   *role;     /* what the wire carries, e.g. "s3_qspi_clk" */
} linkpin_t;

extern const linkpin_t g_linkpins[LINKPINS_N];

void linkpins_clocks_on(void);          /* GPIOB..GPIOG clocks (needed to touch the pins) */
void linkpins_release_all(void);        /* every link pin de-initialised: analog, no pull */
int  linkpins_find(const char *name);   /* table index, case-insensitive; -1 if not a link pin */
void linkpins_inputs(void);             /* every link pin an input with the pull-down on */
void linkpins_drive(int idx, int level);/* that pin a push-pull output at that level, every
                                           other link pin an input with the pull-down on */
int  linkpins_level(int idx);           /* the pin's input level (0 while it is analog) */
