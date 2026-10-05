/* ctl.c — control plane over the VCP, and the run state machine. See ctl.h.
 *
 *   hello                         identity, clocks, current configuration
 *   cfg k=v ...                   rate_bps= dur_s= link=none|qspi|sdio seed= frame_bytes=270
 *                                 qspi_hz= qspi_rd_hz= qspi_dummy= qspi_addr_lines=1|4
 *                                 qspi_sshift=0|1 qspi_even=0|1 qspi_nocredit=0|1 sdio_hz= batch=
 *   probe                         open the configured link, report whether a slave answers, close it
 *   start                         open the link (fails if no slave answers), start generating
 *   stat                          counters, any time
 *   stop                          end the run now; reply carries the summary
 *   reboot                        system reset
 *   q_open / q_rd addr= / q_tx frames= end= rep= gap_us= / q_close
 *                                 QUADSPI bring-up without a run (logic analyzer, guide §7.1)
 *
 * One JSON object per line in reply. 64-bit values are formatted by hand: newlib-nano's
 * printf does not do %llu. */
#include <stdlib.h>
#include <string.h>
#include "ctl.h"
#include "board.h"
#include "console.h"
#include "gen.h"
#include "link.h"

#if __has_include("fw_version.h")
#include "fw_version.h"          /* written by hostrun.sh stm_build: git describe */
#endif
#ifndef FW_SHA
#define FW_SHA "unknown"
#endif

#define DRAIN_US 200000U         /* after generation ends, how long the link may drain the ring */

typedef enum { ST_IDLE = 0, ST_RUNNING, ST_DRAINING, ST_DONE } state_t;

static int        s_cm4_synced;
static state_t    s_state;
static link_cfg_t s_link;
static uint64_t   s_rate_bps;
static uint32_t   s_dur_s = 62;
static uint32_t   s_seed;
static int        s_seed_set;
static uint32_t   s_drain_t0;
static link_stats_t s_final_link;       /* link counters frozen when the run ended */
static int        s_debug_open;         /* q_open is holding the link */
static char       s_line[256];

/* ---- tiny JSON writer -------------------------------------------------------------- */
static char  s_out[1100];
static size_t s_n;
static int   s_first;

static void j_raw(const char *s) { while (*s && s_n < sizeof s_out - 2U) s_out[s_n++] = *s++; }
static void j_u64(uint64_t v)
{
    char t[21];
    int i = 0;
    do { t[i++] = (char)('0' + (v % 10U)); v /= 10U; } while (v);
    while (i && s_n < sizeof s_out - 2U) s_out[s_n++] = t[--i];
}
static void j_begin(void) { s_n = 0; s_first = 1; j_raw("{"); }
static void j_key(const char *k) { if (!s_first) j_raw(","); s_first = 0; j_raw("\""); j_raw(k); j_raw("\":"); }
static void j_u(const char *k, uint64_t v) { j_key(k); j_u64(v); }
static void j_b(const char *k, int v) { j_key(k); j_raw(v ? "true" : "false"); }
static void j_s(const char *k, const char *v)
{
    j_key(k);
    j_raw("\"");
    for (; *v && s_n < sizeof s_out - 3U; v++) {
        if (*v == '"' || *v == '\\' || (unsigned char)*v < 0x20) continue;
        s_out[s_n++] = *v;
    }
    j_raw("\"");
}
static void j_open(const char *k) { j_key(k); j_raw("{"); s_first = 1; }
static void j_close(void) { j_raw("}"); s_first = 0; }
static void j_end(void) { j_raw("}"); s_out[s_n++] = '\n'; console_write(s_out, s_n); }

static void reply_err(const char *err)
{
    j_begin();
    j_b("ok", 0);
    j_s("err", err);
    j_end();
}

/* ---- helpers ------------------------------------------------------------------------ */
static const char *state_name(void)
{
    switch (s_state) {
    case ST_RUNNING:  return "running";
    case ST_DRAINING: return "draining";
    case ST_DONE:     return "done";
    default:          return "idle";
    }
}

