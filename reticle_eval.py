"""NumPy port of reticle_msdf.h (GAMMA patch) for the offline gate, formats 3 and 4. Keep the two in step.

Pixels are evaluated in bulk: p is (n, 2) reticle angle, jx / jy are (n, 2) reticle angle per screen pixel.
"""

import math

import numpy as np

import block_compress as bc
import reticle_format as rf

GUTTER = 1 / 16


class Atlas:
    """A resident mip chain (mip 0 = the first resident mip) decoded like reticle_atlas_open. images: as
    reticle_build.Atlas holds them (format 3 RGBA8, or format 4 decoded BC5: distance, coverage).
    """

    def __init__(self, images, first_mip=0):
        self.compressed = images[first_mip].shape[2] == 2
        if self.compressed:
            self.levels = [np.asarray(img, np.float32) for img in images[first_mip:]]
            texels, texel = bc.header_texels(images[first_mip]), 4
        else:
            self.levels = [img.astype(np.float32) / 255.0 for img in images[first_mip:]]
            texels, texel = images[first_mip], 1
        self.header, self.desc = rf.read_block(texels, len(images) - first_mip, texel)
        self.cover = None  # gate isolation check: coverage read from these levels instead (see coverage_only)
        self.recognised = self.header is not None
        self.dims = np.array(
            [images[first_mip].shape[1], images[first_mip].shape[0]], np.float64
        )
        self.orig_mip = self.header["orig_mip"] if self.header else 0
        self.units_per_radian = (
            1000.0 if self.header and self.header["unit"] == rf.UNIT_MRAD else 3437.74677
        )


def sample_level(atlas, uv, lod, channels):
    """Trilinear sample (smp_linear: wrap, linear min/mag/mip) at explicit lod, returns (n, len(channels))."""
    lod = np.clip(lod, 0, len(atlas.levels) - 1)
    l0 = np.floor(lod).astype(int)
    frac = lod - l0
    out = np.zeros((len(uv), len(channels)), np.float32)
    for level in np.unique(np.concatenate([l0, np.minimum(l0 + 1, len(atlas.levels) - 1)])):
        img = atlas.levels[level][..., channels]
        h, w = img.shape[:2]
        x = uv[:, 0] * w - 0.5
        y = uv[:, 1] * h - 0.5
        xi, yi = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = (x - xi)[:, None], (y - yi)[:, None]
        x0, x1 = xi % w, (xi + 1) % w
        y0, y1 = yi % h, (yi + 1) % h
        v = (
            img[y0, x0] * (1 - fx) * (1 - fy)
            + img[y0, x1] * fx * (1 - fy)
            + img[y1, x0] * (1 - fx) * fy
            + img[y1, x1] * fx * fy
        )
        weight = np.where(l0 == level, 1 - frac, 0) + np.where(
            (l0 + 1 == level) & (level < len(atlas.levels)), frac, 0
        )
        if level == len(atlas.levels) - 1:
            weight = np.where(l0 >= level, 1.0, weight)
        out += v * weight[:, None]
    return out


def median3(v):
    return np.maximum(
        np.minimum(v[:, 0], v[:, 1]), np.minimum(np.maximum(v[:, 0], v[:, 1]), v[:, 2])
    )


def tile_distance(atlas, t, uv, lod):
    """The signed distance (0.5 = edge) of tile t: the median of a multi-channel tile (format 4: R, G in its square,
    B in the square to its right), else the one true distance."""
    if not atlas.compressed:
        return median3(sample_level(atlas, uv, lod, [0, 1, 2]))
    if t.flags & rf.TILE_MSDF:
        rg = sample_level(atlas, uv, lod, [0, 1])
        b = sample_level(atlas, uv + [t.uv_size, 0], lod, [0])
        return median3(np.concatenate([rg, b], 1))
    return sample_level(atlas, uv, lod, [0])[:, 0]


class _Levels:
    def __init__(self, levels):
        self.levels = levels


