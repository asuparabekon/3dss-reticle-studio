"""The reticle settings file (<drawing>.reticle.ini) that sits next to the SVG."""

import configparser
import math
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE = """; Reticle settings for {svg}. Lines starting with ; are comments.
; Every key has a default except texture; change what you need and run make_reticle again.

[reticle]
; the game texture this reticle replaces, exactly as the sight's model or its 3DSS reticle profile names it
; (wpn\\... without .dds). The atlas is written to that path and overrides the bitmap like any texture mod.
texture =
; angle units of the drawing: moa or mil
units = moa
; ffp = first focal plane (the reticle grows with the zoom), sfp = second focal plane (it keeps its size)
focal_plane = ffp
; sfp only: the magnification at which the reticle's subtensions are true
calibrated_magnification = 10
; normal, or high for a sharper reticle at a larger texture
quality = normal
; auto, none or bc5. auto and bc5 compress the texture (BC5): a tile keeps one true distance (a quarter of the
; memory) where its sharpest corners stay as the quality checks want them at strong magnification, else a
; multi-channel distance (half). auto keeps the texture uncompressed when, compressed, it misses the corners
; quality check; none never compresses
compression = auto
; yes for a holographic sight (EOTech style): softer, dimmer, with laser speckle
holo = no
; optional: the highest magnification the reticle is ever seen at (a red dot or holo with a 3x magnifier: 3);
; keeps the texture no larger than that needs. Empty = size by the thinnest stroke alone
max_zoom =
; optional: SVG units per angle unit; overrides the "calibrate" layer
scale =
; optional: x, y of the bore centre in SVG units; overrides the "center" layer and the centre dot
center =
; true = the reticle shows at its real angular size; 3dss = at the size the bitmap it replaces shows in the
; 3DSS shaders (gameplay scale), following that bitmap's mapping in every path
scale_mode = true
; 3dss only: angle units across the replaced bitmap's width (one Mark Switch cell for grid textures),
; measured on its solid lines, e.g. a feature 300 px from the centre of a 4096 px bitmap at 70 MOA: 4096 * 70 / 300
legacy_span =

[illumination]
colour_mode = authored
switch_states =
; colour of the lit previews (the game takes it from the sight's Mark Switch colours): a 3DSS colour name or r, g, b
preview = scope_red2
; optional, for sights whose marks carry their own colour (no ms_colors, like the 3DSS EOTech): one colour per
; mark, separated by |, e.g. 1, 0.3, 0.3 | 0.35, 0.9, 0.35. The game uses them while the sight's colour is black.
mark_colours =
; painted colours that light up with the illumination (comma separated, e.g. #00ff00)
paint_glows =
; brightness of the lit parts at the top step, in scene luminance: the game scales it by the eye's current
; exposure, so the same value is a hot dot with a tight halo by day and a big bloom at night. 2 = dim, 8 = an
; LPVO dot, 16 and more = a bright holographic sight. Range 0.5 to 64
illumination_luminance = 8
; optional: brightness of each Mark Switch mark from the first, 0 to 1 (e.g. 0, 0.25, 0.5, 1); empty = the
; sight's Mark Switch colours set it (a dimmer colour is a lower step)
brightness_steps =
; glow (halo and veil) energy relative to the core, 1 = normal, 0 = none, up to 8
glow_strength = 1
; size of the glow relative to the game's default lobes, 1 = normal, 0.25 to 4. The game uses the value of the
; sight being drawn (the largest when two are in view); the player's glow size setting scales it
glow_size = 1

[holo]
; holographic sights only (holo = yes); every key is optional, remove the ; to set one. Angles are in the drawing's units (MOA or mil)
; laser speckle fixed in reticle angle, 0 = none .. 1 = deep
; speckle = 0.3
; speckle grain size, 0.05 .. 2.5
; speckle_size = 0.35
; speckle grains it shifts per millimetre the eye moves (shimmer while the eye moves), 0 .. 2.5
; speckle_eye_shift = 0.23
; smoothly evolving noise over time, 0 = static .. 1 = deep
; noise_animated = 0
; how fast the noise evolves, changes per second, 0.1 .. 15
; noise_speed = 1
; noise grain size, 0.05 .. 2.5
; noise_size = 1
; edge softness of the hologram, 1 = as sharp as an LED reticle .. 4
; softness = 1.25
; brightness against an LED reticle of the same luminance, 0.05 .. 2.5
; level = 0.75
; a faint second image from an internal reflection: its brightness 0 .. 0.5 and offset x, y (-15 .. 15)
; ghost_level = 0
; ghost_offset = 0, 0
; colour separation at the edges, 0 .. 2.5
; fringe = 0
; brightness flicker at the lowest brightness steps (like pulse-width dimming), 0 .. 1
; flicker = 0

[checks]
; optional subtension checks, each "shape id, side, value in units[, tolerance]"
; side is left, right, top, bottom, centre_x or centre_y; dot and ring check the illuminated centre
; example:  tick_20 = path16, centre_x, 20
; dot = 1.0
; ring = 4.0, 5.5
"""

