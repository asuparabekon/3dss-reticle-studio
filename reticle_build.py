"""Turns a reticle drawing into a self-describing distance field atlas (DDS).

Format 3, RGBA8 UNORM, per tile: RGB = multi-channel signed distance (regenerated for every mip with one
tile-normalised range), A = exact area coverage of that mip's texels. Format 4, BC5 (a quarter of the memory), per
tile: R = the true signed distance with the same range, G = the coverage; the multi-channel median does not survive
block compression, so corners round off slightly under strong magnification. Tile sizes and the atlas layout are
chosen automatically from the thinnest stroke each tile has to resolve; make_reticle.py is the front end authors
use and picks the format.
"""

import math
import struct
import tempfile
from pathlib import Path

import numpy as np

import block_compress as bc
import msdfgen_tool
import raster
import reticle_format as rf
import svg_reticle

GUTTER_FRACTION = 1 / 16  # of the tile width, on every side
MIN_TILE_TEXELS = 8  # smallest mip a tile is sampled from
RESERVED = (
    32  # texels of the header and descriptor cell at mip 0: 8 texels at mip 2, enough for 7 tiles
)
RESERVED_LARGE = 64  # for more tiles: the descriptor still fits mip 2's 16 texel cell
BLOCK = 4  # format 4: texels per header texel (one BC5 block each) along each side; the cell grows to match
HEADER_MIPS = 3  # mips that may be resident mip 0 (texture quality strips 0 to 2) carry a header
HANDOVER = 0.05  # a finer tile hands over to the next one across this fraction of its region
TEXELS_PER_STROKE = {"normal": 1.5, "high": 3.0}
DOT_TEXELS = {"normal": 4.0, "high": 8.0}  # across a dot
FILTER_MARGIN_MOA = (
    6.0  # room around lit shapes for a pixel footprint at low magnification (small lone dots)
)
DETAIL_PAD_MOA = 4.0
MOA_PER_MIL = 3.43774677
PXRANGE = 8  # distance range at mip 0, texels


class Role:
    def __init__(self, key, role, colour, glows, mark=0, material=0, materials=()):
        self.key = key
        self.role = role
        self.colour = colour
        self.glows = glows
        self.mark = mark  # Mark Switch mark, 0 = every mark
        self.material = material
        self.materials = materials
        self.paths = []  # one list of contours per SVG shape, in units
        self.ids = []
        self._outline = None

    @property
    def contours(self):
        return [c for p in self.paths for c in p]

    def outline(self):
        """Overlap-free outline rings of the role, in units."""
        if self._outline is None:
            self._outline = raster.merge(
                [[svg_reticle.contour_polygon(c, 24) for c in p] for p in self.paths]
            )
        return self._outline


def stroke_width(contours):
    """Estimate stroke width from area/perimeter and narrow straight sections."""
    area, perimeter = 0.0, 0.0
    for c in contours:
        q = np.array(svg_reticle.contour_polygon(c, 24))
        area += (q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]).sum() / 2
        perimeter += np.hypot(*(np.roll(q, -1, axis=0) - q).T).sum()
    area = abs(area)
    width = (perimeter - math.sqrt(max(perimeter * perimeter - 16 * area, 0.0))) / 4
    local = narrow_sections(contours, width)
    # The area estimate already handles uniform strokes. Only override it for
    # substantial width changes, not small corner/curve differences.
    return local if local < 0.75 * width else width


def narrow_sections(contours, width):
    """Keep thin stems attached to wide shapes when choosing distance-field resolution.

    Area/perimeter alone hides these features. Opposing straight edges with a long
    shared span give a local stroke width; requiring filled space between them avoids
    measuring gaps between disconnected arms of a contour.
    """
    from shapely.geometry import Point, Polygon

    for contour in contours:
        edges = [segment for segment in contour if segment[0] == "L"]
        polygon = None
        for i, (_, a, b) in enumerate(edges):
            a, b = np.asarray(a), np.asarray(b)
            vector = b - a
            length = np.linalg.norm(vector)
            if length <= 1e-9:
                continue
            tangent = vector / length
            normal = np.array([-tangent[1], tangent[0]])
            for _, c, d in edges[i + 1 :]:
                c, d = np.asarray(c), np.asarray(d)
                other = d - c
                other_length = np.linalg.norm(other)
                if other_length <= 1e-9 or np.dot(tangent, other) > -0.999999 * other_length:
                    continue
                distance = abs(np.dot(c - a, normal))
                if not 1e-6 < distance < width:
                    continue
                start, end = sorted((np.dot(c - a, tangent), np.dot(d - a, tangent)))
                lo, hi = max(0, start), min(length, end)
                if hi - lo < 2 * distance:
                    continue
                if polygon is None:
                    polygon = Polygon(svg_reticle.contour_polygon(contour, 24))
                midpoint = a + tangent * ((lo + hi) / 2) + normal * (np.dot(c - a, normal) / 2)
                if polygon.contains(Point(midpoint)):
                    width = distance
    return width