static void j_cfg(void)
{
    j_u("rate_bps", s_rate_bps);
    j_u("dur_s", s_dur_s);
    j_u("frame_bytes", GEN_FRAME_BYTES);
    j_s("link", link_name(s_link.kind));
    j_u("seed", s_seed);
    j_u("batch", s_link.batch_max);
    j_u("qspi_hz", s_link.qspi_hz);
    j_u("qspi_rd_hz", s_link.qspi_rd_hz);
    j_u("qspi_dummy", s_link.qspi_dummy);
    j_u("qspi_addr_lines", s_link.qspi_addr_lines);
    j_u("qspi_sshift", s_link.qspi_sshift);
    j_u("qspi_even", s_link.qspi_even);
    j_u("qspi_nocredit", s_link.qspi_nocredit);
    j_u("sdio_hz", s_link.sdio_hz);
}

static void j_run(void)
{
    gen_stats_t g;
    link_stats_t l;
    gen_stats(&g);
    if (s_state == ST_RUNNING || s_state == ST_DRAINING || s_debug_open) link_stats(&l);
    else l = s_final_link;

    uint64_t achieved = 0, link_bps = 0;
    if (g.elapsed_us) {
        achieved = (uint64_t)g.frames * GEN_FRAME_BYTES * 8ULL * 1000000ULL / g.elapsed_us;
        link_bps = l.bytes * 8ULL * 1000000ULL / g.elapsed_us;
    }
    j_s("state", state_name());
    j_b("running", s_state == ST_RUNNING);
    j_s("link", link_name(s_link.kind));
    j_u("commanded_bps", g.rate_bps);
    j_u("achieved_bps", achieved);
    j_u("link_bps", link_bps);
    j_u("elapsed_us", g.elapsed_us);
    j_u("frames", g.frames);
    j_u("ring_drops", g.ring_drops);
    j_u("ring_fill", g.ring_fill);
    j_u("ring_max", g.ring_max);
    j_u("first_seq", g.first_seq);
    j_u("last_seq", g.last_seq);
    j_u("seed", g.seed);
    j_u("link_bytes", l.bytes);
    j_u("xfers", l.xfers);
    j_u("credit_waits", l.credit_waits);
    j_u("ready_timeouts", l.ready_timeouts);
    j_u("link_errors", l.errors);
    j_u("torn_reads", l.torn_reads);
    j_u("credit_errors", l.credit_errors);
    j_u("ready_edges", l.ready_edges);
    j_u("sent", l.sent);
    j_u("loaded", l.last_loaded);
    j_u("completed", l.last_completed);
    j_u("link_hz", l.actual_hz);
    j_u("tx_dropped", console_tx_dropped());
}

static void finish_run(void)
{
    link_stats(&s_final_link);
    link_close();
    led_yellow(0);
    s_state = ST_DONE;
}

/* ---- commands ----------------------------------------------------------------------- */
static void cmd_hello(void)
{
    j_begin();
    j_b("ok", 1);
    j_u("proto", CTL_PROTO);
    j_s("chip", "stm32h745");
    j_s("fw_sha", FW_SHA);
    j_u("sysclk_hz", HAL_RCC_GetSysClockFreq());
    j_u("hclk_hz", HAL_RCC_GetHCLKFreq());
    j_b("cm4_synced", s_cm4_synced);
    j_u("t_us", board_micros());
    j_s("state", state_name());
    j_u("ring_frames", GEN_RING_FRAMES);
    j_u("max_batch", HDEMG_LINK_MAX_FRAMES);
    j_open("cfg");
    j_cfg();
    j_close();
    j_end();
}

