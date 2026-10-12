"""BC5 (two BC4 channels) block compression for compressed atlases (format 4), and the GPU's decode of it.

A BC4 block is 4 x 4 texels of one channel: two endpoint bytes and a 3 bit index per texel. Indices 0 and 1 are
the endpoints exactly; the others interpolate them in 8 steps (endpoint 0 > endpoint 1) or 6 steps plus 0 and 1.
The header and descriptor of an atlas use only exact values: one block per header texel, its four bytes in the
endpoints of the R and G blocks (R0, G0, R1, G1), the block's top row on endpoint 0 and the rest on endpoint 1.
"""

import numpy as np

# texel index -> position along the endpoint ramp (0 = endpoint 0, 1 = endpoint 1), 8 step mode
_RAMP8 = np.array([0, 7, 1, 2, 3, 4, 5, 6], np.float64) / 7
_RAMP6 = np.array([0, 5, 1, 2, 3, 4], np.float64) / 5


def _palettes(e0, e1):
    """(n, 8) decoded values (0..255, unrounded) of blocks with these endpoints, in index order."""
    e0, e1 = e0.astype(np.float64)[:, None], e1.astype(np.float64)[:, None]
    p8 = e0 * (1 - _RAMP8) + e1 * _RAMP8
    p6 = np.concatenate(
        [e0 * (1 - _RAMP6) + e1 * _RAMP6, np.zeros_like(e0), np.full_like(e0, 255.0)], 1
    )
    return np.where(e0 > e1, p8, p6)


def _fit(v, e0, e1, keep_zero, weight):
    pal = _palettes(e0, e1)
    idx = np.abs(v[:, :, None] - pal[:, None, :]).argmin(2)
    got = np.take_along_axis(pal, idx, 1)
    err = (weight * (got - v) ** 2).sum(1)
    if keep_zero:
        err = np.where(((v == 0) & (got != 0)).any(1), np.inf, err)
    return idx, err


def _refine(v, idx, six, weight):
    """Weighted least-squares endpoints for fixed indices (the ramp positions they stand for)."""
    t = np.where(
        six[:, None], np.take(np.append(_RAMP6, [np.nan, np.nan]), idx), np.take(_RAMP8, idx)
    )
    use = ~np.isnan(t)
    a, b = np.where(use, 1 - t, 0) * np.sqrt(weight), np.where(use, t, 0) * np.sqrt(weight)
    vv = np.where(use, v, 0) * np.sqrt(weight)
    aa, bb, ab = (a * a).sum(1), (b * b).sum(1), (a * b).sum(1)
    av, bv = (a * vv).sum(1), (b * vv).sum(1)
    det = aa * bb - ab * ab
    ok = np.abs(det) > 1e-9
    safe = np.where(ok, det, 1)
    e0 = np.where(ok, (bb * av - ab * bv) / safe, v.max(1))
    e1 = np.where(ok, (aa * bv - ab * av) / safe, v.min(1))
    return np.clip(np.round(e0), 0, 255).astype(np.uint8), np.clip(np.round(e1), 0, 255).astype(
        np.uint8
    )


def _keep_sum(v, idx, pal):
    """Coverage: moves texels (never one at 0) to the neighbouring palette value while that brings the block's sum,
    the area a minified footprint integrates, closer to the source: a small dot on a coarse mip keeps its darkness.
    """
    order = np.argsort(pal, 1)
    ps = np.take_along_axis(pal, order, 1)
    rank = np.argsort(order, 1)
    r = np.take_along_axis(rank, idx, 1)
    live = v > 0
    rows = np.arange(len(v))
    for _ in range(16):
        got = np.take_along_axis(ps, r, 1)
        diff = v.sum(1) - got.sum(1)
        up = np.take_along_axis(ps, np.minimum(r + 1, 7), 1) - got
        down = np.take_along_axis(ps, np.maximum(r - 1, 0), 1) - got
        step = np.where((diff > 0)[:, None], up, down)
        after = np.where(live & (step != 0), np.abs(diff[:, None] - step), np.inf)
        k = after.argmin(1)
        better = after[rows, k] < np.abs(diff) - 1e-9
        if not better.any():
            break
        r[rows[better], k[better]] += np.where(diff[better] > 0, 1, -1)
    return np.take_along_axis(order, r, 1)


# a distance texel within about a texel of the edge (range 8 texels: 32 per texel) decides where the edge is drawn
EDGE_WEIGHT, EDGE_SPREAD = 40.0, 24.0


