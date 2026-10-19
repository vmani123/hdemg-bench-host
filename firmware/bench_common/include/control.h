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

/* v2 (docs/CAMPAIGN_PROTOCOL.md §1): cfg keys nodelay / wr_max / dgram, the linkdrop
 * and pins commands, and the health fields in stat. The host refuses any other version,
 * so a board on an old build is reflashed rather than half-understood. */
#define CONTROL_PROTOCOL_VERSION 2
#define CONTROL_PORT             3334

/* Every reply fits one unfragmented datagram on a 1500-byte MTU. */
#define CONTROL_REPLY_MAX        1400

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
    /* v2 transport knobs. Their defaults reproduce v1 byte for byte. */
    uint8_t  nodelay;      /* TCP_NODELAY on the next TCP connection (1 = on, as in v1) */
    uint32_t wr_max;       /* TCP: largest send(), whole frames; 0 = the whole pool buffer */
    uint32_t dgram;        /* UDP: largest datagram, whole frames; 0 = CONFIG_BENCH_UDP_DGRAM_MAX */
    /* Not a protocol bump: an older board ignores the key, and 0 is the v2 behaviour. */
    uint32_t batch;        /* synth source: frames held back until a batch is due; 0 = send
                            * whatever is due each 1 ms tick (v2) */
} run_cfg_t;

/* The frame size the source actually uses: a value too small to hold a header is
 * replaced by the 128-channel frame, as source_synth.c does. A sink that splits a pool
 * buffer must split on this, or a write or a datagram could end mid-frame. */
static inline uint32_t control_frame_bytes(const run_cfg_t *c)
{
    return (c->frame_bytes < HDEMG_HDR_BYTES + 2) ? HDEMG_RAW16_128CH : c->frame_bytes;
}

void             control_start(void);
const run_cfg_t *control_cfg(void);
bool             control_running(void);
void             control_stop_run(void);