def coverage_only(atlas, index):
    """A copy of the atlas whose coverage reads see only tile `index`'s own coverage texels, zero everywhere else
    (other tiles, the header, a multi-channel tile's distance square, the filled space between tiles).
    """
    out = Atlas.__new__(Atlas)
    out.__dict__.update(atlas.__dict__)
    channel = 1 if atlas.compressed else 3
    levels = []
    for img in atlas.levels:
        h, w = img.shape[:2]
        cover = np.zeros((h, w, 1), np.float32)
        for t in atlas.desc.tiles[index : index + 1]:
            n = max(1, int(round(t.uv_size * w)))
            x = int(
                round(
                    (
                        t.uv0[0]
                        + (t.uv_size if atlas.compressed and t.flags & rf.TILE_MSDF else 0)
                    )
                    * w
                )
            )
            y = int(round(t.uv0[1] * h))
            cover[y : y + n, x : x + n, 0] = img[y : y + n, x : x + n, channel]
        levels.append(cover)
    out.cover = _Levels(levels)
    return out


def tile_area(atlas, t, uv, lod):
    """The coverage of tile t"""
    if atlas.cover is not None:
        return sample_level(
            atlas.cover,
            uv + [t.uv_size, 0] if atlas.compressed and t.flags & rf.TILE_MSDF else uv,
            lod,
            [0],
        )[:, 0]
    if atlas.desc.material_values:
        offset = np.array([t.uv_size if atlas.compressed and t.flags & rf.TILE_MSDF else 0, 0])
        lo = np.asarray(t.uv0) + offset
        hi = lo + t.uv_size * np.array([1, atlas.dims[0] / atlas.dims[1]])
        uv = uv + offset
        base = np.floor(lod)
        result = np.zeros(len(uv), np.float32)
        for level, blend in ((base, 1 - (lod - base)), (base + 1, lod - base)):
            dims = atlas.dims[None, :] / np.exp2(level[:, None])
            half = 0.5 / dims
            weight = np.prod(
                np.clip((uv - lo) * dims + 0.5, 0, 1) * np.clip((hi - uv) * dims + 0.5, 0, 1),
                axis=1,
            )
            sample = sample_level(
                atlas, np.clip(uv, lo + half, hi - half), level, [1 if atlas.compressed else 3]
            )[:, 0]
            result += sample * weight * blend
        return result
    if not atlas.compressed:
        return sample_level(atlas, uv, lod, [3])[:, 0]
    return sample_level(atlas, uv + [t.uv_size, 0] if t.flags & rf.TILE_MSDF else uv, lod, [1])[
        :, 0
    ]


def tile_frame(atlas, t, p, jx, jy):
    scale = 0.5 / t.half_extent
    extent = t.uv_size * np.array([1.0, atlas.dims[0] / atlas.dims[1]])
    uv = np.array(t.uv0) + ((p - np.array(t.centre)) * scale + 0.5) * extent
    texels = t.uv_size * atlas.dims[0]
    return uv, texels, jx * scale * texels, jy * scale * texels


NEAR_LOD = 3


