/* linkpins.c — the Stage 2 link pin table and the wiring-check pin modes. See linkpins.h. */
#include "linkpins.h"

/* Wire order; the roles name the ESP and the function. Two pins carry two roles, one per
 * C5 link: PD2 is SDMMC1_CMD for SDIO and the software chip select for QSPI, PC9 is
 * SDMMC1_D1 for SDIO and QUADSPI_BK1_IO0 for QSPI. */
const linkpin_t g_linkpins[LINKPINS_N] = {
    { "PB2",  GPIOB, GPIO_PIN_2,  "s3_qspi_clk" },
    { "PG6",  GPIOG, GPIO_PIN_6,  "s3_qspi_cs" },
    { "PD11", GPIOD, GPIO_PIN_11, "s3_qspi_d0" },
    { "PD12", GPIOD, GPIO_PIN_12, "s3_qspi_d1" },
    { "PE2",  GPIOE, GPIO_PIN_2,  "s3_qspi_d2" },
    { "PD13", GPIOD, GPIO_PIN_13, "s3_qspi_d3" },
    { "PG9",  GPIOG, GPIO_PIN_9,  "s3_ready" },
    { "PF10", GPIOF, GPIO_PIN_10, "c5_qspi_clk" },
    { "PD2",  GPIOD, GPIO_PIN_2,  "c5_sdio_cmd/c5_qspi_cs" },
    { "PC9",  GPIOC, GPIO_PIN_9,  "c5_sdio_d1/c5_qspi_d0" },
    { "PF9",  GPIOF, GPIO_PIN_9,  "c5_qspi_d1" },
    { "PF7",  GPIOF, GPIO_PIN_7,  "c5_qspi_d2" },
    { "PF6",  GPIOF, GPIO_PIN_6,  "c5_qspi_d3" },
    { "PG10", GPIOG, GPIO_PIN_10, "c5_ready" },
    { "PC12", GPIOC, GPIO_PIN_12, "c5_sdio_clk" },
    { "PC8",  GPIOC, GPIO_PIN_8,  "c5_sdio_d0" },
    { "PC10", GPIOC, GPIO_PIN_10, "c5_sdio_d2" },
    { "PC11", GPIOC, GPIO_PIN_11, "c5_sdio_d3" },
};

void linkpins_clocks_on(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    __HAL_RCC_GPIOF_CLK_ENABLE();
    __HAL_RCC_GPIOG_CLK_ENABLE();
}

/* HAL_GPIO_DeInit returns the pin to its reset state (analog first, then AF0, no pull,
 * push-pull, low speed; the output data register is left alone) and clears its EXTI
 * line if that line was routed from this port, which drops a READY interrupt's routing.
 * An analog pin is Hi-Z and reads 0. */
void linkpins_release_all(void)
{
    linkpins_clocks_on();
    for (int i = 0; i < LINKPINS_N; i++) HAL_GPIO_DeInit(g_linkpins[i].port, g_linkpins[i].pin);
}

int linkpins_find(const char *name)
{
    for (int i = 0; i < LINKPINS_N; i++) {
        const char *a = g_linkpins[i].name, *b = name;
        while (*a && *b) {
            char c = *b;
            if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
            if (c != *a) break;
            a++;
            b++;
        }
        if (*a == '\0' && *b == '\0') return i;
    }
    return -1;
}

static void input_pulldown(int i)
{
    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_INPUT;
    g.Pull = GPIO_PULLDOWN;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Pin = g_linkpins[i].pin;
    HAL_GPIO_Init(g_linkpins[i].port, &g);
}

void linkpins_inputs(void)
{
    linkpins_release_all();                 /* also clears any READY EXTI routing */
    for (int i = 0; i < LINKPINS_N; i++) input_pulldown(i);
}

void linkpins_drive(int idx, int level)
{
    if (idx < 0 || idx >= LINKPINS_N) return;
    linkpins_inputs();
    const linkpin_t *p = &g_linkpins[idx];
    /* The level goes into the output register first, so the pin goes straight from
     * "pulled-down input" to the requested level with no opposite-level glitch. */
    p->port->BSRR = level ? (uint32_t)p->pin : ((uint32_t)p->pin << 16);
    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Pin = p->pin;
    HAL_GPIO_Init(p->port, &g);
}

int linkpins_level(int idx)
{
    if (idx < 0 || idx >= LINKPINS_N) return 0;
    return (g_linkpins[idx].port->IDR & g_linkpins[idx].pin) ? 1 : 0;
}