class Calibration:
    def __init__(self, units_per_user_unit, origin, notes):
        self.units_per_user_unit = units_per_user_unit
        self.origin = origin
        self.notes = notes


def calibrate(svg, settings):
    """Scale and origin. Scale: settings > 'calibrate' layer. Origin: settings > 'center' layer > centre of the
    illuminated centre dot > middle of the drawing."""
    notes, errors = [], []
    unit = settings.units
    if settings.scale:
        upu = 1.0 / settings.scale
        notes.append(f"scale from the settings: {settings.scale:g} SVG units per {unit}")
    elif svg.calibration:
        length, value, cal_unit = svg.calibration
        value_in_unit = (
            value
            if cal_unit == unit
            else (value * MOA_PER_MIL if unit == "moa" else value / MOA_PER_MIL)
        )
        upu = value_in_unit / length
        notes.append(
            f"scale from the calibrate layer: {length:.4f} SVG units = {value:g} {cal_unit}"
        )
    else:
        errors.append(
            "no scale: add a layer named like 'calibrate 10 MOA' holding one line of that length, "
            "or set 'scale' in the settings file"
        )
        return None, errors
    if settings.center:
        origin = settings.center
        notes.append(f"centre from the settings: {origin[0]:g}, {origin[1]:g}")
    elif svg.centre:
        origin = svg.centre
        notes.append(f"centre from the center layer: {origin[0]:.4f}, {origin[1]:.4f}")
    else:
        vb = svg.view_box
        middle = (vb[0] + vb[2] / 2, vb[1] + vb[3] / 2)
        dots = []
        for sh in svg.shapes:
            if sh.role != "illum":
                continue
            for c in sh.contours:
                x0, y0, x1, y1 = svg_reticle.bounds([c])
                w, h = x1 - x0, y1 - y0
                if min(w, h) > 0 and max(w, h) / min(w, h) < 1.15:
                    centre = ((x0 + x1) / 2, (y0 + y1) / 2)
                    dots.append((w * h, math.dist(centre, middle), centre))
        if dots:
            origin = min(dots)[2]
            notes.append(f"centre at the illuminated centre dot: {origin[0]:.4f}, {origin[1]:.4f}")
        else:
            origin = middle
            notes.append(
                "centre at the middle of the drawing (no center layer and no illuminated dot)"
            )
    return Calibration(upu, origin, notes), errors


def load_roles(svg, calibration, glowing_paint=(), materials=False):
    """Shapes grouped by role (ink, illum, one group per paint colour) and Mark Switch mark, converted to units,
    y down. Keys of mark-specific groups end in ' mark N'."""
    ox, oy = calibration.origin
    k = calibration.units_per_user_unit
    glowing = set() if materials else {c.lower() for c in glowing_paint}
    roles = {}
    if materials:
        from reticle_materials import discover, identity

        catalog = discover(svg)
        material_ids = {m.key: i for i, m in enumerate(catalog)}
    for sh in svg.shapes:
        contours = [
            [(seg[0],) + tuple(((p[0] - ox) * k, (p[1] - oy) * k) for p in seg[1:]) for seg in c]
            for c in sh.contours
        ]
        role_name = "paint" if materials and sh.role == "ink" else sh.role
        source_colour = (0, 0, 0) if sh.role == "ink" else sh.colour
        if role_name == "paint" or (materials and role_name == "illum"):
            hexcol = "#" + bytes(source_colour).hex()
            key, colour, glows = (
                role_name + " " + hexcol,
                source_colour,
                role_name == "paint" and hexcol in glowing,
            )
        else:
            key, colour, glows = role_name, source_colour, False
        material = material_ids[identity(sh)] if materials else 0
        if materials:
            key += f" material {material}"
        if sh.mark:
            key += f" mark {sh.mark}"
        if key not in roles:
            roles[key] = Role(
                key,
                rf.ROLES[role_name],
                colour,
                glows,
                sh.mark,
                material,
                catalog if materials else (),
            )
        roles[key].paths.append(contours)
        roles[key].ids.append(sh.source)
    return roles