/* Applies key=value tokens to *lc and the run settings. Returns NULL or an error text. */
static const char *apply_kv(char *tok, link_cfg_t *lc)
{
    char *eq = strchr(tok, '=');
    if (eq == NULL) return "expected key=value";
    *eq = '\0';
    const char *k = tok, *v = eq + 1;
    uint64_t n = strtoull(v, NULL, 0);

    if      (!strcmp(k, "rate_bps"))        s_rate_bps = n;
    else if (!strcmp(k, "dur_s"))           s_dur_s = (uint32_t)n;
    else if (!strcmp(k, "seed"))          { s_seed = (uint32_t)n; s_seed_set = 1; }
    else if (!strcmp(k, "frame_bytes"))   { if (n != GEN_FRAME_BYTES) return "frame_bytes must be 270"; }
    else if (!strcmp(k, "link")) {
        if      (!strcmp(v, "none")) lc->kind = LINK_NONE;
        else if (!strcmp(v, "qspi")) lc->kind = LINK_QSPI;
        else if (!strcmp(v, "sdio")) lc->kind = LINK_SDIO;
        else return "link must be none, qspi or sdio";
    }
    else if (!strcmp(k, "batch"))           lc->batch_max = (uint32_t)n;
    else if (!strcmp(k, "qspi_hz"))         lc->qspi_hz = (uint32_t)n;
    else if (!strcmp(k, "qspi_rd_hz"))      lc->qspi_rd_hz = (uint32_t)n;
    else if (!strcmp(k, "qspi_dummy"))      lc->qspi_dummy = (uint32_t)n;
    else if (!strcmp(k, "qspi_addr_lines")) { if (n != 1U && n != 4U) return "qspi_addr_lines must be 1 or 4"; lc->qspi_addr_lines = (uint32_t)n; }
    else if (!strcmp(k, "qspi_sshift"))     lc->qspi_sshift = n ? 1U : 0U;
    else if (!strcmp(k, "qspi_even"))       lc->qspi_even = n ? 1U : 0U;
    else if (!strcmp(k, "qspi_nocredit"))   lc->qspi_nocredit = n ? 1U : 0U;
    else if (!strcmp(k, "sdio_hz"))         lc->sdio_hz = (uint32_t)n;
    else return "unknown key";
    return NULL;
}

static int busy(void)
{
    if (s_state == ST_RUNNING || s_state == ST_DRAINING) { reply_err("a run is active"); return 1; }
    return 0;
}

static void cmd_cfg(void)
{
    if (busy()) return;
    char *tok;
    while ((tok = strtok(NULL, " \t")) != NULL) {
        const char *e = apply_kv(tok, &s_link);
        if (e != NULL) {
            j_begin();
            j_b("ok", 0);
            j_s("err", e);
            j_s("key", tok);
            j_end();
            return;
        }
    }
    j_begin();
    j_b("ok", 1);
    j_open("applied");
    j_cfg();
    j_close();
    j_end();
}

static void cmd_start(void)
{
    if (busy()) return;
    if (s_debug_open) { reply_err("q_open is holding the link; q_close first"); return; }
    if (s_rate_bps == 0U) { reply_err("rate_bps not set"); return; }
    if (s_dur_s == 0U) { reply_err("dur_s not set"); return; }
    if (!s_seed_set) s_seed = board_micros() * 2654435761U + 1U;

    const char *err = "";
    int rc = link_open(&s_link, &err);
    if (rc != 0) {
        link_stats_t l;
        link_stats(&l);
        j_begin();
        j_b("ok", 0);
        j_s("err", err);
        j_u("code", (uint64_t)(uint32_t)(-rc));
        j_u("read_value", l.last_loaded);
        j_u("link_errors", l.errors);
        j_end();
        return;
    }
    memset(&s_final_link, 0, sizeof s_final_link);
    if (gen_start(s_rate_bps, s_dur_s, s_seed) != 0) {
        link_close();
        reply_err("generator refused to start");
        return;
    }
    s_state = ST_RUNNING;
    led_yellow(1);

    gen_stats_t g;
    link_stats_t l;
    gen_stats(&g);
    link_stats(&l);
    j_begin();
    j_b("ok", 1);
    j_u("t0_us", g.t0_us);
    j_u("seed", g.seed);
    j_u("first_seq", g.first_seq);
    j_s("link", link_name(s_link.kind));
    j_u("link_hz", l.actual_hz);
    j_u("loaded", l.last_loaded);
    j_u("completed", l.last_completed);
    j_end();
    s_seed_set = 0;                 /* a seed applies to one run unless set again */
}

