/* sink_tcp.c — TCP transport backend.
 *
 * A backend only opens, sends and closes. It does NOT count bytes: the single
 * byte-counting point lives in pipe.c so that it cannot drift between transports or
 * between chips. */
#include "pipe.h"
#include "control.h"
#include "lwip/sockets.h"

static int tcp_open(void)
{
    const run_cfg_t *cfg = control_cfg();
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) return -1;
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    struct sockaddr_in dst = { .sin_family = AF_INET, .sin_port = htons(cfg->dst_port) };
    inet_pton(AF_INET, cfg->dst_ip, &dst.sin_addr);
    if (connect(fd, (struct sockaddr *)&dst, sizeof(dst)) != 0) { close(fd); return -1; }
    return fd;
}

static int tcp_send(int fd, const uint8_t *buf, size_t len)
{
    size_t off = 0;
    while (off < len) {
        int n = send(fd, buf + off, len - off, 0);
        if (n <= 0) return -1;             /* caller reopens; the partial write is lost,
                                            * which is a real event and is counted as
                                            * such rather than papered over. */
        off += (size_t)n;
    }
    return (int)off;
}

static void tcp_close(int fd) { if (fd >= 0) close(fd); }

static const transport_ops_t s_tcp = {
    .name = "tcp", .open = tcp_open, .send = tcp_send, .close = tcp_close,
};

const transport_ops_t *transport_tcp(void) { return &s_tcp; }