# --- layout -----------------------------------------------------------------------------------


def pow2_at_least(v, lo=256, hi=2048):
    return int(min(hi, max(lo, 2 ** math.ceil(math.log2(max(v, 1))))))


# screen pixels per MOA at 1x on a 1440p screen (80 degree horizontal field)
PX_PER_MOA_1X = 0.47
TEXELS_PER_PIXEL_CAP = 1.4  # with max_zoom: texels per screen pixel at that zoom; still minified there, so coverage draws it


def density_cap(settings):
    """Texels per unit beyond which no shape needs more, from the settings' max_zoom (None = no cap)."""
    max_zoom = getattr(settings, "max_zoom", None)
    if not max_zoom:
        return None
    moa_per_unit = 1.0 if settings.units == "moa" else MOA_PER_MIL
    return TEXELS_PER_PIXEL_CAP * PX_PER_MOA_1X * max_zoom * moa_per_unit


def measure_shape(contours, quality, cap):
    """Measure density, effective stroke and bounds once for tile planning."""
    width = stroke_width(contours)
    density = TEXELS_PER_STROKE[quality] / max(width, 1e-6)
    bounds = svg_reticle.bounds(contours)
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    # Compact shapes need enough texels across their diameter before switching
    # from coverage to distance-field rendering.
    if max(w, h) < 2 * min(w, h):
        density = max(density, DOT_TEXELS[quality] / max(min(w, h), 1e-6))
        width = min(width, min(w, h) * TEXELS_PER_STROKE[quality] / DOT_TEXELS[quality])
    return (min(density, cap) if cap else density), width, bounds


def plan_role(role, quality, units, cap=None):
    """One tile, or a fine tile over the detailed middle plus a coarse tile over everything when the fine
    detail sits in a small part of a large reticle."""
    moa = 1.0 if units == "moa" else 1 / MOA_PER_MIL
    margin = FILTER_MARGIN_MOA * moa if role.role == rf.ROLE_ILLUM or role.glows else 0
    shapes = [measure_shape(p, quality, cap) for p in role.paths]
    x0, y0 = min(b[0] for _, _, b in shapes), min(b[1] for _, _, b in shapes)
    x1, y1 = max(b[2] for _, _, b in shapes), max(b[3] for _, _, b in shapes)
    full_centre, full_half = ((x0 + x1) / 2, (y0 + y1) / 2), max(x1 - x0, y1 - y0) / 2

    def tile(centre, half, members):
        half_extent = (half + margin) / (1 - 2 * GUTTER_FRACTION) * 1.0001
        need = 2 * half_extent * max(d for d, _, _ in members)
        return dict(
            role=role,
            centre=centre,
            half_extent=half_extent,
            region_half=half,
            size=pow2_at_least(need),
            need=pow2_at_least(need, hi=1 << 20),
            thin=min(w for _, w, _ in members),
        )

    single = tile(full_centre, full_half, shapes)
    best, cost = [single], single["need"] ** 2
    # A narrow stem may extend across the whole drawing. Try each detail-density
    # boundary instead of forcing every nearly-as-thin shape into the centre tile.
    for threshold in sorted({d for d, _, _ in shapes}):
        detail = [b for d, _, b in shapes if d >= threshold]
        dx0, dy0 = min(b[0] for b in detail), min(b[1] for b in detail)
        dx1, dy1 = max(b[2] for b in detail), max(b[3] for b in detail)
        detail_half = (max(dx1 - dx0, dy1 - dy0) / 2 + DETAIL_PAD_MOA * moa) / (1 - HANDOVER)
        if detail_half > 0.5 * full_half:
            continue
        centre = ((dx0 + dx1) / 2, (dy0 + dy1) / 2)
        inner = detail_half * (1 - HANDOVER)

        def inside(bounds):
            return max(abs(bounds[i] - centre[i % 2]) for i in range(4)) <= inner

        fine = tile(centre, detail_half, [shape for shape in shapes if inside(shape[2])])
        outside = [shape for shape in shapes if not inside(shape[2])]
        coarse = tile(full_centre, full_half, outside or shapes)
        candidate_cost = fine["need"] ** 2 + coarse["need"] ** 2
        if candidate_cost < cost:
            best, cost = [fine, coarse], candidate_cost
    return best


