/* pipe.h — bytes in, bytes out.
 *
 * MEASUREMENT CONTRACT (parity-checked). A source fills fixed-size buffers; a sink
 * drains them to the network. Two queues decouple the two sides so one core can absorb
 * a Wi-Fi hiccup.
 *
 * THE BYTE-COUNTING POINT IS pipe_note_sent(), called by the sink once a buffer has
 * been handed to the socket layer. Every goodput number in this project is counted
 * there and nowhere else. Moving it changes what the number means. */
#pragma once
#include <stdint.h>
#include <stddef.h>

#define PIPE_BUF_BYTES  8192
#define PIPE_NUM_BUFS   6

typedef struct {
    uint64_t bytes_sent;
    uint64_t frames_sent;
    uint32_t dropped;        /* source could not get a buffer: pool starvation */
    uint32_t starve_events;
} pipe_stats_t;

void      pipe_init(void);
void      pipe_run(void);            /* start sink + source tasks */
uint8_t  *pipe_acquire(void);
size_t    pipe_capacity(void);
void      pipe_submit(uint8_t *buf, size_t len);
void      pipe_release(uint8_t *buf);
int       pipe_take(uint8_t **buf, size_t *len);
void      pipe_recycle(uint8_t *buf);
void      pipe_note_sent(size_t bytes, uint32_t frames);
void      pipe_note_drop(void);
void      pipe_stats(pipe_stats_t *out);
void      pipe_reset_stats(void);
void      pipe_set_connected(int up);
void      pipe_wait_connected(void);

/* A transport backend opens, sends and closes. It never counts bytes — that happens
 * once, in pipe.c, so the counting point cannot drift between transports or chips. */
typedef struct {
    const char *name;
    int  (*open)(void);
    int  (*send)(int fd, const uint8_t *buf, size_t len);
    void (*close)(int fd);
} transport_ops_t;

const transport_ops_t *transport_udp(void);
const transport_ops_t *transport_tcp(void);

/* Provided by the selected ingress translation unit. */
void      source_start(void);
void      source_stop(void);

/* Achieved source rate. Reported alongside the commanded rate so a
 * source-limited cell is labelled as such instead of being read as a radio
 * ceiling (spec §1.3). */
uint64_t  source_achieved_bps(void);
