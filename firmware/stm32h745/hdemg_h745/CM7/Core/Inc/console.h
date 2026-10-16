/* console.h — the control-plane UART: USART3 on PD8/PD9, wired to the ST-LINK VCP.
 * Fully interrupt driven and non-blocking in both directions, so answering `stat`
 * mid-run never stalls the link service loop. */
#pragma once
#include <stddef.h>
#include <stdint.h>

void console_init(void);

/* Queue bytes for transmit. Never blocks: what does not fit is dropped and counted. */
void console_write(const char *s, size_t n);
void console_puts(const char *s);
uint32_t console_tx_dropped(void);

/* Returns the length of a complete line copied into buf (terminator stripped, NUL added),
 * or -1 if no full line has arrived yet. Over-long lines are discarded whole. */
int console_readline(char *buf, size_t cap);
