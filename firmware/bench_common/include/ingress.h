/* ingress.h — Stage 2 wired ingress (STM32H745 -> ESP), shared by both targets.
 *
 * The wired sources replace source_synth.c one hop upstream and must meet the same
 * contract: fill pool buffers with WHOLE frames only, pipe_submit() them, and report
 * source_achieved_bps(). Nothing here touches the measurement core (pipe.c, control.c,
 * instr.c): a Stage 2 build runs the same sink, the same byte-counting point and the
 * same control plane as Stage 1, so (synth - ingress) is the ingress cost and nothing
 * else.
 *
 * Because the control plane is left alone, ingress counters are served on a port of
 * their own (INGRESS_STAT_PORT): send "istat", get one JSON object back.
 *
 * LINK TEST (guide §7.2). `cfg payload=lt:<seed>` before `start` turns the run into a
 * link-only test: every buffer is fully verified — length, magic at every stride, seq
 * continuity, and the payload regenerated from (seq, seed) — and then DROPPED instead
 * of going to the sink, so Wi-Fi is out of the loop and a link fault cannot masquerade
 * as a radio result. In a normal run only the cheap per-frame header checks are made. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "hdemg_link.h"

#define INGRESS_STAT_PORT 3335

typedef struct {
    int cs, clk, d0, d1, d2, d3;   /* GP-SPI2 pins; IO_MUX set for full speed */
    int ready;                     /* slave -> master: a receive buffer is loaded */
} ingress_qspi_pins_t;

typedef struct {
    uint64_t bytes_in;         /* bytes accepted this run (whole, structurally valid frames) */
    uint32_t bufs_in;
    uint32_t frames_in;
    uint32_t len_errors;       /* buffer empty, oversize, or not a whole number of frames */
    uint32_t magic_errors;     /* 0xA55A missing at a frame stride */
    uint32_t seq_gaps;         /* frames missing between consecutive seq values */
    uint32_t seq_gap_events;
    uint32_t seq_backwards;    /* seq repeated or went back: duplicated / reordered data */
    uint32_t payload_errors;   /* link test only: payload != xorshift(seq, seed) */
    uint32_t pool_starved;     /* times nothing could be armed: every pool buffer was busy */
    uint32_t stray_bufs;       /* buffers that arrived while no run was active */
    uint32_t first_seq, last_seq;
    uint32_t link_test, seed;
    int32_t  init_err;         /* esp_err_t from bring-up; 0 = the link is up */
} ingress_stats_t;

/* ---- provided by ingress_common.c --------------------------------------------------- */
void     ingress_common_start(const char *link_name);   /* stat port; call once */
void     ingress_set_init_err(int err);
void     ingress_run_begin(void);                       /* a run started: reset, read the mode */
void     ingress_run_end(void);
bool     ingress_link_test(void);
/* Check one received buffer and count it. True = whole frames with intact headers, so
 * it may go to the sink. */
bool     ingress_accept(const uint8_t *buf, size_t len);
void     ingress_note_starved(void);
void     ingress_note_stray(void);
void     ingress_set_counters(uint32_t loaded, uint32_t completed);
uint64_t ingress_achieved_bps(void);

/* ---- provided by the selected link -------------------------------------------------- */
void     ingress_qspi_start(const ingress_qspi_pins_t *pins);
void     ingress_sdio_start(void);
