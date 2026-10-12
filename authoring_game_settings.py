"""Read-only import of game exposure and reticle-glow controls."""

KEYS = {
    "r2_tonemap_adaptation": "adaptation_speed",
    "r2_tonemap_middlegray": "middle_grey",
    "r2_tonemap_amount": "adaptation_amount",
    "r2_tonemap_lowlum": "game_floor",
    "r__reticle_msdf_glow_lowlum": "glow_floor",
    "r__reticle_msdf_brightness": "brightness",
    "r__reticle_msdf_holo_level": "holo_level",
    "r__reticle_msdf_speckle": "speckle",
    "r__reticle_msdf_line_weight": "line_weight",
    "r__reticle_msdf_glow_shoulder": "shoulder",
    "r__reticle_msdf_glow_tint": "tint",
    "r__reticle_msdf_glow_halo": "halo",
    "r__reticle_msdf_glow_exposure": "glow_exposure",
}


def import_settings(path):
    result = {}
    tone_enabled = True
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split(";", 1)[0].strip().split(None, 1)
        if len(parts) != 2:
            continue
        name, value = parts
        if name == "r2_tonemap":
            tone_enabled = value.strip().lower() == "on"
        elif name in KEYS:
            result[KEYS[name]] = float(value)
        elif name in ("r__reticle_msdf_glow_size", "r__reticle_msdf_glow_weight"):
            values = [float(v) for v in value.strip("() ").split(",")]
            if len(values) != 3:
                raise ValueError(f"{name} needs three numbers")
            suffix = "sigma" if name.endswith("size") else "weight"
            result.update(
                {f"{label}_{suffix}": v for label, v in zip(("tight", "mid", "wide"), values)}
            )
    if not tone_enabled:
        result["adaptation_amount"] = 0
    if not result:
        raise ValueError("No supported exposure or reticle glow settings found")
    return result
