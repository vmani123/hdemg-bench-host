/* instr.h — on-device instrumentation.
 *
 * MEASUREMENT CONTRACT. CPU idle percentage is the observable that settles the
 * single-core question (plan §2.3): idle -> 0 at the knee means the core is the limit;
 * idle still above 20% means the limit is RF, the AP or protocol overhead and core
 * count is irrelevant. The negotiated PHY rate is what settles whether the access point
 * ever offered 802.11ax (plan §7.3b). Both are reported every run. */
#pragma once
#include <stdint.h>
#include <stddef.h>

void        instr_start(void);
void        instr_stat_json(char *out, size_t out_sz, const char *wrap_key);
const char *instr_band(void);
int         instr_rssi(void);