COLOURS_3DSS = {
    "scope_black": (0, 0, 0),
    "scope_red": (1, 0.2, 0.2),
    "scope_red_dim": (0.4, 0.05, 0.05),
    "scope_red1": (0.6, 0, 0),
    "scope_red2": (1, 0.3, 0.3),
    "scope_orange": (1, 0.5, 0),
    "scope_amber": (1, 0.75, 0),
    "scope_blue": (0.16, 0.69, 0.9),
    "scope_green": (0.1, 1, 0.1),
}


HOLO_RANGES = (
    ("speckle", 0, 1),
    ("speckle_size", 0.05, 2.5),
    ("speckle_eye_shift", 0, 2.5),
    ("noise_animated", 0, 1),
    ("noise_speed", 0.1, 15),
    ("noise_size", 0.05, 2.5),
    ("softness", 1, 4),
    ("level", 0.05, 2.5),
    ("ghost_level", 0, 0.5),
    ("fringe", 0, 2.5),
    ("flicker", 0, 1),
)


class SettingsError(ValueError):
    pass


@dataclass
class Settings:
    texture: str = ""
    name: str = ""
    units: str = "moa"
    focal_plane: str = "ffp"
    calibrated_magnification: float = 1.0
    quality: str = "normal"
    scale: float | None = None
    center: tuple | None = None
    max_zoom: float | None = None
    holo: bool = False
    colour_mode: str = "legacy"
    switch_states: tuple = ()
    materials: tuple = ()
    scale_mode: str = "true"
    legacy_span: float = 0.0
    compression: str = "none"
    luminance: float = 8.0
    steps: tuple | list = ()
    glow_strength: float = 1.0
    glow_size: float = 1.0
    hologram: dict = field(default_factory=dict)
    preview_colour: tuple = COLOURS_3DSS["scope_red2"]
    mark_colours: list = field(default_factory=list)
    paint_glows: list = field(default_factory=list)
    checks: list = field(default_factory=list)


def template_path(svg):
    svg = Path(svg)
    return svg.with_name(svg.stem + ".reticle.ini")


def write_template(svg):
    path = template_path(svg)
    path.write_text(TEMPLATE.format(svg=Path(svg).name))
    return path


def _list(text):
    return [v.strip() for v in (text or "").split(",") if v.strip()]


def colour(value):
    if value in COLOURS_3DSS:
        return COLOURS_3DSS[value]
    parts = _list(value)
    if len(parts) == 3:
        try:
            values = tuple(float(p) for p in parts)
            if all(math.isfinite(v) and 0 <= v <= 1 for v in values):
                return values
        except ValueError:
            pass
    raise SettingsError(f"colour '{value}' is neither a 3DSS colour name nor r, g, b")


def _number(text, key, lo, hi):
    try:
        v = float(text)
    except ValueError:
        raise SettingsError(f"{key} '{text}': a number from {lo:g} to {hi:g}")
    if not lo <= v <= hi:
        raise SettingsError(f"{key} {v:g} is outside {lo:g} to {hi:g}")
    return v


def _finite(text, key, positive=False):
    try:
        value = float(text)
    except ValueError:
        raise SettingsError(f"{key}: enter a number") from None
    if not math.isfinite(value) or (positive and value <= 0):
        raise SettingsError(f"{key}: enter a finite {'positive ' if positive else ''}number")
    return value


def load(path):
    return parse(Path(path).read_text(encoding="utf-8"))


