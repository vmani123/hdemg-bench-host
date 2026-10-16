/* ctl.h — the H7 control plane: line-oriented ASCII over the VCP, one JSON reply per
 * command. Deliberately the same verbs as the ESP's UDP control plane
 * (firmware/bench_common/control.c): hello / cfg / start / stat / stop. */
#pragma once

#define CTL_PROTO 1

void ctl_init(int cm4_synced);
void ctl_poll(void);
