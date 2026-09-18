/* sink_udp.c — UDP transport backend.
 *
 * A backend only opens, sends and closes. It does NOT count bytes: the single
 * byte-counting point lives in pipe.c so that it cannot drift between transports or
 * between chips. */
#include "pipe.h"
#include "control.h"
#include "lwip/sockets.h"

static int udp_open(void)
{
    return socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
}

static int udp_send(int fd, const uint8_t *buf, size_t len)
{
    const run_cfg_t *cfg = control_cfg();
    struct sockaddr_in dst = { .sin_family = AF_INET, .sin_port = htons(cfg->dst_port) };
    inet_pton(AF_INET, cfg->dst_ip, &dst.sin_addr);
    /* One datagram per pool buffer, whole frames only, so the receiver can reframe a
     * datagram without a length field. */
    return sendto(fd, buf, len, 0, (struct sockaddr *)&dst, sizeof(dst));
}

static void udp_close(int fd) { if (fd >= 0) close(fd); }

static const transport_ops_t s_udp = {
    .name = "udp", .open = udp_open, .send = udp_send, .close = udp_close,
};

const transport_ops_t *transport_udp(void) { return &s_udp; }
