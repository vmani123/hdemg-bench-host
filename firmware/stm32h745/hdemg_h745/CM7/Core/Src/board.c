/* board.c — NUCLEO-H745ZI-Q bring-up. Clock tree and boot handshake follow
 * STM32Cube_FW_H7 V1.12.1 Projects/NUCLEO-H745ZI-Q/Templates/BootCM4_CM7. */
#include "board.h"

#define HSEM_ID_0 0U

void board_fatal(void)
{
    __disable_irq();
    led_red(1);
    while (1) { }
}

void board_mpu_config(void)
{
    MPU_Region_InitTypeDef r = {0};

    HAL_MPU_Disable();

    /* Background: no access to unmapped space, so a speculative read cannot wander
     * into it (ST's standard region 0 for the H7). */
    r.Enable = MPU_REGION_ENABLE;
    r.BaseAddress = 0x00;
    r.Size = MPU_REGION_SIZE_4GB;
    r.AccessPermission = MPU_REGION_NO_ACCESS;
    r.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;
    r.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
    r.IsShareable = MPU_ACCESS_SHAREABLE;
    r.Number = MPU_REGION_NUMBER0;
    r.TypeExtField = MPU_TEX_LEVEL0;
    r.SubRegionDisable = 0x87;
    r.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
    HAL_MPU_ConfigRegion(&r);

    /* AXI SRAM, 512 KiB: normal memory, NOT cacheable. The frame ring lives here and is
     * read by MDMA / IDMA behind the CPU's back; with the write-back D-cache on, a
     * cacheable ring would send stale bytes intermittently and silently. */
    r.BaseAddress = AXI_SRAM_BASE;
    r.Size = MPU_REGION_SIZE_512KB;
    r.AccessPermission = MPU_REGION_FULL_ACCESS;
    r.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;
    r.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
    r.IsShareable = MPU_ACCESS_NOT_SHAREABLE;
    r.Number = MPU_REGION_NUMBER1;
    r.TypeExtField = MPU_TEX_LEVEL1;
    r.SubRegionDisable = 0x00;
    r.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
    HAL_MPU_ConfigRegion(&r);

    HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);
}

void board_cache_enable(void)
{
    SCB_EnableICache();
    SCB_EnableDCache();
}

/* The CM4 boots alongside the CM7 and parks its domain (D2) in STOP until released. */
int board_wait_cm4_stopped(void)
{
    int32_t timeout = 0xFFFF;
    while ((__HAL_RCC_GET_FLAG(RCC_FLAG_D2CKRDY) != RESET) && (timeout-- > 0)) { }
    return timeout >= 0;
}

int board_release_cm4(void)
{
    int32_t timeout = 0xFFFF;
    __HAL_RCC_HSEM_CLK_ENABLE();
    HAL_HSEM_FastTake(HSEM_ID_0);
    HAL_HSEM_Release(HSEM_ID_0, 0);
    while ((__HAL_RCC_GET_FLAG(RCC_FLAG_D2CKRDY) == RESET) && (timeout-- > 0)) { }
    return timeout >= 0;
}

/* HSE bypass 8 MHz (ST-LINK MCO) -> PLL1 M=4 N=400 P=2 -> SYSCLK 400 MHz, HCLK 200 MHz,
 * all APB 100 MHz, VOS1. 480 MHz would need VOS0, which direct SMPS does not allow.
 * The supply itself (direct SMPS) is set before main by ExitRun0Mode(), selected by the
 * USE_PWR_DIRECT_SMPS_SUPPLY build define — do not remove that define: the wrong supply
 * mode locks this board out of SWD. */
void board_clock_config(void)
{
    RCC_ClkInitTypeDef clk = {0};
    RCC_OscInitTypeDef osc = {0};

    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) { }

    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    osc.HSEState = RCC_HSE_BYPASS;
    osc.HSIState = RCC_HSI_OFF;
    osc.CSIState = RCC_CSI_OFF;
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLM = 4;
    osc.PLL.PLLN = 400;
    osc.PLL.PLLFRACN = 0;
    osc.PLL.PLLP = 2;
    osc.PLL.PLLR = 2;
    osc.PLL.PLLQ = 4;
    osc.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;
    osc.PLL.PLLRGE = RCC_PLL1VCIRANGE_1;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) board_fatal();

    clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_D1PCLK1 |
                    RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_D3PCLK1;
    clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clk.SYSCLKDivider = RCC_SYSCLK_DIV1;
    clk.AHBCLKDivider = RCC_HCLK_DIV2;
    clk.APB3CLKDivider = RCC_APB3_DIV2;
    clk.APB1CLKDivider = RCC_APB1_DIV2;
    clk.APB2CLKDivider = RCC_APB2_DIV2;
    clk.APB4CLKDivider = RCC_APB4_DIV2;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_4) != HAL_OK) board_fatal();

    /* I/O compensation cell: needed for clean edges on QUADSPI / SDMMC at speed. */
    __HAL_RCC_CSI_ENABLE();
    __HAL_RCC_SYSCFG_CLK_ENABLE();
    HAL_EnableCompensationCell();
}

void board_leds_init(void)
{
    GPIO_InitTypeDef g = {0};
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Pin = GPIO_PIN_0 | GPIO_PIN_14;
    HAL_GPIO_Init(GPIOB, &g);
    g.Pin = GPIO_PIN_1;
    HAL_GPIO_Init(GPIOE, &g);
}

void led_green(int on)  { HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0,  on ? GPIO_PIN_SET : GPIO_PIN_RESET); }
void led_yellow(int on) { HAL_GPIO_WritePin(GPIOE, GPIO_PIN_1,  on ? GPIO_PIN_SET : GPIO_PIN_RESET); }
void led_red(int on)    { HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, on ? GPIO_PIN_SET : GPIO_PIN_RESET); }

void board_us_timer_init(void)
{
    __HAL_RCC_TIM2_CLK_ENABLE();
    /* Timer kernel clock is 2 x PCLK1 whenever the APB1 prescaler is not 1 (TIMPRE = 0). */
    uint32_t tclk = HAL_RCC_GetPCLK1Freq();
    if ((RCC->D2CFGR & RCC_D2CFGR_D2PPRE1_2) != 0U) tclk *= 2U;
    TIM2->CR1 = 0;
    TIM2->PSC = (tclk / 1000000U) - 1U;
    TIM2->ARR = 0xFFFFFFFFU;
    TIM2->EGR = TIM_EGR_UG;          /* latch the prescaler */
    TIM2->CNT = 0;
    TIM2->CR1 = TIM_CR1_CEN;
}
