/* hdemg_frame.h — the wire format. MEASUREMENT CONTRACT: byte-identical in both
 * firmware repos and mirrored by host/bench/frame.py. A change here is a contract
 * break, not a refactor: numbers taken before and after are not comparable. */
#pragma once
#include <stdint.h>

#define HDEMG_MAGIC      0xA55Au
#define HDEMG_TYPE_RAW16 0
#define HDEMG_CHIP_COMBINED 0xFF

typedef struct __attribute__((packed)) {
    uint16_t magic;     /* 0xA55A */
    uint8_t  type;      /* 0 = RAW16 */
    uint8_t  chip_id;   /* 0, 1, or 0xFF = combined */
    uint32_t seq;       /* monotonic frame counter — loss and ordering */
    uint32_t t_stm;     /* source cycle counter at generation — latency anchor */
    uint16_t n_ch;      /* channels in payload */
} hdemg_hdr_t;

_Static_assert(sizeof(hdemg_hdr_t) == 14, "header must be 14 bytes packed LE");

#define HDEMG_HDR_BYTES      14
#define HDEMG_RAW16_128CH    270   /* 14 + 128 * int16 */