def encode_bc4(chan, keep_zero=False, edge=False):
    """One channel (H x W uint8, H and W multiples of 4) -> (H/4, W/4, 8) BC4 blocks. keep_zero: texels at 0 stay
    exactly 0 (coverage: nothing drawn away from the shapes). edge: a distance channel, whose texels near 0.5 (the
    edge) are fitted most closely."""
    h, w = chan.shape
    v = chan.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.float64)
    weight = (
        1 + EDGE_WEIGHT * np.exp(-(((v - 127.5) / EDGE_SPREAD) ** 2)) if edge else np.ones_like(v)
    )
    hi, lo = v.max(1).astype(np.uint8), v.min(1).astype(np.uint8)
    # 8 steps between the extremes, 6 steps between the inner values when the block also holds 0 or 255, then
    # refined and searched around the best pair
    best_e0, best_e1 = np.where(hi > lo, hi, lo), np.where(hi > lo, lo, hi)
    best_idx, best_err = _fit(v, best_e0, best_e1, keep_zero, weight)
    inner = np.where((v > 0) & (v < 255), v, np.nan)
    has_inner = ~np.isnan(inner).all(1)
    ilo = np.where(has_inner, np.nanmin(np.where(has_inner[:, None], inner, 0), 1), 0).astype(
        np.uint8
    )
    ihi = np.where(has_inner, np.nanmax(np.where(has_inner[:, None], inner, 0), 1), 0).astype(
        np.uint8
    )
    candidates = [(ilo, ihi)]

    def consider(pairs):
        nonlocal best_e0, best_e1, best_idx, best_err
        for e0, e1 in pairs:
            idx, err = _fit(v, e0, e1, keep_zero, weight)
            better = err < best_err
            best_e0, best_e1 = np.where(better, e0, best_e0), np.where(better, e1, best_e1)
            best_idx, best_err = np.where(better[:, None], idx, best_idx), np.where(
                better, err, best_err
            )

    for _ in range(2):
        consider(candidates + [_refine(v, best_idx, best_e0 <= best_e1, weight)])
        candidates = []
    base0, base1 = best_e0.astype(int), best_e1.astype(int)
    consider(
        [
            (
                np.clip(base0 + d0, 0, 255).astype(np.uint8),
                np.clip(base1 + d1, 0, 255).astype(np.uint8),
            )
            for d0 in range(-2, 3)
            for d1 in range(-2, 3)
            if d0 or d1
        ]
    )
    if keep_zero:
        best_idx = _keep_sum(v, best_idx, _palettes(best_e0, best_e1))
    bits = (best_idx.astype(np.uint64) << (3 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.empty((len(v), 8), np.uint8)
    out[:, 0], out[:, 1] = best_e0, best_e1
    for k in range(6):
        out[:, 2 + k] = (bits >> np.uint64(8 * k)) & np.uint64(255)
    return out.reshape(h // 4, w // 4, 8)


def decode_bc4(blocks):
    """(bh, bw, 8) BC4 blocks -> (4 bh, 4 bw) float32 in 0..1, as the GPU samples them."""
    bh, bw = blocks.shape[:2]
    b = blocks.reshape(-1, 8)
    pal = _palettes(b[:, 0], b[:, 1])
    bits = np.zeros(len(b), np.uint64)
    for k in range(6):
        bits |= b[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    idx = ((bits[:, None] >> (3 * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
    vals = np.take_along_axis(pal, idx, 1) / 255.0
    return (
        vals.reshape(bh, bw, 4, 4).transpose(0, 2, 1, 3).reshape(4 * bh, 4 * bw).astype(np.float32)
    )


def pad4(img):
    """Pads a mip to whole blocks by repeating its edge (a 2 x 1 mip becomes one 4 x 4 block)."""
    h, w = img.shape[:2]
    return np.pad(img, ((0, -h % 4), (0, -w % 4)) + ((0, 0),) * (img.ndim - 2), mode="edge")


def encode_bc5(img, g_distance=None):
    """H x W x 2 uint8 (R distance, G coverage or, where g_distance (H x W bool) is set, a second distance) ->
    (bh, bw, 16) BC5 blocks."""
    img = pad4(img)
    if g_distance is not None and g_distance.any():
        mask = pad4(g_distance)[0::4, 0::4]
        bh, bw = mask.shape
        blocks = img[..., 1].reshape(bh, 4, bw, 4).transpose(0, 2, 1, 3)
        g = np.empty((bh, bw, 8), np.uint8)
        # Each block has one interpretation. Encode only that interpretation,
        # packing selected blocks into a one-block-wide image for the BC4 encoder.
        for selected, distance in ((mask, True), (~mask, False)):
            if selected.any():
                channel = blocks[selected].reshape(-1, 4)
                g[selected] = encode_bc4(channel, keep_zero=not distance, edge=distance).reshape(
                    -1, 8
                )
    else:
        g = encode_bc4(img[..., 1], keep_zero=True)
    return np.concatenate([encode_bc4(img[..., 0], edge=True), g], 2)


def decode_bc5(blocks, height, width):
    """BC5 blocks -> height x width x 2 float32, the mip as sampled."""
    out = np.stack([decode_bc4(blocks[..., :8]), decode_bc4(blocks[..., 8:])], 2)
    return out[:height, :width]


# header blocks: top row of the block on endpoint 0, the other rows on endpoint 1
_EXACT_INDICES = np.array(
    [(sum(1 << (3 * i) for i in range(4, 16)) >> (8 * k)) & 255 for k in range(6)], np.uint8
)


def exact_blocks(texels):
    """(n, m, 4) bytes -> (n, m, 16) BC5 blocks that each decode to those four bytes exactly: R0, G0 on the block's
    top row, R1, G1 below it."""
    n, m = texels.shape[:2]
    out = np.empty((n, m, 16), np.uint8)
    out[..., 0], out[..., 1], out[..., 2:8] = texels[..., 0], texels[..., 2], _EXACT_INDICES
    out[..., 8], out[..., 9], out[..., 10:16] = texels[..., 1], texels[..., 3], _EXACT_INDICES
    return out


def header_texels(decoded):
    """The bytes the header blocks of a decoded mip (H x W x 2, 0..1) carry, as (H/4, W/4, 4) uint8: what
    reticle_msdf.h reads for a compressed atlas."""
    q = np.round(np.asarray(decoded, np.float64) * 255).astype(np.uint8)
    return np.stack([q[0::4, 0::4, 0], q[0::4, 0::4, 1], q[1::4, 0::4, 0], q[1::4, 0::4, 1]], 2)
