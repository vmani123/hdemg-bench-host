/* hdemg_h745 (CM7) — Stage 2 ingress master. The H745 plays the FPGA: it generates
 * paced 128-channel frames and pushes them into the ESP over QUADSPI (2a) or SDIO (2b).
 * See docs/STM32_STAGE2_GUIDE.md. Everything runs on the CM7; the CM4 image only parks
 * its domain and idles. */
#include "main.h"
#include "board.h"
#include "console.h"
#include "ctl.h"
#include "gen.h"

int main(void)
{
    board_mpu_config();
    board_cache_enable();

    /* Dual-core boot: wait for the CM4 to park D2 in STOP, bring the clocks up, then
     * release it. A missing or stale CM4 image is reported in `hello`, not fatal. */
    int cm4_synced = board_wait_cm4_stopped();

    HAL_Init();
    board_clock_config();
    if (cm4_synced) cm4_synced = board_release_cm4();

    board_leds_init();
    board_us_timer_init();
    board_ref_clock_init();         /* 32.768 kHz crystal; falls back to TIM2 if it is dead */
    console_init();
    gen_init();
    ctl_init(cm4_synced);

    uint32_t beat = HAL_GetTick();
    int on = 0;
    while (1) {
        ctl_poll();
        if (HAL_GetTick() - beat >= 500U) {
            beat += 500U;
            on = !on;
            led_green(on);
        }
    }
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
    (void)file; (void)line;
    board_fatal();
}
#endif
