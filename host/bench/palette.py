"""Chart palette — the validated reference instance, light and dark.

Only the first three categorical slots are used. That is deliberate: this report has
exactly three series (C5 @ 5 GHz, C5 @ 2.4 GHz, S3 @ 2.4 GHz), and the first three slots
are the set that clears the all-pairs colour-vision-deficiency floors in both modes. A
fourth series would put yellow beside orange and fail them.

Slots are assigned to entities, never to rank, so filtering the chart never repaints the
survivors.
"""
LIGHT = {
    "surface": "#fcfcfb",
    "text_primary": "#0b0b0b",
    "text_secondary": "#52514e",
    "text_muted": "#7a7975",
    "grid": "#e4e3df",
    "axis": "#c3c2bd",
    "series": ("#2a78d6", "#eb6834", "#1baf7a"),
    "reference": "#7a7975",
}

DARK = {
    "surface": "#1a1a19",
    "text_primary": "#ffffff",
    "text_secondary": "#c3c2b7",
    "text_muted": "#8f8e86",
    "grid": "#33332f",
    "axis": "#4a4a45",
    "series": ("#3987e5", "#d95926", "#199e70"),
    "reference": "#8f8e86",
}

# Fixed entity -> slot mapping. Never cycled, never reassigned by rank.
ENTITY_ORDER = ("esp32c5@5", "esp32c5@2.4", "esp32s3@2.4")
ENTITY_LABEL = {
    "esp32c5@5": "C5 · 5 GHz",
    "esp32c5@2.4": "C5 · 2.4 GHz",
    "esp32s3@2.4": "S3 · 2.4 GHz",
}


def entity(chip: str, band: str) -> str:
    return f"{chip}@{band}"


def color_for(ent: str, theme: dict) -> str:
    try:
        return theme["series"][ENTITY_ORDER.index(ent)]
    except ValueError:
        return theme["text_muted"]
