/* linkdrop.h — take the Wi-Fi link down on command and bring it back (`linkdrop <ms>`,
 * protocol v2, docs/CAMPAIGN_PROTOCOL.md §1.2).
 *
 * The stress a UDP sender cannot get from the receiver: the station leaves the access
 * point for a set time while a stream keeps running, then rejoins the same access point
 * with the same band, width and power settings. */
#pragma once
#include <stdint.h>

#define LINKDROP_MIN_MS    100u
#define LINKDROP_MAX_MS  60000u

void        linkdrop_init(void);      /* once, after the target's own STA_START handlers */
uint32_t    linkdrop_clamp_ms(uint32_t ms);
/* Two steps so the reply can go out first: arm() creates the task, parked, and returns
 * NULL or the reason it refused (one already in progress, no memory); fire() releases it
 * once the reply has been sent. */
const char *linkdrop_arm(uint32_t ms);
void        linkdrop_fire(void);