def tile_coverage(atlas, t, p, jx, jy):
    uv, texels, tx, ty = tile_frame(atlas, t, p, jx, jy)
    xx, yy, xy = (tx * tx).sum(1), (ty * ty).sum(1), (tx * ty).sum(1)
    mean = 0.5 * (xx + yy)
    spread = np.sqrt(np.maximum(0.25 * (xx - yy) ** 2 + xy * xy, 0))
    footprint = np.sqrt(mean + spread)
    lod_max = max(t.level_max - atlas.orig_mip, 0)
    # holographic atlases widen the filter by their softness (reticle_msdf_reflex.h)
    soften = (
        max(atlas.desc.hologram["softness"], 1.0)
        if atlas.header and atlas.header["flags"] & rf.FLAG_HOLO
        else 1.0
    )
    # the distance field draws edges only where it resolves the thinnest stroke; elsewhere coverage does
    stroke = t.stroke_texels / 2**atlas.orig_mip if t.stroke_texels > 0 else 1e9
    resolved = np.clip((stroke - 0.75) / 0.5, 0, 1)
    minified = np.maximum(np.clip(footprint * soften - 1, 0, 1), 1 - resolved)
    edge = np.zeros(len(p), np.float32)
    sel = minified < 1
    if sel.any():
        sd = tile_distance(atlas, t, uv[sel], np.zeros(sel.sum())) - 0.5
        edge[sel] = np.clip(
            0.5
            + sd * t.distance_range * texels / np.maximum(np.sqrt(mean[sel]) * soften, 1e-4),
            0,
            1,
        )
        # far from every shape the channels can still meet near 0.5; a coarse coverage mip says whether a shape is
        # within a few texels at all
        near = (
            tile_area(atlas, t, uv[sel], np.full(sel.sum(), float(min(NEAR_LOD, lod_max)))) > 0
        )
        edge[sel] *= near
    area = np.zeros(len(p), np.float32)
    sel = minified > 0
    if sel.any():
        nx = np.where(xx > 4 * yy, 4, 2)[sel]
        ny = np.where(yy > 4 * xx, 4, 2)[sel]
        lod_raw = (
            np.log2(np.maximum(np.maximum(np.sqrt(xx[sel]) / nx, np.sqrt(yy[sel]) / ny), 1e-4))
            + 1.4
        )
        lod = np.clip(lod_raw, 0, lod_max)
        extra = np.exp2(np.clip(lod_raw - lod_max, 0, 2))
        nx = np.minimum(np.ceil(nx * extra), 8).astype(int)
        ny = np.minimum(np.ceil(ny * extra), 8).astype(int)
        if soften > 1:
            nx, ny = np.maximum(nx, 4), np.maximum(ny, 4)
        acc = np.zeros(sel.sum(), np.float32)
        for i in range(8):
            for j in range(8):
                use = (i < nx) & (j < ny)
                if not use.any():
                    continue
                o = (
                    ((i + 0.5) / nx - 0.5)[:, None] * tx[sel]
                    + ((j + 0.5) / ny - 0.5)[:, None] * ty[sel]
                ) * soften
                v = tile_area(atlas, t, uv[sel] + o / atlas.dims, lod)
                acc += np.where(use, v, 0)
        area[sel] = acc / (nx * ny)
    return edge * (1 - minified) + area * minified


def marks(atlas, p, jx, jy, line_weight=0.0, mark=0, pattern=False):
    """mark: Mark Switch mark, 0 based like markswitch_current"""
    n = len(p)
    out = {k: np.zeros(n, np.float32) for k in ("ink", "illum", "paint", "paint_glow")}
    out["paint_rgb"] = np.zeros((n, 3), np.float32)
    gamma = 1 - 0.75 * line_weight
    tiles = atlas.desc.tiles
    remaining = np.ones(n, np.float32)
    group_cov = np.zeros(n, np.float32)
    selected = mark % atlas.desc.marks + 1
    state = atlas.desc.states[mark % len(atlas.desc.states)] if atlas.desc.states else None
    if state is not None and not pattern:
        selected = state.pattern
    out["illum_rgb"] = np.zeros((n, 3), np.float32)
    for i, t in enumerate(tiles):
        last = (
            i + 1 == len(tiles)
            or tiles[i + 1].role != t.role
            or tuple(tiles[i + 1].colour) != tuple(t.colour)
            or tiles[i + 1].mark != t.mark
            or tiles[i + 1].material != t.material
        )
        reach = np.abs(p - np.array(t.centre)).max(1)
        limit = t.half_extent * (1 - GUTTER) if last else t.region_half
        sel = (remaining > 0) & (reach <= limit) & (t.mark in (0, selected))
        if sel.any():
            w = (
                np.ones(sel.sum())
                if last
                else np.clip((t.region_half - reach[sel]) / (0.05 * t.region_half), 0, 1)
            )
            cov = np.clip(tile_coverage(atlas, t, p[sel], jx[sel], jy[sel]), 0, 1) ** gamma
            group_cov[sel] += remaining[sel] * w * cov
            remaining[sel] *= 1 - w
        if last:
            material = None
            tile_colour = np.asarray(t.colour) / 255
            if atlas.desc.material_values and not pattern:
                material = atlas.desc.material_values[mark % len(atlas.desc.states)][t.material]
                if not material.visible:
                    group_cov[:] = 0
                if material.colour is not None:
                    tile_colour = np.asarray(material.colour)
            if t.role == rf.ROLE_INK:
                out["ink"] = 1 - (1 - out["ink"]) * (1 - group_cov)
            elif t.role == rf.ROLE_ILLUM:
                out["illum"] = 1 - (1 - out["illum"]) * (1 - group_cov)
                colour = (
                    np.asarray(t.colour) / 255
                    if state is not None
                    and atlas.desc.colour_mode == "authored"
                    and state.colour is None
                    else np.ones(3)
                )
                if material is not None:
                    colour = (
                        tile_colour if atlas.desc.colour_mode == "authored" else np.ones(3)
                    ) * material.brightness
                out["illum_rgb"] += colour[None, :] * group_cov[:, None]
            else:
                out["paint"] = 1 - (1 - out["paint"]) * (1 - group_cov)
                out["paint_rgb"] += tile_colour[None, :] * group_cov[:, None]
                if t.flags & rf.TILE_PAINT_GLOWS:
                    out["paint_glow"] = 1 - (1 - out["paint_glow"]) * (1 - group_cov)
            remaining = np.ones(n, np.float32)
            group_cov = np.zeros(n, np.float32)
    if atlas.desc.colour_mode == "legacy":
        out["ink"] = 1 - (1 - out["ink"]) * (1 - out["illum"])
    if atlas.desc.states:
        out["emitting_rgb"] = out["illum_rgb"]
    return out


