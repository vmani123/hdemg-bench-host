/* instr.h — on-device instrumentation.
 *
 * MEASUREMENT CONTRACT. CPU idle percentage is the observable that settles the
 * single-core question (plan §2.3): idle -> 0 at the knee means the core is the limit;
 * idle still above 20% means the limit is RF, the AP or protocol overhead and core
 * count is irrelevant. The negotiated PHY rate is what settles whether the access point
 * ever offered 802.11ax (plan §7.3b). Both are reported every run.
 *
 * Protocol v2 adds the board's health (docs/CAMPAIGN_PROTOCOL.md §1.3): heap
 * fragmentation and a windowed heap low, PSRAM, uptime and reset reason, per-task stack
 * low-water marks, and counters for the events a long unattended campaign has to be able
 * to see after the fact — back-pressure, failed sends, TCP reconnects, Wi-Fi drops. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

void        instr_start(void);
/* Not reentrant: the control task is its only caller (stat and stop). Reading it also
 * restarts the heap_win_min window. */
void        instr_stat_json(char *out, size_t out_sz, const char *wrap_key);
const char *instr_band(void);
int         instr_rssi(void);

/* ---- health counters, since boot -----------------------------------------------------
 * Each has one writer task today (named below), but they are incremented atomically so
 * a second writer added later cannot lose counts. */
void        instr_note_udp_enomem(void);   /* sink: waited for a free Wi-Fi TX buffer */
void        instr_note_send_err(void);     /* sink: a send failed outright */
void        instr_note_tcp_open(bool ok);  /* sink: a TCP connection made, or one that failed */
void        instr_note_linkdrop(void);     /* ctl: a linkdrop command was served */
