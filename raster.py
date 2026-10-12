"""Exact-area reference coverage for filled shapes, used for the coverage mips and the gate.

Shapes are first merged into one overlap-free outline (shapely), then each edge is clipped to pixel cells and its
signed area accumulated (the scanline area method of font rasterisers), so a stroke thinner than a pixel gets
its true area.
"""

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union


def merge(paths):
    """paths: list of shapes, each a list of (k, 2) contour arrays filled with the nonzero rule. Returns the
    outline rings of their union. A contour is a hole when the winding number just inside it is zero.
    """
    shapes = []
    for contours in paths:
        rings = [np.asarray(c, float) for c in contours if len(c) >= 3]
        if not rings:
            continue
        signs = [
            np.sign((r[:, 0] * np.roll(r[:, 1], -1) - np.roll(r[:, 0], -1) * r[:, 1]).sum())
            for r in rings
        ]
        polys = [Polygon(r).buffer(0) for r in rings]
        solid, holes = [], []
        for poly in polys:
            if poly.is_empty:
                continue
            nested = [o for o in polys if o is not poly and not o.is_empty and poly.contains(o)]
            region = poly.difference(unary_union(nested)) if nested else poly
            probe = (region if not region.is_empty else poly).representative_point()
            winding = sum(
                s for s, other in zip(signs, polys) if not other.is_empty and other.contains(probe)
            )
            (solid if winding != 0 else holes).append(poly)
        shape = unary_union(solid)
        if holes:
            shape = shape.difference(unary_union(holes))
        shapes.append(shape)
    union = unary_union(shapes)
    rings = []
    for poly in getattr(union, "geoms", [union]):
        if poly.is_empty:
            continue
        poly = orient(poly, 1.0)
        rings.append(np.asarray(poly.exterior.coords)[:-1])
        rings.extend(np.asarray(i.coords)[:-1] for i in poly.interiors)
    return rings


def polygon_edges(polygons):
    """polygons: list of (k, 2) arrays (closed implicitly). Returns (m, 4) array x0, y0, x1, y1."""
    edges = [
        np.concatenate([p, np.roll(p, -1, axis=0)], axis=1)
        for p in map(np.asarray, polygons)
        if len(p) >= 3
    ]
    return np.concatenate(edges).astype(np.float64) if edges else np.zeros((0, 4))


def _split(a0, a1, b0, b1):
    """Splits segments (a0,b0)-(a1,b1) at every integer value of a. Returns piece endpoints, owner index and
    the integer cell of a each piece lies in (taken from the split itself, never from rounded endpoints).
    """
    lo, hi = np.minimum(a0, a1), np.maximum(a0, a1)
    first = np.floor(lo).astype(np.int64)
    count = np.maximum(np.ceil(hi).astype(np.int64) - first, 1)
    idx = np.repeat(np.arange(len(a0)), count)
    k = np.arange(len(idx)) - np.repeat(np.cumsum(count) - count, count)
    cell = first[idx] + k
    da = a1 - a0
    t0 = np.where(
        da[idx] != 0,
        (np.clip(cell, lo[idx], hi[idx]) - a0[idx]) / np.where(da[idx] != 0, da[idx], 1),
        0,
    )
    t1 = np.where(
        da[idx] != 0,
        (np.clip(cell + 1, lo[idx], hi[idx]) - a0[idx]) / np.where(da[idx] != 0, da[idx], 1),
        1,
    )
    t0, t1 = np.minimum(t0, t1), np.maximum(t0, t1)
    pa0 = a0[idx] + t0 * da[idx]
    pa1 = a0[idx] + t1 * da[idx]
    db = b1 - b0
    pb0 = b0[idx] + t0 * db[idx]
    pb1 = b0[idx] + t1 * db[idx]
    # t is the parameter along the original segment, so sorted t keeps its direction
    return pa0, pa1, pb0, pb1, idx, cell


def coverage(polygons, width, height):
    """Exact area coverage per pixel of non-overlapping outline rings (from merge) in pixel units, y down."""
    e = polygon_edges(polygons)
    out = np.zeros((height, width), np.float64)
    if len(e) == 0:
        return out.astype(np.float32)
    x0, y0, x1, y1 = e.T
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    # clip to the vertical extent of the image; horizontally, area left of 0 lands in column 0's accumulator
    ylo = np.clip(np.minimum(y0, y1), 0, height)
    yhi = np.clip(np.maximum(y0, y1), 0, height)
    keep = yhi > ylo
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    t_a = (np.clip(y0, 0, height) - y0) / (y1 - y0)
    t_b = (np.clip(y1, 0, height) - y0) / (y1 - y0)
    nx0, nx1 = x0 + t_a * (x1 - x0), x0 + t_b * (x1 - x0)
    ny0, ny1 = np.clip(y0, 0, height), np.clip(y1, 0, height)
    # rows
    ry0, ry1, rx0, rx1, _, row = _split(ny0, ny1, nx0, nx1)
    keep = (ry1 != ry0) & (row >= 0) & (row < height)
    ry0, ry1, rx0, rx1, row = ry0[keep], ry1[keep], rx0[keep], rx1[keep], row[keep]
    # columns
    cx0, cx1, cy0, cy1, owner, col = _split(rx0, rx1, ry0, ry1)
    row = row[owner]
    dy = cy1 - cy0
    xm = np.clip(0.5 * (cx0 + cx1) - col, 0.0, 1.0)
    acc = np.zeros((height, width + 2), np.float64)
    left = col < 0
    right = col >= width
    inside = ~left & ~right
    np.add.at(acc, (row[inside], col[inside]), dy[inside] * (1 - xm[inside]))
    np.add.at(acc, (row[inside], col[inside] + 1), dy[inside] * xm[inside])
    np.add.at(acc, (row[left], np.zeros(left.sum(), np.int64)), dy[left])
    winding = np.cumsum(acc[:, :width], axis=1)
    return np.clip(np.abs(winding), 0.0, 1.0).astype(np.float32)


def downsample(c):
    return 0.25 * (c[0::2, 0::2] + c[1::2, 0::2] + c[0::2, 1::2] + c[1::2, 1::2])
