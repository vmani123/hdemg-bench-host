/* sink_tcp.c — TCP transport backend.
 *
 * A backend only opens, sends and closes. It does NOT count bytes: the single
 * byte-counting point lives in pipe.c so that it cannot drift between transports or
 * between chips.
 *
 * Two runtime knobs (protocol v2), both v1 behaviour by default:
 *   nodelay  TCP_NODELAY on the connection this run opens (default on). Off lets lwIP's
 *            Nagle coalesce small writes, which only matters with a small wr_max.
 *   wr_max   the largest single send(), rounded down to whole frames (default 0 = the
 *            whole pool buffer in one call). Smaller writes trade per-call overhead for
 *            earlier hand-off to lwIP; that trade is what the knob measures. */
#include "pipe.h"
#include "control.h"
#include "instr.h"
#include "lwip/sockets.h"

static int tcp_open(void)
{
    const run_cfg_t *cfg = control_cfg();
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) { instr_note_tcp_open(false); return -1; }
    int nodelay = cfg->nodelay ? 1 : 0;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
    struct sockaddr_in dst = { .sin_family = AF_INET, .sin_port = htons(cfg->dst_port) };
    inet_pton(AF_INET, cfg->dst_ip, &dst.sin_addr);
    if (connect(fd, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
        close(fd);
        instr_note_tcp_open(false);
        return -1;
    }
    instr_note_tcp_open(true);
    return fd;
}

/* Bytes per send(): wr_max rounded down to whole frames, never below one frame, so a
 * write never ends mid-frame. 0 (or a cap at least the buffer) = the whole buffer. */
static size_t write_bytes(size_t len, const run_cfg_t *cfg)
{
    if (cfg->wr_max == 0 || len <= cfg->wr_max) return len;
    uint32_t fb = control_frame_bytes(cfg);
    size_t frames = cfg->wr_max / fb;
    return (frames ? frames : 1) * fb;
}

static int tcp_send(int fd, const uint8_t *buf, size_t len)
{
    size_t step = write_bytes(len, control_cfg());
    size_t off = 0;
    while (off < len) {
        size_t want = (len - off < step) ? len - off : step;
        int n = send(fd, buf + off, want, 0);
        if (n <= 0) {
            instr_note_send_err();
            return -1;                     /* caller reopens; the partial write is lost,
                                            * which is a real event and is counted as
                                            * such rather than papered over. */
        }
        off += (size_t)n;
    }
    return (int)off;
}

static void tcp_close(int fd) { if (fd >= 0) close(fd); }

static const transport_ops_t s_tcp = {
    .name = "tcp", .open = tcp_open, .send = tcp_send, .close = tcp_close,
};

const transport_ops_t *transport_tcp(void) { return &s_tcp; }