# The engine's 3DSS reticle glow defaults (r__reticle_msdf_glow_size / _glow_weight / _glow_exposure /
# _glow_shoulder / _glow_tint / _glow_halo): the three glow lobes' Gaussian sigma in pixels at 1080p, their weights,
# the exposure's power on them, the core's white shoulder, the cover and the lobes' display ceiling
GLOW_SIGMA_PX = (3.0, 12.0, 40.0)
GLOW_WEIGHT = (1.0, 0.35, 0.15)
GLOW_EXPOSURE = 1.0
GLOW_SHOULDER = 1.0
GLOW_TINT = 0.5
GLOW_HALO = 0.55


def emission(m, colour, luminance=8.0):
    """The lit core as reticle_msdf.h writes it: colour times coverage times luminance, unbounded."""
    if "emitting_rgb" in m:
        return m["emitting_rgb"] * np.asarray(colour, np.float32)[None, :] * luminance
    core = np.maximum(m["illum"], m["paint_glow"])
    return np.asarray(colour, np.float32)[None, :] * (core * luminance)[:, None]


def _hash(p):
    return np.modf(np.sin(p[..., 0] * 127.1 + p[..., 1] * 311.7) * 43758.5453)[0] % 1.0


def value_noise(p):
    i = np.floor(p)
    f = p - i
    u = f * f * (3 - 2 * f)
    a, b = _hash(i), _hash(i + [1, 0])
    c, d = _hash(i + [0, 1]), _hash(i + [1, 1])
    return (a + (b - a) * u[..., 0]) + (
        (c + (d - c) * u[..., 0]) - (a + (b - a) * u[..., 0])
    ) * u[..., 1]


def speckle_wave(cell):
    power = -np.log(np.maximum(_hash(cell), 1e-4))
    phase = 2 * math.pi * _hash(cell + [43.7, 19.3])
    return np.sqrt(power)[..., None] * np.stack([np.cos(phase), np.sin(phase)], -1)


def speckle_field(p):
    cell = np.floor(p)
    f = p - cell
    u = f * f * (3 - 2 * f)
    a, b = speckle_wave(cell), speckle_wave(cell + [1, 0])
    c, d = speckle_wave(cell + [0, 1]), speckle_wave(cell + [1, 1])
    top = a + (b - a) * u[..., 0, None]
    bottom = c + (d - c) * u[..., 0, None]
    wave = top + (bottom - top) * u[..., 1, None]
    variance = u * u + (1 - u) * (1 - u)
    return (wave * wave).sum(-1) / variance.prod(-1)


def speckle(p, jx, jy):
    a, b = 0.25 * (jx + jy), 0.25 * (jx - jy)
    power = sum(speckle_field(p + offset) for offset in (a, -a, b, -b)) * 0.25
    footprint = np.maximum(np.linalg.norm(jx, axis=-1), np.linalg.norm(jy, axis=-1))
    fade = np.clip((footprint - 1) / 1.5, 0, 1)
    fade = fade * fade * (3 - 2 * fade)
    return 1 + (power - 1) * (1 - fade)