/* Link check without a run: lets the host refuse a cell in a second instead of
 * discovering sixty seconds later that nothing was listening. */
static void cmd_probe(void)
{
    if (busy()) return;
    if (s_debug_open) { reply_err("q_open is holding the link; q_close first"); return; }
    const char *err = "";
    int rc = link_open(&s_link, &err);
    link_stats_t l;
    link_stats(&l);
    if (rc == 0) link_close();
    j_begin();
    j_b("ok", rc == 0);
    if (rc != 0) { j_s("err", err); j_u("code", (uint64_t)(uint32_t)(-rc)); }
    j_s("link", link_name(s_link.kind));
    j_u("link_hz", l.actual_hz);
    j_u("read_value", l.last_loaded);
    j_u("loaded", l.last_loaded);
    j_u("completed", l.last_completed);
    j_u("link_errors", l.errors);
    j_u("torn_reads", l.torn_reads);
    j_end();
}

static void cmd_stat(void)
{
    j_begin();
    j_b("ok", 1);
    j_run();
    j_end();
}

static void cmd_stop(void)
{
    if (s_state == ST_RUNNING || s_state == ST_DRAINING) {
        gen_stop();
        uint32_t t0 = board_micros();
        while (gen_fill() != 0U && (uint32_t)(board_micros() - t0) < 50000U) link_poll(1);
        finish_run();
    }
    j_begin();
    j_b("ok", 1);
    j_open("summary");
    j_run();
    j_close();
    j_end();
    if (s_state == ST_DONE) s_state = ST_IDLE;
}

/* ---- QUADSPI bring-up verbs ----------------------------------------------------------- */
static uint8_t s_test[HDEMG_LINK_MAX_FRAMES * GEN_FRAME_BYTES] AXI_RING_ATTR;

static uint64_t arg_u(const char *name, uint64_t dflt, char **toks, int ntok)
{
    size_t ln = strlen(name);
    for (int i = 0; i < ntok; i++) {
        if (!strncmp(toks[i], name, ln) && toks[i][ln] == '=') return strtoull(toks[i] + ln + 1U, NULL, 0);
    }
    return dflt;
}

