/* ctl.c — control plane over the VCP. See ctl.h. */
#include <stdio.h>
#include <string.h>
#include "ctl.h"
#include "board.h"
#include "console.h"

#if __has_include("fw_version.h")
#include "fw_version.h"          /* written by hostrun.sh stm_build: git describe */
#endif
#ifndef FW_SHA
#define FW_SHA "unknown"
#endif

static int s_cm4_synced;
static char s_line[256];
static char s_out[512];

static void reply(int n)
{
    if (n < 0) return;
    if ((size_t)n >= sizeof s_out) n = (int)sizeof s_out - 1;
    console_write(s_out, (size_t)n);
    console_write("\n", 1);
}

static void cmd_hello(void)
{
    reply(snprintf(s_out, sizeof s_out,
        "{\"ok\":true,\"proto\":%d,\"chip\":\"stm32h745\",\"fw_sha\":\"%s\","
        "\"sysclk_hz\":%lu,\"hclk_hz\":%lu,\"cm4_synced\":%s,\"t_us\":%lu}",
        CTL_PROTO, FW_SHA,
        (unsigned long)HAL_RCC_GetSysClockFreq(), (unsigned long)HAL_RCC_GetHCLKFreq(),
        s_cm4_synced ? "true" : "false", (unsigned long)board_micros()));
}

void ctl_init(int cm4_synced)
{
    s_cm4_synced = cm4_synced;
}

void ctl_poll(void)
{
    if (console_readline(s_line, sizeof s_line) < 0) return;

    char *verb = strtok(s_line, " \t");
    if (verb == NULL) return;

    if (strcmp(verb, "hello") == 0) {
        cmd_hello();
    } else {
        reply(snprintf(s_out, sizeof s_out,
                       "{\"ok\":false,\"err\":\"unknown verb\",\"verb\":\"%.32s\"}", verb));
    }
}