def hologram(h, p, illumination=1.0, time=0.0, eye_mm=(0.0, 0.0), jx=None, jy=None):
    """reticle_msdf_reflex.h: the brightness factor of a hologram's light at reticle angles p (n, 2), without the
    ghost and fringe (they need the marks again)."""
    grain = np.full(len(p), h["level"], np.float64)
    if h["speckle"] > 0:
        size = max(h["speckle_size"], 0.01)
        dx = np.zeros_like(p) if jx is None else jx / size
        dy = np.zeros_like(p) if jy is None else jy / size
        n = speckle(p / size + np.array(eye_mm) * h["speckle_eye_shift"], dx, dy)
        grain *= 1 + (n - 1) * min(h["speckle"], 1)
    if h["noise_animated"] > 0:
        t = time * h["noise_speed"]
        k = math.floor(t)
        f = t - k
        f = f * f * (3 - 2 * f)
        q = p / max(h["noise_size"], 0.01)
        shift = lambda kk: 97 * _hash(np.array([kk, 1.7]))
        n = value_noise(q + shift(k)) * (1 - f) + value_noise(q + shift(k + 1)) * f
        grain *= 1 + (0.4 + 1.2 * n - 1) * min(h["noise_animated"], 1)
    if h["flicker"] > 0 and (time * 9.7) % 1 >= 0.55:
        grain *= 1 - h["flicker"] * np.clip(1 - illumination / 0.35, 0, 1)
    return grain


def _box(img, r, axis):
    if r < 1:
        return img
    pad = [(0, 0)] * img.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(img, pad, mode="edge"), axis=axis)
    n = img.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return (hi - lo) / (2 * r + 1)


def gaussian(img, sigma):
    """Three box passes per axis, close to a Gaussian of this sigma (pixels)."""
    r = int(round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2))
    for axis in (0, 1):
        for _ in range(3):
            img = _box(img, r, axis)
    return img


def compose(core, background, exposure, glow_strength=1.0, px_scale=1.0):
    """reticle_emission_combine.ps (SDR) on an (h, w, 3) image at the glow's exposure: the core through the
    hue-keeping soft clip with its white shoulder, its three glow lobes hue-kept below GLOW_HALO, screen-blended,
    over the background with the glow's tint."""
    glow = np.zeros_like(core)
    for sigma, weight in zip(GLOW_SIGMA_PX, GLOW_WEIGHT):
        glow += glow_strength * weight * gaussian(core, sigma * px_scale)
    c = core * exposure
    peak = c.max(-1, keepdims=True)
    level = 1 - np.exp(-peak)
    white = 1 - np.exp(-GLOW_SHOULDER * np.maximum(peak - 1, 0))
    hue = c / np.maximum(peak, 1e-6)
    core_d = level * (hue + (1 - hue) * white)
    g = glow * exposure**GLOW_EXPOSURE
    g_peak = g.max(-1, keepdims=True)
    g_level = GLOW_HALO * (1 - np.exp(-g_peak / GLOW_HALO))
    halo = g * g_level / np.maximum(g_peak, 1e-6)
    out = 1 - (1 - core_d) * (1 - halo)
    return out + background * (1 - GLOW_TINT * np.maximum(level, g_level))


def screen_grid(width, height, centre_px, units_per_px, rotation_deg=0.0, offset_px=(0.0, 0.0)):
    """Affine screen->reticle mapping: pixel centres, returns p (n, 2), jx, jy."""
    ys, xs = np.mgrid[0:height, 0:width]
    px = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1).astype(np.float64)
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    rel = px - np.array(centre_px) - np.array(offset_px)
    p = (
        np.stack([c * rel[:, 0] - s * rel[:, 1], s * rel[:, 0] + c * rel[:, 1]], 1)
        * units_per_px
    )
    jx = np.tile(np.array([[c, s]]) * units_per_px, (len(p), 1))
    jy = np.tile(np.array([[-s, c]]) * units_per_px, (len(p), 1))
    return p, jx, jy