static void cmd_q(const char *verb)
{
    char *toks[12];
    int ntok = 0;
    char *t;
    while (ntok < 12 && (t = strtok(NULL, " \t")) != NULL) toks[ntok++] = t;

    if (busy()) return;

    if (!strcmp(verb, "q_open")) {
        link_cfg_t c = s_link;
        c.kind = LINK_QSPI;
        c.qspi_nocredit = (uint32_t)arg_u("nocredit", 1, toks, ntok);   /* raw by default */
        const char *err = "";
        int rc = link_open(&c, &err);
        link_stats_t l;
        link_stats(&l);
        j_begin();
        j_b("ok", rc == 0);
        if (rc != 0) { j_s("err", err); j_u("read_value", l.last_loaded); }
        j_u("link_hz", l.actual_hz);
        j_u("loaded", l.last_loaded);
        j_u("completed", l.last_completed);
        j_end();
        s_debug_open = (rc == 0);
        return;
    }
    if (!s_debug_open) { reply_err("q_open first"); return; }

    if (!strcmp(verb, "q_close")) {
        link_close();
        s_debug_open = 0;
        j_begin(); j_b("ok", 1); j_end();
    } else if (!strcmp(verb, "q_rd")) {
        uint32_t v = 0;
        uint32_t addr = (uint32_t)arg_u("addr", HDEMG_QSPI_REG_MAGIC, toks, ntok);
        int rc = link_qspi_read_reg(addr, &v);
        link_stats_t l;
        link_stats(&l);
        j_begin();
        j_b("ok", rc == 0);
        j_u("addr", addr);
        j_u("value", v);
        j_u("torn_reads", l.torn_reads);
        j_u("link_errors", l.errors);
        j_end();
    } else if (!strcmp(verb, "q_tx")) {
        uint32_t frames = (uint32_t)arg_u("frames", 1, toks, ntok);
        uint32_t end = (uint32_t)arg_u("end", 1, toks, ntok);
        uint32_t rep = (uint32_t)arg_u("rep", 1, toks, ntok);
        uint32_t gap = (uint32_t)arg_u("gap_us", 1000, toks, ntok);
        uint32_t seq = (uint32_t)arg_u("seq", 1, toks, ntok);
        uint32_t seed = (uint32_t)arg_u("seed", 0, toks, ntok);
        if (frames < 1U || frames > HDEMG_LINK_MAX_FRAMES) { reply_err("frames must be 1..30"); return; }
        if (rep < 1U || rep > 100000U) { reply_err("rep must be 1..100000"); return; }
        uint32_t fails = 0;
        for (uint32_t r = 0; r < rep; r++) {
            for (uint32_t f = 0; f < frames; f++) {
                uint8_t *p = &s_test[f * GEN_FRAME_BYTES];
                hdemg_hdr_t h = { .magic = HDEMG_MAGIC, .type = HDEMG_TYPE_RAW16,
                                  .chip_id = HDEMG_CHIP_COMBINED, .seq = seq,
                                  .t_stm = board_micros(), .n_ch = 128 };
                memcpy(p, &h, HDEMG_HDR_BYTES);
                uint32_t x = hdemg_payload_state0(seq, seed);
                for (uint32_t i = 0; i < HDEMG_PAYLOAD_WORDS; i++) {
                    x = hdemg_xs32(x);
                    memcpy(p + HDEMG_HDR_BYTES + 4U * i, &x, 4);
                }
                seq++;
            }
            if (link_qspi_send_raw(s_test, frames * GEN_FRAME_BYTES, (int)end) != 0) fails++;
            uint32_t t0 = board_micros();
            while ((uint32_t)(board_micros() - t0) < gap) { }
        }
        link_stats_t l;
        link_stats(&l);
        j_begin();
        j_b("ok", fails == 0U);
        j_u("sent_transactions", rep - fails);
        j_u("failed", fails);
        j_u("next_seq", seq);
        j_u("bytes", l.bytes);
        j_u("ready_edges", l.ready_edges);
        j_end();
    } else {
        reply_err("unknown verb");
    }
}

/* ---- entry points ------------------------------------------------------------------- */
void ctl_init(int cm4_synced)
{
    s_cm4_synced = cm4_synced;
    link_cfg_defaults(&s_link);
    s_state = ST_IDLE;
}

void ctl_poll(void)
{
    /* Run service. Generation is in the TIM3 interrupt; this only moves frames out. */
    if (s_state == ST_RUNNING) {
        link_poll(0);
        if (!gen_running()) { s_state = ST_DRAINING; s_drain_t0 = board_micros(); }
    } else if (s_state == ST_DRAINING) {
        link_poll(1);
        if (gen_fill() == 0U || (uint32_t)(board_micros() - s_drain_t0) > DRAIN_US) finish_run();
    }

    if (console_readline(s_line, sizeof s_line) < 0) return;
    char *verb = strtok(s_line, " \t");
    if (verb == NULL) return;

    if      (!strcmp(verb, "hello"))  cmd_hello();
    else if (!strcmp(verb, "cfg"))    cmd_cfg();
    else if (!strcmp(verb, "start"))  cmd_start();
    else if (!strcmp(verb, "probe"))  cmd_probe();
    else if (!strcmp(verb, "stat"))   cmd_stat();
    else if (!strcmp(verb, "stop"))   cmd_stop();
    else if (!strcmp(verb, "reboot")) { j_begin(); j_b("ok", 1); j_end(); HAL_Delay(50); NVIC_SystemReset(); }
    else if (!strncmp(verb, "q_", 2)) cmd_q(verb);
    else {
        j_begin();
        j_b("ok", 0);
        j_s("err", "unknown verb");
        j_s("verb", verb);
        j_end();
    }
}
