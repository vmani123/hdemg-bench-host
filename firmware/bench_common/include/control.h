/* control.h — the runtime control plane.
 *
 * MEASUREMENT CONTRACT. This is the mechanism that turns hundreds of flashes into
 * about four: rate, transport, destination, duration, frame size and run label are all
 * settable at runtime. Only the target, the ingress and the tuning rung need a rebuild.
 *
 * Protocol: line-oriented ASCII over UDP, JSON replies. UDP rather than serial because
 * the serial port is shared with idf.py monitor and stops working the moment a board is
 * not on the desk. */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "hdemg_frame.h"

/* INGRESS_NAME and RUNG_NAME are supplied by each repo's CMakeLists as
 * compile definitions. They describe the build, not the measurement, which
 * is why this file stays byte-identical across both repos. */
#ifndef INGRESS_NAME
#define INGRESS_NAME "unknown"
#endif
#ifndef RUNG_NAME
#define RUNG_NAME "unknown"
#endif

#define CONTROL_PROTOCOL_VERSION 1
#define CONTROL_PORT             3334

typedef enum { XPORT_UDP = 0, XPORT_TCP = 1 } xport_t;

typedef struct {
    uint64_t rate_bps;
    xport_t  transport;
    char     dst_ip[40];
    uint16_t dst_port;
    uint32_t dur_s;
    uint32_t frame_bytes;
    char     payload[16];
    char     label[48];
} run_cfg_t;

void             control_start(void);
const run_cfg_t *control_cfg(void);
bool             control_running(void);
void             control_stop_run(void);
