/* bench_pins.c — the `pins` command: the ESP end of the wiring check. See bench_pins.h.
 *
 *   pins list               every link GPIO and the roles it carries
 *   pins in                 every link GPIO an input, pull-down on; reply with levels
 *   pins read               levels, without reconfiguring anything
 *   pins drive <gpio> <0|1> that GPIO an output at that level, every other one an input
 *                           with the pull-down on; reply with levels
 *   pins release            every link GPIO back to its reset state
 *
 * Pull-DOWN, not pull-up: the far end is either Hi-Z (the H7's unused pins) or driving
 * high for the one wire under test, so a pin reads 1 only if that wire reaches it. A
 * driven pin is configured input-and-output, so its own level reads back as it is on the
 * pad rather than as 0. */
#include "bench_pins.h"
#include "control.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include "driver/gpio.h"

static const bench_link_pin_t *find(int gpio)
{
    size_t n;
    const bench_link_pin_t *t = bench_link_pins(&n);
    for (size_t i = 0; i < n; i++) if (t[i].gpio == gpio) return &t[i];
    return NULL;
}

/* Every link GPIO except `skip` (-1 = none) becomes an input with the pull-down on. */
static void inputs_pulled_down(int skip)
{
    size_t n;
    const bench_link_pin_t *t = bench_link_pins(&n);
    uint64_t mask = 0;
    for (size_t i = 0; i < n; i++) if (t[i].gpio != skip) mask |= 1ULL << t[i].gpio;
    if (mask == 0) return;
    gpio_config_t io = {
        .pin_bit_mask = mask, .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE, .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
}

static void drive(int gpio, int level)
{
    inputs_pulled_down(gpio);
    /* Latch the level before the output is enabled, so the pin never shows the other
     * level on its way to this one. */
    gpio_set_level(gpio, (uint32_t)level);
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << gpio, .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE, .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    gpio_set_level(gpio, (uint32_t)level);
}

static void levels_json(char *out, size_t out_sz)
{
    size_t n;
    const bench_link_pin_t *t = bench_link_pins(&n);
    size_t off = (size_t)snprintf(out, out_sz, "{\"ok\":true,\"levels\":{");
    for (size_t i = 0; i < n && off < out_sz; i++) {
        off += (size_t)snprintf(out + off, out_sz - off, "%s\"%d\":%d", i ? "," : "",
                                t[i].gpio, gpio_get_level(t[i].gpio));
    }
    if (off < out_sz) snprintf(out + off, out_sz - off, "}}");
}

static void list_json(char *out, size_t out_sz)
{
    size_t n;
    const bench_link_pin_t *t = bench_link_pins(&n);
    size_t off = (size_t)snprintf(out, out_sz, "{\"ok\":true,\"pins\":{");
    for (size_t i = 0; i < n && off < out_sz; i++) {
        off += (size_t)snprintf(out + off, out_sz - off, "%s\"%d\":[", i ? "," : "", t[i].gpio);
        for (int r = 0; r < BENCH_PIN_MAX_ROLES && t[i].roles[r] && off < out_sz; r++) {
            off += (size_t)snprintf(out + off, out_sz - off, "%s\"%s\"", r ? "," : "",
                                    t[i].roles[r]);
        }
        if (off < out_sz) off += (size_t)snprintf(out + off, out_sz - off, "]");
    }
    if (off < out_sz) snprintf(out + off, out_sz - off, "}}");
}

static bool parse_int(const char *s, long lo, long hi, long *v)
{
    if (s == NULL || *s == 0) return false;
    char *end = NULL;
    long x = strtol(s, &end, 10);
    if (*end != 0 || x < lo || x > hi) return false;
    *v = x;
    return true;
}

void bench_pins_command(char *args, char *out, size_t out_sz)
{
    /* A wired build's link driver has these pins configured for its peripheral (and
     * DMA running on them); reconfiguring them under it would break the link, and its
     * pins would not read what the wiring check expects anyway. */
    if (strcmp(INGRESS_NAME, "synth") != 0) {
        snprintf(out, out_sz, "{\"ok\":false,\"err\":\"pins are owned by the %s link; "
                              "flash a synth build\"}", INGRESS_NAME);
        return;
    }
    char *save = NULL;
    const char *verb = args ? strtok_r(args, " \t", &save) : NULL;
    if (verb && !strcmp(verb, "list")) {
        list_json(out, out_sz);
    } else if (verb && !strcmp(verb, "in")) {
        inputs_pulled_down(-1);
        levels_json(out, out_sz);
    } else if (verb && !strcmp(verb, "read")) {
        levels_json(out, out_sz);
    } else if (verb && !strcmp(verb, "drive")) {
        const char *g = strtok_r(NULL, " \t", &save);
        const char *l = strtok_r(NULL, " \t", &save);
        long gpio, level;
        if (!parse_int(g, 0, 63, &gpio) || !parse_int(l, 0, 1, &level)) {
            snprintf(out, out_sz, "{\"ok\":false,\"err\":\"usage: pins drive <gpio> <0|1>\"}");
        } else if (find((int)gpio) == NULL) {
            snprintf(out, out_sz, "{\"ok\":false,\"err\":\"gpio %ld is not a link pin\"}", gpio);
        } else {
            drive((int)gpio, (int)level);
            levels_json(out, out_sz);
        }
    } else if (verb && !strcmp(verb, "release")) {
        size_t n;
        const bench_link_pin_t *t = bench_link_pins(&n);
        for (size_t i = 0; i < n; i++) gpio_reset_pin(t[i].gpio);
        snprintf(out, out_sz, "{\"ok\":true}");
    } else {
        snprintf(out, out_sz, "{\"ok\":false,\"err\":\"usage: pins list|in|read|"
                              "drive <gpio> <0|1>|release\"}");
    }
}
