/* sink_udp.c — UDP transport backend.
 *
 * A backend only opens, sends and closes. It does NOT count bytes: the single
 * byte-counting point lives in pipe.c so that it cannot drift between transports or
 * between chips. */
#include "pipe.h"
#include "control.h"
#include "hdemg_frame.h"
#include "sdkconfig.h"
#include <errno.h>
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static int udp_open(void)
{
    return socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
}

/* Bytes per datagram: as many WHOLE frames as fit under CONFIG_BENCH_UDP_DGRAM_MAX, so
 * the receiver can reframe a datagram without a length field and no datagram is
 * IP-fragmented. Never less than one frame; 0 means one datagram per pool buffer. */
static size_t datagram_bytes(size_t len, uint32_t frame_bytes)
{
    size_t cap = CONFIG_BENCH_UDP_DGRAM_MAX;
    /* The frame size the source actually used: it replaces a value too small to hold a
     * header with the 128-channel frame, and a datagram must never split a frame. */
    if (frame_bytes < HDEMG_HDR_BYTES + 2) frame_bytes = HDEMG_RAW16_128CH;
    if (cap == 0 || frame_bytes == 0 || len <= cap) return len;
    size_t frames = cap / frame_bytes;
    return (frames ? frames : 1) * frame_bytes;
}

static int udp_send(int fd, const uint8_t *buf, size_t len)
{
    const run_cfg_t *cfg = control_cfg();
    struct sockaddr_in dst = { .sin_family = AF_INET, .sin_port = htons(cfg->dst_port) };
    inet_pton(AF_INET, cfg->dst_ip, &dst.sin_addr);
    size_t step = datagram_bytes(len, cfg->frame_bytes);
    size_t off = 0;
    while (off < len) {
        size_t n = (len - off < step) ? len - off : step;
        int r = sendto(fd, buf + off, n, 0, (struct sockaddr *)&dst, sizeof(dst));
        if (r < 0) {
            /* ENOMEM / ENOBUFS: every Wi-Fi TX buffer is in use. That is back-pressure,
             * not a socket fault (ESP-IDF lwIP guide), so wait for the driver to drain
             * and send the same datagram again. Dropping it instead would turn the
             * device's own queue limit into "network loss" in the ledger, and closing
             * the socket on it only adds churn. A run that stops ends the wait. */
            if ((errno == ENOMEM || errno == ENOBUFS) && control_running()) {
                vTaskDelay(1);
                continue;
            }
            return off ? (int)off : -1;
        }
        off += (size_t)r;
    }
    return (int)off;
}

static void udp_close(int fd) { if (fd >= 0) close(fd); }

static const transport_ops_t s_udp = {
    .name = "udp", .open = udp_open, .send = udp_send, .close = udp_close,
};

const transport_ops_t *transport_udp(void) { return &s_udp; }
