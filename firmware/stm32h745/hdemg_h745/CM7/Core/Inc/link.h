/* link.h — the wired link out of the ring: none (generate-and-drop), QUADSPI (Stage 2a)
 * or SDIO (Stage 2b). The service loop is polled from main; generation is interrupt
 * driven, so a link may block for a transaction without touching the offered load. */
#pragma once
#include <stdint.h>

typedef enum { LINK_NONE = 0, LINK_QSPI = 1, LINK_SDIO = 2 } link_kind_t;

typedef struct {
    link_kind_t kind;
    /* QUADSPI */
    uint32_t qspi_hz;          /* data clock for WRDMA / WR_END */
    uint32_t qspi_rd_hz;       /* clock for RDBUF register reads (slave MISO is slow) */
    uint32_t qspi_dummy;       /* dummy cycles; 8 on the installed IDF */
    uint32_t qspi_addr_lines;  /* 4 -> WRDMA 0xA3, 1 -> 0x23 */
    uint32_t qspi_sshift;      /* sample half a cycle late on reads */
    uint32_t qspi_even;        /* prefer even frame counts (transaction length % 4 == 0) */
    uint32_t qspi_nocredit;    /* DEBUG: ignore flow control entirely */
    /* SDIO */
    uint32_t sdio_hz;
    /* both */
    uint32_t batch_max;        /* frames per transaction, 1..HDEMG_LINK_MAX_FRAMES */
} link_cfg_t;

typedef struct {
    uint64_t bytes;            /* bytes the link accepted */
    uint32_t xfers;            /* transactions completed */
    uint32_t credit_waits;     /* times the link had data but no credit */
    uint32_t ready_timeouts;   /* ...and still none 10 ms later */
    uint32_t errors;           /* transactions that failed or timed out */
    uint32_t torn_reads;       /* credit reads that did not match on re-read */
    uint32_t credit_errors;    /* impossible credit values (slave/master out of step) */
    uint32_t ready_edges;      /* READY rising edges seen (QSPI) */
    uint32_t last_loaded;      /* last slave counters read (QSPI) / token (SDIO) */
    uint32_t last_completed;
    uint32_t sent;             /* buffers the master has ended */
    uint32_t actual_hz;        /* clock actually programmed */
} link_stats_t;

void        link_cfg_defaults(link_cfg_t *c);
const char *link_name(link_kind_t k);

/* Returns 0 on success, otherwise a negative code; *err is a short reason. On failure
 * the link's pins are released again. */
int  link_open(const link_cfg_t *c, const char **err);
void link_poll(int flush);          /* flush: the run is over, send odd leftovers too */
void link_close(void);
void link_stats(link_stats_t *out);

/* Per-link implementations. */
int  link_qspi_open(const link_cfg_t *c, const char **err);
void link_qspi_poll(int flush);
void link_qspi_close(void);
void link_qspi_stats(link_stats_t *out);
/* Bring-up helpers, usable without a run (ctl verbs q_open / q_rd / q_tx / q_close). */
int  link_qspi_read_reg(uint32_t addr, uint32_t *value);
int  link_qspi_send_raw(const uint8_t *data, uint32_t n, int end_buffer);

int  link_sdio_open(const link_cfg_t *c, const char **err);
void link_sdio_poll(int flush);
void link_sdio_close(void);
void link_sdio_stats(link_stats_t *out);