def parse(text):
    """Parse a document snapshot with the same validation as an on-disk INI."""
    # only ; starts an inline comment: colours like #00ff00 are values
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    cp.read_string(text)
    get = lambda section, key, default="": (
        cp.get(section, key, fallback=default) or default
    ).strip()
    s = Settings()
    s.texture = get("reticle", "texture").replace("/", "\\").strip("\\")
    if s.texture.lower().endswith(".dds"):
        s.texture = s.texture[:-4]
    if not s.texture:
        raise SettingsError(
            "texture: name the game texture this reticle replaces, e.g. wpn\\scope_reticles\\..."
        )
    if any(c in s.texture for c in ':*?"<>|') or ".." in s.texture:
        raise SettingsError(
            f"texture '{s.texture}': a game texture path like wpn\\folder\\name"
        )
    s.units = get("reticle", "units", "moa").lower()
    if s.units in ("mils", "mrad"):
        s.units = "mil"
    if s.units not in ("moa", "mil"):
        raise SettingsError(f"units '{s.units}': use moa or mil")
    s.focal_plane = get("reticle", "focal_plane", "ffp").lower()
    if s.focal_plane not in ("ffp", "sfp"):
        raise SettingsError(f"focal_plane '{s.focal_plane}': use ffp or sfp")
    s.calibrated_magnification = _finite(
        get("reticle", "calibrated_magnification", "1"),
        "calibrated_magnification",
        positive=True,
    )
    s.quality = get("reticle", "quality", "normal").lower()
    if s.quality not in ("normal", "high"):
        raise SettingsError(f"quality '{s.quality}': use normal or high")
    s.compression = get("reticle", "compression", "auto").lower()
    if s.compression not in ("auto", "none", "bc5"):
        raise SettingsError(f"compression '{s.compression}': use auto, none or bc5")
    holo = get("reticle", "holo", "no").lower()
    if holo not in ("yes", "true", "1", "on", "no", "false", "0", "off"):
        raise SettingsError("holo: use yes or no")
    s.holo = holo in ("yes", "true", "1", "on")
    max_zoom = get("reticle", "max_zoom")
    s.max_zoom = _number(max_zoom, "max_zoom", 0.5, 100) if max_zoom else None
    if s.max_zoom is not None and not 0.5 <= s.max_zoom <= 100:
        raise SettingsError(f"max_zoom '{max_zoom}': a magnification such as 1, 3 or 10")
    scale = get("reticle", "scale")
    s.scale = _finite(scale, "scale", positive=True) if scale else None
    centre = get("reticle", "center") or get("reticle", "centre")
    if centre:
        coordinates = centre.split(",")
        if len(coordinates) != 2:
            raise SettingsError("center: enter two coordinates, x, y")
        s.center = tuple(_finite(v, "center") for v in coordinates)
    s.scale_mode = get("reticle", "scale_mode", "true").lower()
    if s.scale_mode not in ("true", "3dss"):
        raise SettingsError(f"scale_mode '{s.scale_mode}': use true or 3dss")
    span = get("reticle", "legacy_span")
    s.legacy_span = (
        _number(span, "legacy_span", 1, 8191) if span and s.scale_mode == "3dss" else 0.0
    )
    if s.scale_mode == "3dss" and not 1.0 <= s.legacy_span <= 8191.0:
        raise SettingsError(
            f"legacy_span '{span}': the angle units across the replaced bitmap, 1 to 8191"
        )
    from reticle_states import parse_states

    s.colour_mode = get("illumination", "colour_mode", "legacy").lower()
    if s.colour_mode not in ("legacy", "authored", "3dss"):
        raise SettingsError(
            "Colour mode must be authored or 3dss (legacy for existing projects)"
        )
    s.switch_states = parse_states(get("illumination", "switch_states"))
    s.preview_colour = colour(get("illumination", "preview", "scope_red2"))
    s.mark_colours = [
        colour(v.strip()) for v in get("illumination", "mark_colours").split("|") if v.strip()
    ]
    s.paint_glows = _list(get("illumination", "paint_glows"))
    s.luminance = _number(
        get("illumination", "illumination_luminance", "8"), "illumination_luminance", 0.5, 64
    )
    s.steps = [
        _number(v, "brightness_steps", 0, 1)
        for v in _list(get("illumination", "brightness_steps"))
    ]
    if len(s.steps) > 8:
        raise SettingsError("brightness_steps: at most 8 steps")
    s.glow_strength = _number(
        get("illumination", "glow_strength", "1"), "glow_strength", 0, 7.9
    )
    s.glow_size = _number(get("illumination", "glow_size", "1"), "glow_size", 0.25, 4)
    s.hologram = {}
    for key, lo, hi in HOLO_RANGES:
        text = get("holo", key)
        if text:
            s.hologram[key] = _number(text, key, lo, hi)
    offset = _list(get("holo", "ghost_offset"))
    if offset:
        if len(offset) != 2:
            raise SettingsError("ghost_offset: two numbers, x and y, e.g. 3, -2")
        s.hologram["ghost_offset"] = tuple(_number(v, "ghost_offset", -15, 15) for v in offset)
    # Keep dormant hologram controls when the author toggles holo off in the GUI.
    # The atlas flag decides whether the shader evaluates these values.
    s.checks = []
    if cp.has_section("checks"):
        for key, value in cp.items("checks"):
            s.checks.append((key, _list(value)))
    s.name = s.texture.split("\\")[-1]
    return s