def wraps(x, y, bw, bh, box, w, h):
    """The box (x, y, bw, bh) touches another across the edge of a w x h atlas."""
    ox, oy, ow, oh = box
    cols = x < ox + ow and ox < x + bw
    rows = y < oy + oh and oy < y + bh
    return (cols and ((y == 0 and oy + oh == h) or (oy == 0 and y + bh == h))) or (
        rows and ((x == 0 and ox + ow == w) or (ox == 0 and x + bw == w))
    )


class AtlasPackingError(ValueError):
    pass


def pack(tiles, reserved=RESERVED, wrap_safe=False):
    """Places tiles (power-of-two squares, or two of them side by side: "wide") at multiples of their size,
    beside the reserved cell, in the smallest power-of-two atlas (width >= height) that holds them. wrap_safe
    (format 4): the shaders' sampler wraps, so no box touches another across the atlas edge, where a coarse mip's
    filter would reach it (a format 4 multi-channel square holds a distance in its coverage channel).
    """
    order = sorted(tiles, key=lambda t: (-t["size"], -t.get("wide", 1)))
    for area_log in range(12, 25):
        for h_log in range(area_log // 2, -1, -1):
            w, h = 2 ** (area_log - h_log), 2**h_log
            if w < h or w > 4096 or h < reserved:
                continue
            placed, ok = [(0, 0, reserved, reserved)], True
            for t in order:
                s, sw = t["size"], t["size"] * t.get("wide", 1)
                spot = None
                for y in range(0, h - s + 1, s):
                    for x in range(0, w - sw + 1, s):
                        if all(
                            x + sw <= bx or bx + bw <= x or y + s <= by or by + bh <= y
                            for bx, by, bw, bh in placed
                        ) and not (
                            wrap_safe and any(wraps(x, y, sw, s, box, w, h) for box in placed)
                        ):
                            spot = (x, y)
                            break
                    if spot:
                        break
                if not spot:
                    ok = False
                    break
                placed.append((spot[0], spot[1], sw, s))
                t["pos"] = spot
            if ok:
                return w, h
    raise AtlasPackingError("tiles do not fit a 4096 atlas")


# --- generation -------------------------------------------------------------------------------


def shape_description(contours, centre, half_extent):
    """Tile-normalised coordinates, y up (msdfgen convention)."""

    def coord(p):
        x = (p[0] - (centre[0] - half_extent)) / (2 * half_extent)
        y = 1 - (p[1] - (centre[1] - half_extent)) / (2 * half_extent)
        return f"{x:.9f},{y:.9f}"

    lines = []
    for c in contours:
        segs = [s for s in c if not (s[0] == "L" and s[1] == s[2])]
        if not segs:
            continue
        parts = []
        for s in segs:
            if s[0] == "L":
                parts.append(coord(s[1]) + ";")
            elif s[0] == "Q":
                parts.append(coord(s[1]) + "; (" + coord(s[2]) + ");")
            else:
                parts.append(coord(s[1]) + "; (" + coord(s[2]) + "; " + coord(s[3]) + ");")
        lines.append("{ " + " ".join(parts) + " # }")
    return "\n".join(lines) + "\n"


def quantize_coverage(cov):
    """Coverage to 8 bits, carrying each texel's rounding error to the next covered texel along the row: a small
    dot on a coarse mip covers far less than 1/255 of a texel, and plain rounding would drop or inflate its area.
    The carry stops at uncovered texels, so no stray value lands away from the shapes."""
    out = np.empty(cov.shape, np.uint8)
    carry = np.zeros(cov.shape[0])
    for x in range(cov.shape[1]):
        c = np.clip(cov[:, x], 0, 1)
        v = c + carry
        q = np.where(c > 0, np.clip(np.round(v * 255), 0, 255), 0)
        out[:, x] = q
        carry = np.where(c > 0, v - q / 255, 0)
    return out


def render_tile(plan, work):
    """Per mip of the tile: the MSDF texels (RGB distance, A coverage) and the true signed distance."""
    role, n0 = plan["role"], plan["size"]
    plan["level_max"] = int(math.log2(n0 // MIN_TILE_TEXELS))
    plan["range"] = PXRANGE / n0
    shape = work / f"tile_{len(list(work.iterdir()))}.txt"
    shape.write_text(shape_description(role.contours, plan["centre"], plan["half_extent"]))
    rings = [
        (ring - (np.array(plan["centre"]) - plan["half_extent"])) / (2 * plan["half_extent"]) * n0
        for ring in role.outline()
    ]
    cov = raster.coverage(rings, n0, n0)
    levels = []
    for level in range(plan["level_max"] + 1):
        n = n0 >> level
        msdf = msdfgen_tool.mtsdf(shape, n, plan["range"], work)
        rgba = np.empty((n, n, 4), np.uint8)
        rgba[..., :3] = np.round(np.clip(msdf[..., :3], 0, 1) * 255)
        rgba[..., 3] = quantize_coverage(cov)
        levels.append((rgba, np.round(np.clip(msdf[..., 3], 0, 1) * 255).astype(np.uint8)))
        cov = raster.downsample(cov)
    return levels


def halve(img):
    """Box filter to the next mip; a side already 1 texel long stays 1."""
    if img.shape[0] > 1:
        img = 0.5 * (img[0::2] + img[1::2])
    if img.shape[1] > 1:
        img = 0.5 * (img[:, 0::2] + img[:, 1::2])
    return img


class Atlas:
    """images: the mips as the shaders sample them (format 3: H x W x 4 uint8; format 4: H x W x 2 float32 decoded
    from blocks, the BC5 blocks of each mip)."""

    def __init__(self, images, descriptor, plans, blocks=None):
        self.images = images
        self.descriptor = descriptor
        self.plans = plans
        self.blocks = blocks

    @property
    def compressed(self):
        return self.blocks is not None

    @property
    def bytes(self):
        return sum(b.nbytes for b in (self.blocks if self.compressed else self.images))


SDF, MSDF = "sdf", "msdf"  # format 4 tile kinds


def plan_tiles(roles, settings):
    """The tiles, rendered (plan["levels"]: per mip the MSDF texels and the true distance), in descriptor order:
    within a role, colour and mark, finer tiles first (the shader stops at the first tile whose region holds the
    point)."""
    plans = []
    for role in sorted(roles.values(), key=lambda r: (r.role, r.mark, r.key)):
        plans.extend(plan_role(role, settings.quality, settings.units, density_cap(settings)))
    if len(plans) > rf.MAX_TILES:
        raise ValueError(
            f"{len(plans)} tiles, at most {rf.MAX_TILES}: use fewer paint colours or marks"
        )
    for p in plans:
        p["materials"] = settings.colour_mode != "legacy"
        r = p["role"]
        p["colour"] = r.colour
        if (
            settings.colour_mode == "legacy"
            and r.role == rf.ROLE_ILLUM
            and getattr(settings, "mark_colours", None)
        ):
            c = settings.mark_colours[min(max(r.mark - 1, 0), len(settings.mark_colours) - 1)]
            p["colour"] = tuple(int(round(min(max(v, 0.0), 1.0) * 255)) for v in c)
    plans.sort(
        key=lambda p: (
            p["role"].role,
            tuple(p["colour"]),
            p["role"].mark,
            getattr(p["role"], "material", 0),
            p["half_extent"],
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        for p in plans:
            p["levels"] = render_tile(p, Path(tmp))
    return plans


def _set_layout(plans, kinds):
    for p, kind in zip(plans, kinds or [SDF] * len(plans)):
        p["wide"] = 2 if kinds is not None and kind == MSDF else 1
    if any(p.get("materials") for p in plans):
        return 128 * (BLOCK if kinds is not None else 1)
    return (
        RESERVED
        if len(plans) <= 7 and not any(p.get("materials") for p in plans)
        else RESERVED_LARGE
    ) * (BLOCK if kinds is not None else 1)


def layout_size(plans, kinds):
    """Width and height of the texture these tile kinds take (None: format 3)."""
    return pack(plans, _set_layout(plans, kinds), kinds is not None)


def assemble(plans, roles, settings, kinds=None, layout_kinds=None):
    """The atlas of rendered tiles. kinds None: format 3 (RGBA8: MSDF + coverage). Else format 4 (BC5), per tile
    SDF (R distance, G coverage) or MSDF (R, G distance, and B distance, coverage in the square to its right).
    layout_kinds: lay the tiles out as for these kinds (a fixed layout while kinds are compared).
    """
    compressed = kinds is not None
    texel = BLOCK if compressed else 1
    # the descriptor of up to 7 tiles fits mip 2's 8 header texel cell
    reserved = _set_layout(plans, layout_kinds or kinds)
    width, height = pack(plans, reserved, compressed)
    total_mips = max(width, height).bit_length()
    images = [
        np.zeros((max(1, height >> m), max(1, width >> m), 2 if compressed else 4), np.uint8)
        for m in range(total_mips)
    ]
    g_distance = [
        np.zeros(img.shape[:2], bool) for img in images
    ]  # format 4: G holds a distance there
    tiles = []
    for i, p in enumerate(plans):
        kind = kinds[i] if compressed else None
        for level, (img, sdf) in enumerate(p["levels"]):
            x, y = p["pos"][0] >> level, p["pos"][1] >> level
            n = img.shape[0]
            if kind == MSDF:
                images[level][y : y + n, x : x + n] = img[..., :2]
                images[level][y : y + n, x + n : x + 2 * n] = img[..., 2:]
                g_distance[level][y : y + n, x : x + n] = True
            else:
                images[level][y : y + n, x : x + n] = (
                    np.stack([sdf, img[..., 3]], 2) if compressed else img
                )
        r = p["role"]
        tiles.append(
            rf.Tile(
                r.role,
                p["colour"],
                p["level_max"],
                (p["pos"][0] / width, p["pos"][1] / height),
                p["size"] / width,
                p["centre"],
                p["half_extent"],
                p["region_half"],
                p["range"],
                (rf.TILE_PAINT_GLOWS if r.glows else 0)
                | (rf.TILE_MSDF if kind == MSDF else 0)
                | (r.mark << rf.TILE_MARK_SHIFT),
                p["thin"] * p["size"] / (2 * p["half_extent"]),
                getattr(r, "material", 0),
            )
        )
    radius = max(
        math.hypot(abs(t.centre[0]) + t.region_half, abs(t.centre[1]) + t.region_half)
        for t in tiles
    )
    desc = rf.Descriptor(
        tiles,
        settings.calibrated_magnification,
        settings.focal_plane == "sfp",
        settings.holo,
        radius,
        rf.UNIT_MOA if settings.units == "moa" else rf.UNIT_MRAD,
        max([r.mark for r in roles.values()] + [1]),
        bool(getattr(settings, "mark_colours", None)),
        settings.legacy_span,
        settings.luminance,
        settings.glow_strength,
        settings.steps,
        settings.hologram,
        settings.glow_size,
    )
    if settings.colour_mode != "legacy":
        from reticle_states import resolve_states

        desc.colour_mode = settings.colour_mode
        desc.own_colours = False
        desc.states = resolve_states(settings, [r.mark for r in roles.values()])
        from reticle_materials import state_values

        desc.material_values = state_values(desc.states, next(iter(roles.values())).materials)
    for m in range(1, total_mips):
        box = halve(images[m - 1].astype(np.float32))
        empty = ~images[m].any(axis=2)
        images[m][empty] = np.round(box[empty]).astype(np.uint8)
    if compressed:
        # the distance is read from the resident top mip only; past the mips that can be it, a multi-channel tile's
        # G (a distance) would bleed into its coverage square beside it on the coarse mips the coverage is read from
        for p, kind in zip(plans, kinds):
            if kind == MSDF:
                for m in range(HEADER_MIPS, len(p["levels"])):
                    x, y, n = p["pos"][0] >> m, p["pos"][1] >> m, p["levels"][m][0].shape[0]
                    images[m][y : y + n, x : x + n, 1] = 0
    level_floor = min(t.level_max for t in tiles)
    headers = {}
    for m in range(total_mips):
        cell = (reserved >> m) // texel
        usable = level_floor - m + 1
        if (
            cell >= rf.HEADER_TEXELS
            and usable >= 1
            and (m < HEADER_MIPS or rf.block_fits(desc, cell))
        ):
            images[m][: cell * texel, : cell * texel] = 0
            # format 4 writes the header texels into the mip's block grid, encoded after the tiles
            grid = (
                np.zeros((images[m].shape[0] // texel, images[m].shape[1] // texel, 4), np.uint8)
                if compressed
                else images[m]
            )
            rf.write_block(
                grid,
                desc,
                m,
                total_mips,
                usable,
                cell,
                (
                    (rf.VERSION_MATERIALS_BC5 if compressed else rf.VERSION_MATERIALS)
                    if desc.colour_mode != "legacy"
                    else (rf.VERSION_BC5 if compressed else rf.VERSION)
                ),
                texel,
            )
            headers[m] = grid[:cell, :cell]
    if not compressed:
        return Atlas(images, desc, plans)
    blocks = [bc.encode_bc5(img, g) for img, g in zip(images, g_distance)]
    for m, header in headers.items():
        blocks[m][: header.shape[0], : header.shape[1]] = bc.exact_blocks(header)
    decoded = [bc.decode_bc5(b, img.shape[0], img.shape[1]) for b, img in zip(blocks, images)]
    return Atlas(decoded, desc, plans, blocks)


def build(roles, settings, kinds=None):
    """plan_tiles + assemble; kinds as assemble takes them, or SDF / MSDF for every tile."""
    plans = plan_tiles(roles, settings)
    if isinstance(kinds, str):
        kinds = [kinds] * len(plans)
    return assemble(plans, roles, settings, kinds)


DXGI_FORMAT_BC5_UNORM = 83


def write_dds(path, atlas):
    """Format 3 as a legacy RGBA8 DDS, format 4 as BC5_UNORM with the DX10 header."""
    h, w = atlas.images[0].shape[:2]
    caps = 0x1000 | 0x8 | 0x400000
    mips = len(atlas.images)
    if atlas.compressed:
        flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
        pf = struct.pack("<II4sIIIII", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
        hdr = (
            struct.pack("<IIIIIII", 124, flags, h, w, atlas.blocks[0].nbytes, 0, mips)
            + b"\0" * 44
            + pf
        )
        hdr += struct.pack("<IIIII", caps, 0, 0, 0, 0)
        hdr += struct.pack("<IIIII", DXGI_FORMAT_BC5_UNORM, 3, 0, 1, 0)
        data = atlas.blocks
    else:
        flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8
        pf = struct.pack(
            "<II4sIIIII",
            32,
            0x41,
            b"\0\0\0\0",
            32,
            0x000000FF,
            0x0000FF00,
            0x00FF0000,
            0xFF000000,
        )
        hdr = struct.pack("<IIIIIII", 124, flags, h, w, w * 4, 0, mips) + b"\0" * 44 + pf
        hdr += struct.pack("<IIIII", caps, 0, 0, 0, 0)
        data = atlas.images
    with open(path, "wb") as f:
        f.write(b"DDS " + hdr)
        for img in data:
            f.write(np.ascontiguousarray(img).tobytes())


def read_dds(path):
    """The mips as the shaders sample them (see Atlas)."""
    from reticle_dds import layout

    data = Path(path).read_bytes()
    dds = layout(data)
    images = []
    for mip in dds.mips:
        mh, mw, off = mip.height, mip.width, mip.offset
        if dds.compressed:
            bh, bw = -(-mh // 4), -(-mw // 4)
            blocks = np.frombuffer(data, np.uint8, bh * bw * 16, off).reshape(bh, bw, 16)
            images.append(bc.decode_bc5(blocks, mh, mw))
        else:
            images.append(np.frombuffer(data, np.uint8, mh * mw * 4, off).reshape(mh, mw, 4))
    return images
