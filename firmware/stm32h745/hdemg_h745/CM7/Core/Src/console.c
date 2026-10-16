/* console.c — USART3 (ST-LINK VCP), 115200 8N1, interrupt-driven rings. */
#include <string.h>
#include "console.h"
#include "board.h"

#define RX_CAP 512U          /* powers of two */
#define TX_CAP 4096U
#define LINE_CAP 256U

static UART_HandleTypeDef huart;
static volatile uint8_t  rx_buf[RX_CAP];
static volatile uint32_t rx_head, rx_tail;          /* ISR writes head, main reads tail */
static volatile uint8_t  tx_buf[TX_CAP];
static volatile uint32_t tx_head, tx_tail;          /* main writes head, ISR reads tail */
static volatile uint32_t tx_dropped;
static char     line[LINE_CAP];
static uint32_t line_len;
static int      line_overflow;

void console_init(void)
{
    GPIO_InitTypeDef g = {0};
    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_USART3_CLK_ENABLE();
    g.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = GPIO_PULLUP;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Alternate = GPIO_AF7_USART3;
    HAL_GPIO_Init(GPIOD, &g);

    huart.Instance = USART3;
    huart.Init.BaudRate = 115200;
    huart.Init.WordLength = UART_WORDLENGTH_8B;
    huart.Init.StopBits = UART_STOPBITS_1;
    huart.Init.Parity = UART_PARITY_NONE;
    huart.Init.Mode = UART_MODE_TX_RX;
    huart.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart.Init.OverSampling = UART_OVERSAMPLING_16;
    huart.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart.Init.ClockPrescaler = UART_PRESCALER_DIV1;
    huart.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
    if (HAL_UART_Init(&huart) != HAL_OK) board_fatal();

    USART3->CR1 |= USART_CR1_RXNEIE_RXFNEIE;
    HAL_NVIC_SetPriority(USART3_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
}

void USART3_IRQHandler(void)
{
    uint32_t isr = USART3->ISR;

    if (isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE | USART_ISR_PE)) {
        USART3->ICR = USART_ICR_ORECF | USART_ICR_FECF | USART_ICR_NECF | USART_ICR_PECF;
    }
    if (isr & USART_ISR_RXNE_RXFNE) {
        uint8_t c = (uint8_t)USART3->RDR;
        uint32_t next = (rx_head + 1U) & (RX_CAP - 1U);
        if (next != rx_tail) { rx_buf[rx_head] = c; rx_head = next; }
    }
    if ((isr & USART_ISR_TXE_TXFNF) && (USART3->CR1 & USART_CR1_TXEIE_TXFNFIE)) {
        if (tx_tail != tx_head) {
            USART3->TDR = tx_buf[tx_tail];
            tx_tail = (tx_tail + 1U) & (TX_CAP - 1U);
        } else {
            USART3->CR1 &= ~USART_CR1_TXEIE_TXFNFIE;
        }
    }
}

void console_write(const char *s, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        uint32_t next = (tx_head + 1U) & (TX_CAP - 1U);
        if (next == tx_tail) { tx_dropped += (uint32_t)(n - i); break; }
        tx_buf[tx_head] = (uint8_t)s[i];
        tx_head = next;
    }
    /* CR1 is also written by the ISR: make the read-modify-write atomic against it. */
    NVIC_DisableIRQ(USART3_IRQn);
    USART3->CR1 |= USART_CR1_TXEIE_TXFNFIE;
    NVIC_EnableIRQ(USART3_IRQn);
}

void console_puts(const char *s) { console_write(s, strlen(s)); }

uint32_t console_tx_dropped(void) { return tx_dropped; }

int console_readline(char *buf, size_t cap)
{
    while (rx_tail != rx_head) {
        char c = (char)rx_buf[rx_tail];
        rx_tail = (rx_tail + 1U) & (RX_CAP - 1U);
        if (c == '\r') continue;
        if (c == '\n') {
            int overflow = line_overflow;
            uint32_t n = line_len;
            line_len = 0;
            line_overflow = 0;
            if (overflow || n == 0U || n >= cap) continue;
            memcpy(buf, line, n);
            buf[n] = '\0';
            return (int)n;
        }
        if (line_len < LINE_CAP - 1U) line[line_len++] = c;
        else line_overflow = 1;
    }
    return -1;
}
