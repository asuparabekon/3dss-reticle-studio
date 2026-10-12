"""Offline gate for an MSDF reticle atlas: header/descriptor per mip, then the NumPy shader port
(reticle_eval.py) against the exact raster of the drawing.

usage: python gate_reticle.py <drawing.svg> <atlas.dds>      (settings from <drawing>.reticle.ini)

Checks
  headers    every mip that may be resident mip 0 (texture_lod strips 0..2) decodes and validates
  strokes    isolated 0.2-1 px strokes keep their integrated darkness within +-5% of the true area over
             sub-pixel offsets, rotations and fractional LOD scales (a synthetic atlas, same pipeline)
  iou        magnified edges match the drawing: soft IoU (sum of min over sum of max coverage) >= 0.95
  corners    the sharpest corners and thinnest strokes keep their shape magnified 4x and 8x (2x and 4x with a
             max_zoom): soft IoU >= 0.9
  darkness   integrated darkness of the reticle within +-5% of the drawing (both sides blurred alike, so the
             comparison ignores where inside a few pixels the darkness lands)
  survival   every shape keeps at least 85% of its darkness at every tested scale
  dot        centre dot area within +-5% at max zoom; at 1x, the lit shapes together (they share a pixel)
A max_zoom in the settings limits every check to densities up to that zoom.
  pop        a footprint step across a mip boundary moves no pixel more than an equal step inside a level
             + 1/255 (sample positions fixed)
  stripped   the same image from resident mips 1 and 2 stays within +-5% darkness of mip 0
  isolation  minified (the two lowest scales), each tile draws within 1% of what it draws when its
             coverage reads see only its own coverage texels: no filter reaches another tile, the header or a
             distance channel
A drawing with Mark Switch marks runs every check except strokes once per mark, against the shared shapes plus
that mark's.
"""

import math
import sys
import tempfile
from pathlib import Path

import numpy as np

import raster
import reticle_build as rb
import reticle_format as rf
import reticle_eval as ev
import settings as st
import svg_reticle

# screen pixels per MOA: about 1x, mid and 10x of a 1-10x scope on a 1440p screen, with half-octave steps
SCALES_MOA = (0.40, 0.57, 1.2, 1.7, 4.0, 5.6)
ROTATIONS = (0.0, 0.6, 15.0)
OFFSETS = ((0.0, 0.0), (0.37, 0.21), (0.5, 0.5), (0.83, 0.64))
BLUR = 5


def marks_at(atlas, p, jx, jy, mark=0, **kw):
    result = ev.marks(atlas, p, jx, jy, mark=mark, pattern=True, **kw)
    if atlas.desc.colour_mode != "legacy":
        result["ink"] = 1 - (1 - result["ink"]) * (1 - result["illum"])
    if all(tile.role == rf.ROLE_PAINT for tile in atlas.desc.tiles):
        result["ink"] = result["paint"]
    return result


def roles_for_mark(roles, mark):
    """The shared groups plus those of one mark (1 based), keyed without the mark suffix."""
    out = {}
    for key, role in roles.items():
        if role.mark not in (0, mark):
            continue
        base = key.rsplit(" mark ", 1)[0]
        if base not in out:
            out[base] = rb.Role(
                base,
                role.role,
                role.colour,
                role.glows,
                material=role.material,
                materials=role.materials,
            )
        out[base].paths.extend(role.paths)
        out[base].ids.extend(role.ids)
    return out


def role_polys(roles, keys):
    """Overlap-free outline rings of the union of the given roles, in units."""
    return raster.merge(
        [
            [svg_reticle.contour_polygon(c, 24) for c in path]
            for k in keys
            for path in roles[k].paths
        ]
    )


def polygon_area(paths):
    total = 0.0
    for q in raster.merge(
        [[svg_reticle.contour_polygon(c, 64) for c in path] for path in paths]
    ):
        total += (q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]).sum() / 2
    return abs(total)


def to_screen(points, centre, upp, rot, off):
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    q = np.asarray(points, float) / upp
    return np.stack(
        [
            c * q[:, 0] + s * q[:, 1] + centre[0] + off[0],
            -s * q[:, 0] + c * q[:, 1] + centre[1] + off[1],
        ],
        1,
    )


def reference(polys, width, height, centre, upp, rot, off):
    return raster.coverage([to_screen(p, centre, upp, rot, off) for p in polys], width, height)


def blur(img, k=BLUR):
    pad = np.pad(img, k // 2 + 1)
    c = pad.cumsum(0).cumsum(1)
    out = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return out[: img.shape[0], : img.shape[1]] / (k * k)


def evaluate(atlas, w, h, centre, upp, rot=0.0, off=(0.0, 0.0), key="ink", mark=0, **kw):
    p, jx, jy = ev.screen_grid(w, h, centre, upp, rot, off)
    return marks_at(atlas, p, jx, jy, mark=mark, **kw)[key].reshape(h, w)


def soft_iou(a, b):
    return np.minimum(a, b).sum() / max(np.maximum(a, b).sum(), 1e-9)


class Gate:
    def __init__(self):
        self.results, self.failures = {}, []

    def check(self, name, ok, detail):
        self.results.setdefault(name, []).append(("PASS " if ok else "FAIL ") + detail)
        if not ok:
            self.failures.append(f"{name}: {detail}")

    def summary(self):
        lines = []
        for name, items in self.results.items():
            bad = sum(1 for f in self.failures if f.startswith(name + ":"))
            lines.append(f"{name}: {'FAIL' if bad else 'PASS'} ({len(items)} checks)")
        return lines


def stroke_test(gate, quality, compressed=False):
    """Synthetic isolated strokes through the real pipeline (format 4: distance tiles)."""
    widths = (0.05, 0.08, 0.12, 0.17, 0.25)
    rects = []
    for i, wu in enumerate(widths):
        rects.append((-40 + 20 * i - wu / 2, -30, wu, 20))  # vertical
        rects.append((-30, 4 + 10 * i - wu / 2, 40, wu))  # horizontal
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-60 -60 120 120">']
    svg.append(
        '<g xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" inkscape:groupmode="layer" inkscape:label="ink">'
    )
    svg += [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#ff0000"/>'
        for x, y, w, h in rects
    ]
    svg.append("</g></svg>")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "strokes.svg"
        path.write_text("\n".join(svg))
        s = st.Settings()
        s.units, s.quality, s.focal_plane, s.calibrated_magnification, s.holo = (
            "moa",
            quality,
            "ffp",
            1.0,
            False,
        )
        s.scale, s.center = 1.0, (0.0, 0.0)
        drawing = svg_reticle.read_reticle_svg(path)
        cal, _ = rb.calibrate(drawing, s)
        built = rb.build(rb.load_roles(drawing, cal), s, rb.SDF if compressed else None)
        atlas = ev.Atlas(built.images, 0)
    worst = 0.0
    for scale in (1.2, 1.68, 2.0, 2.83, 4.0):
        upp = 1 / scale
        for rot in ROTATIONS:
            for off in OFFSETS if rot == 0.0 else OFFSETS[1:2]:
                for x, y, wd, ht in rects:
                    wu = min(wd, ht)
                    if not 0.2 <= wu * scale <= 1.0:
                        continue
                    corners = to_screen(
                        [[x, y], [x + wd, y], [x, y + ht], [x + wd, y + ht]],
                        (0, 0),
                        upp,
                        rot,
                        (0, 0),
                    )
                    lo = corners.min(0) - 5
                    span = np.ceil(corners.max(0) + 5 - lo).astype(int)
                    p, jx, jy = ev.screen_grid(
                        span[0], span[1], (-lo[0], -lo[1]), upp, rot, off
                    )
                    pad = 5 * upp
                    near = (
                        (p[:, 0] > x - pad)
                        & (p[:, 0] < x + wd + pad)
                        & (p[:, 1] > y - pad)
                        & (p[:, 1] < y + ht + pad)
                    )
                    got = ev.marks(atlas, p[near], jx[near], jy[near])["ink"].sum()
                    err = got / (wd * ht * scale * scale) - 1
                    worst = max(worst, abs(err))
                    if abs(err) > 0.05:
                        gate.check(
                            "strokes",
                            False,
                            f"{wu * scale:.2f} px stroke at {scale} px/unit rot {rot} "
                            f"off {off}: {err:+.2%}",
                        )
    gate.check("strokes", worst <= 0.05, f"worst integrated darkness error {worst:.2%}")


def sharp_corners(rings, limit=12, angle=100.0):
    found = []
    for ring in rings:
        n = len(ring)
        for i in range(n):
            a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
            u, v = a - b, c - b
            lu, lv = np.linalg.norm(u), np.linalg.norm(v)
            if lu < 1e-9 or lv < 1e-9:
                continue
            theta = math.degrees(math.acos(max(-1, min(1, np.dot(u, v) / (lu * lv)))))
            if theta < angle:
                found.append((theta, tuple(b)))
    found.sort()
    picked = []
    for theta, p in found:
        if all(math.dist(p, q) > 2.0 for q in picked):
            picked.append(p)
        if len(picked) >= limit:
            break
    return picked


CORNER_IOU = 0.9


def test_scales(units, max_zoom):
    """Screen pixels per unit tested, and the density beyond which the reticle is never seen (None = no limit). A
    reticle never seen beyond max_zoom is only checked up to that density (at least the two lowest scales).
    """
    to_units = 1.0 if units == "moa" else 1 / rb.MOA_PER_MIL
    limit = rb.PX_PER_MOA_1X * max_zoom * 1.25 if max_zoom else None
    kept = [s for s in SCALES_MOA if limit is None or s <= limit] or list(SCALES_MOA[:2])
    if len(kept) < 2:
        kept = list(SCALES_MOA[:2])
    return [s / to_units for s in kept], limit


def corners(atlas, roles, units, max_zoom, spot_count=12, mark=0, key="ink"):
    """The sharpest corners and thinnest shapes, magnified beyond the top density: [(magnification, worst soft
    IoU, spots, soft IoU per spot (None where the spot holds no shape))], empty when even the coarsest tile stays
    minified (more texels than pixels) up to max_zoom (the distance field never draws there); and the finest
    tile's texels per unit."""
    to_units = 1.0 if units == "moa" else 1 / rb.MOA_PER_MIL
    scales, limit = test_scales(units, max_zoom)
    texels = min(
        t.uv_size * atlas.dims[0] / (2 * t.half_extent)
        for t in atlas.desc.tiles
        if t.mark in (0, mark % atlas.desc.marks + 1)
    )
    if max_zoom and texels >= limit / to_units:
        return [], texels
    ink_keys = [k for k in roles if not k.startswith("paint")] or list(roles)
    ink_polys = role_polys(roles, ink_keys)
    thin = sorted(
        ((rb.stroke_width(p), svg_reticle.bounds(p)) for k in ink_keys for p in roles[k].paths)
    )[:6]
    spots = sharp_corners(ink_polys, spot_count) + [
        ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for _, b in thin
    ]
    # headroom beyond the top density; a texture sized to max_zoom gets half of it
    out = []
    for magnify in (2.0, 4.0) if max_zoom else (4.0, 8.0):
        upp_px = 1 / (magnify * scales[-1])  # units per pixel at magnify x the max-zoom density
        ious = []
        for sx, sy in spots:
            span = 64
            centre = (span / 2 - sx / upp_px, span / 2 - sy / upp_px)
            got = evaluate(atlas, span, span, centre, upp_px, key=key, mark=mark)
            ref = reference(ink_polys, span, span, centre, upp_px, 0, (0, 0))
            ious.append(soft_iou(got, ref) if ref.sum() > 1 else None)
        out.append(
            (magnify, min([v for v in ious if v is not None] or [1.0]), len(spots), ious)
        )
    return out, texels


def run(
    roles, images, units="moa", quality="normal", strokes=True, max_zoom=None, mark=0, key="ink"
):
    gate = Gate()
    check = gate.check
    to_units = 1.0 if units == "moa" else 1 / rb.MOA_PER_MIL
    scales, limit = test_scales(units, max_zoom)
    atlases = {}
    for m in range(3):
        try:
            a = ev.Atlas(images, m)
            atlases[m] = a
            check(
                "headers",
                a.recognised and a.orig_mip == m,
                f"resident from mip {m}: orig_mip {a.orig_mip}, {len(a.desc.tiles) if a.recognised else 0} tiles",
            )
        except (ValueError, IndexError) as e:
            check("headers", False, f"resident from mip {m}: {e}")
    if gate.failures:
        return gate
    atlas = atlases[0]

    if strokes:
        stroke_test(gate, quality, atlas.compressed)

    ink_keys = [k for k in roles if not k.startswith("paint")] or list(roles)
    ink_polys = role_polys(roles, ink_keys)
    x0, y0, x1, y1 = svg_reticle.bounds([c for k in ink_keys for c in roles[k].contours])

    # tiles that stay minified (more texels than pixels) up to max_zoom are never drawn by their distance field
    corner_results, texels = corners(atlas, roles, units, max_zoom, mark=mark, key=key)
    never_magnified = not corner_results

    # magnified edges around the centre
    if never_magnified:
        check("iou", True, f"n/a: {texels:.1f} texels per unit, minified up to max_zoom")
    else:
        upp = 0.05 * to_units
        w = h = int(8 * to_units / upp)
        got = evaluate(atlas, w, h, (w / 2, h / 2), upp, key=key, mark=mark)
        ref = reference(ink_polys, w, h, (w / 2, h / 2), upp, 0, (0, 0))
        check(
            "iou",
            soft_iou(got, ref) >= 0.95,
            f"centre at 20 px/MOA: soft IoU {soft_iou(got, ref):.4f}",
        )

    if never_magnified:
        check("corners", True, f"n/a: {texels:.1f} texels per unit, minified up to max_zoom")
    for magnify, worst, n, _ in corner_results:
        check(
            "corners",
            worst >= CORNER_IOU,
            f"{magnify:g}x max-zoom density: worst soft IoU {worst:.3f} over {n} spots",
        )

    shapes = [
        (sid or f"shape {index + 1}", svg_reticle.bounds(path))
        for index, (sid, path) in enumerate(
            (sid, path) for k in ink_keys for sid, path in zip(roles[k].ids, roles[k].paths)
        )
    ]
    for scale in scales:
        upp = 1 / scale
        half = min(
            40.0 * to_units,
            max(abs(x0), abs(x1), abs(y0), abs(y1)) + max(2 * to_units, 6 / scale),
        )
        w = h = int(2 * half * scale) + 2 * BLUR + 8
        centre = (w / 2, h / 2)
        for rot in ROTATIONS:
            for off in OFFSETS if rot == 0.0 else OFFSETS[1:2]:
                tag = f"{scale * to_units:g} px/MOA rot {rot} off {off}"
                got = blur(evaluate(atlas, w, h, centre, upp, rot, off, key=key, mark=mark))
                ref = blur(reference(ink_polys, w, h, centre, upp, rot, off))
                inner = (slice(BLUR + 2, h - BLUR - 2), slice(BLUR + 2, w - BLUR - 2))
                err = got[inner].sum() / ref[inner].sum() - 1
                check("darkness", abs(err) <= 0.05, f"{tag}: {err:+.2%}")
                worst, worst_id = 1e9, None
                for sid, b in shapes:
                    pts = to_screen(
                        [[b[0], b[1]], [b[2], b[1]], [b[0], b[3]], [b[2], b[3]]],
                        centre,
                        upp,
                        rot,
                        off,
                    )
                    a0, a1 = np.floor(pts.min(0) - BLUR).astype(int), np.ceil(
                        pts.max(0) + BLUR
                    ).astype(int)
                    if a0.min() < BLUR + 2 or a1[0] > w - BLUR - 2 or a1[1] > h - BLUR - 2:
                        continue
                    r = ref[a0[1] : a1[1], a0[0] : a1[0]].sum()
                    if r > 0:
                        ratio = got[a0[1] : a1[1], a0[0] : a1[0]].sum() / r
                        if ratio < worst:
                            worst, worst_id = ratio, sid
                if worst_id:
                    check(
                        "survival",
                        worst >= 0.85,
                        f"{tag}: weakest shape {worst_id} keeps {worst:.3f}",
                    )

    if "illum" in roles:
        lit = roles["illum"]
        dot = [
            [c]
            for c in lit.contours
            if max(abs(v) for v in svg_reticle.bounds([c])) < 1.0 * to_units
        ]
        # the window holds the filter footprint too (3 px), which matters when the top scale is minified
        for scale, paths, label, reach in (
            (scales[-1], dot, "dot", 1.25 * to_units + 3 / scales[-1]),
            (scales[0], lit.paths, "lit shapes", None),
        ):
            if not paths:
                continue
            upp = 1 / scale
            if reach is None:
                bx0, by0, bx1, by1 = svg_reticle.bounds(lit.contours)
                reach = (
                    max(math.hypot(x, y) for x in (bx0, bx1) for y in (by0, by1)) + 4 / scale
                )  # + the filter
            area = polygon_area(paths)
            for off in OFFSETS:
                w = h = int(2 * reach * scale) + 2
                p, jx, jy = ev.screen_grid(w, h, (w / 2, h / 2), upp, 0, off)
                disc = np.hypot(p[:, 0], p[:, 1]) <= reach
                got = (
                    marks_at(atlas, p[disc], jx[disc], jy[disc], mark=mark)["illum"].sum()
                    * upp
                    * upp
                )
                err = got / area - 1
                check(
                    "dot",
                    abs(err) <= 0.05,
                    f"{label} at {scale * to_units:g} px/MOA off {off}: area {err:+.2%}",
                )

    # only the filter footprint changes (fixed sample positions), so edge motion does not count as a pop
    upp_base = 1 / scales[min(2, len(scales) - 1)]
    tile = atlas.desc.tiles[0]
    texels_per_unit = tile.uv_size * atlas.dims[0] / (2 * tile.half_extent)
    k = math.ceil(math.log2(texels_per_unit * upp_base))
    s_edge = 2**k / (texels_per_unit * upp_base)
    eps = 0.002

    def frame(s, stretch):
        p, jx, jy = ev.screen_grid(160, 160, (80.3, 80.2), upp_base * s)
        return marks_at(atlas, p, jx * stretch, jy * stretch, mark=mark)[key]

    step_edge = np.abs(frame(s_edge, 1 + eps) - frame(s_edge, 1 - eps)).max()
    step_mid = np.abs(frame(s_edge * 1.41, 1 + eps) - frame(s_edge * 1.41, 1 - eps)).max()
    check(
        "pop",
        step_edge <= step_mid + 1 / 255,
        f"boundary step {step_edge:.4f} vs in-level {step_mid:.4f}",
    )

    reach_all = max(abs(x0), abs(x1), abs(y0), abs(y1))
    for i, t in enumerate(atlas.desc.tiles):
        if t.mark not in (0, mark % atlas.desc.marks + 1):
            continue
        isolated = ev.coverage_only(atlas, i)
        reach = t.half_extent * (1 - ev.GUTTER)
        for scale in scales[:2]:
            n = int(min(2 * reach * scale + 2, 480))
            for off in OFFSETS:
                p, jx, jy = ev.screen_grid(
                    n,
                    n,
                    (n / 2 - t.centre[0] * scale, n / 2 - t.centre[1] * scale),
                    1 / scale,
                    0,
                    off,
                )
                inside = np.abs(p - np.array(t.centre)).max(1) <= reach
                got = ev.tile_coverage(atlas, t, p[inside], jx[inside], jy[inside]).sum()
                alone = ev.tile_coverage(isolated, t, p[inside], jx[inside], jy[inside]).sum()
                # 1% of the tile's own coverage, at least a hundredth of a pixel
                ok = abs(got - alone) <= max(0.01 * alone, 0.01)
                check(
                    "isolation",
                    ok,
                    f"tile {i} at {scale * to_units:g} px/MOA off {off}: {got:.2f} vs {alone:.2f} "
                    "on its own",
                )
    for m in (1, 2):
        mid = scales[min(3, len(scales) - 1)]
        w = h = int(min(max(140, 2 * (reach_all + 2 * to_units) * mid + 20), 480))
        base = evaluate(
            atlas, w, h, (w / 2, h / 2), 1 / mid, 0, (0.37, 0.21), key=key, mark=mark
        ).sum()
        strip = evaluate(
            atlases[m], w, h, (w / 2, h / 2), 1 / mid, 0, (0.37, 0.21), key=key, mark=mark
        ).sum()
        check(
            "stripped",
            abs(strip / base - 1) <= 0.05,
            f"resident from mip {m}: darkness {strip / base - 1:+.2%}",
        )
    return gate


# Format 4 tile choice: each tile's sharpest corners and thinnest shapes magnified to these screen pixels per texel of
# that tile; a tile may round them this much more (soft IoU) than the one it replaces
CHOICE_MAGNIFICATIONS = (4.0, 8.0)
CORNER_LOSS = 0.02
CHOICE_SPOTS = 48


MARK_KEYS = {rf.ROLE_INK: "ink", rf.ROLE_ILLUM: "illum", rf.ROLE_PAINT: "paint"}


def tile_spots(roles, tiles, index):
    """Corner and thin-shape spots the tile draws: of its own shapes (role; a paint tile: its colour), in its
    region and not in a finer tile's of its group. Also the outline the marks output of its role is compared with
    (ink: ink and illuminated shapes, illum: illuminated, paint: every paint colour) and that output's key.
    """
    t = tiles[index]
    key = MARK_KEYS[t.role]
    own = [
        k
        for k, r in roles.items()
        if r.role == t.role
        and r.material == t.material
        and (t.role != rf.ROLE_PAINT or tuple(r.colour) == tuple(t.colour))
    ]
    drawn = [
        k
        for k, r in roles.items()
        if r.role == t.role or (t.role == rf.ROLE_INK and r.role == rf.ROLE_ILLUM)
    ]
    own_polys = role_polys(roles, own)
    thin = sorted(
        ((rb.stroke_width(p), svg_reticle.bounds(p)) for k in own for p in roles[k].paths)
    )[:6]
    spots = sharp_corners(own_polys, CHOICE_SPOTS) + [
        ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for _, b in thin
    ]
    inside = lambda u, q: max(abs(q[0] - u.centre[0]), abs(q[1] - u.centre[1])) <= u.region_half
    finer = [
        u
        for u in tiles[:index]
        if (u.role, tuple(u.colour), u.mark, u.material)
        == (t.role, tuple(t.colour), t.mark, t.material)
    ]
    return (
        role_polys(roles, drawn),
        [q for q in spots if inside(t, q) and not any(inside(u, q) for u in finer)],
        key,
    )


def spot_ious(atlas, polys, spots, px_per_unit, key="ink", mark=0):
    out = []
    for magnify in CHOICE_MAGNIFICATIONS:
        upp = 1 / (magnify * px_per_unit)
        for sx, sy in spots:
            centre = (32 - sx / upp, 32 - sy / upp)
            ref = reference(polys, 64, 64, centre, upp, 0, (0, 0))
            out.append(
                soft_iou(evaluate(atlas, 64, 64, centre, upp, key=key, mark=mark), ref)
                if ref.sum() > 1
                else None
            )
    return out


def choose_kinds(roles, plans, settings):
    """Format 4 tile kinds: a tile takes one true distance (rb.SDF, a quarter of the RGBA8 memory) where its corner
    spots, magnified CHOICE_MAGNIFICATIONS times its texel size, keep their shape within CORNER_LOSS of the
    multi-channel tile, else the multi-channel distance (rb.MSDF, half); each tile is tried alone among
    multi-channel tiles, in the marks it shows in. A tile that fits as multi-channel without a larger texture
    takes it anyway. Returns (kinds, notes)."""
    n = len(plans)
    marks = sorted({r.mark for r in roles.values()} - {0}) or [0]
    wide = [rb.MSDF] * n
    wide_images = rb.assemble(plans, roles, settings, wide).images

    def loss(images, reference_images, i):
        worst = 0.0
        compared = False
        for mark in [m for m in marks if plans[i]["role"].mark in (0, m)]:
            mark_roles = roles_for_mark(roles, mark) if mark else roles
            atlas, ref_atlas = ev.Atlas(images, 0), ev.Atlas(reference_images, 0)
            tiles = atlas.desc.tiles
            t = tiles[i]
            polys, spots, key = tile_spots(mark_roles, tiles, i)
            texels = t.uv_size * atlas.dims[0] / (2 * t.half_extent)
            r = ref_atlas.desc.tiles[i]
            for got, ref in zip(
                spot_ious(atlas, polys, spots, texels, key, max(mark - 1, 0)),
                spot_ious(
                    ref_atlas,
                    polys,
                    spots,
                    r.uv_size * ref_atlas.dims[0] / (2 * r.half_extent),
                    key,
                    max(mark - 1, 0),
                ),
            ):
                if got is not None and ref is not None:
                    compared = True
                    worst = max(worst, ref - got)
        return worst if compared else float("inf")

    notes, kinds = [], []
    for i, p in enumerate(plans):
        trial = list(wide)
        trial[i] = rb.SDF
        worst = loss(rb.assemble(plans, roles, settings, trial, wide).images, wide_images, i)
        ok = worst <= CORNER_LOSS
        kinds.append(rb.SDF if ok else rb.MSDF)
        notes.append(
            f"{p['role'].key} {p['size']} px: {'distance' if ok else 'multi-channel'} "
            f"(as one distance its corners lose up to {worst:.3f})"
        )
    for i in range(n):
        trial = kinds[:i] + [rb.MSDF] + kinds[i + 1 :]
        if kinds[i] == rb.SDF and rb.layout_size(plans, trial) == rb.layout_size(plans, kinds):
            kinds = trial
            notes[i] += ", multi-channel as it fits the same texture"
    return kinds, notes


def compressed_corners_ok(roles, compressed, uncompressed, settings):
    """The gate's corners check holds for the compressed atlas wherever it holds for the uncompressed one."""
    marks = sorted({r.mark for r in roles.values()} - {0}) or [0]
    ok = True
    got_atlas, ref_atlas = ev.Atlas(compressed, 0), ev.Atlas(uncompressed, 0)
    for mark in marks:
        mark_roles = roles_for_mark(roles, mark) if mark else roles
        channels = [(mark_roles, "ink")]
        paint = {name: role for name, role in mark_roles.items() if role.role == rf.ROLE_PAINT}
        if paint and len(paint) != len(mark_roles):
            channels.append((paint, "paint"))
        for selected, key in channels:
            args = (selected, settings.units, settings.max_zoom)
            got = corners(got_atlas, *args, mark=max(mark - 1, 0), key=key)[0]
            ref = corners(ref_atlas, *args, mark=max(mark - 1, 0), key=key)[0]
            ok &= all(
                g >= CORNER_IOU or r < CORNER_IOU
                for (_, g, _, _), (_, r, _, _) in zip(got, ref)
            )
    return ok


def run_marks(roles, images, units="moa", quality="normal", max_zoom=None):
    """run() once per Mark Switch mark (mark 0 = a drawing without marks). Yields (mark, gate)."""
    marks = sorted({r.mark for r in roles.values()} - {0}) or [0]
    for mark in marks:
        selected = roles_for_mark(roles, mark) if mark else roles
        gate = run(
            selected, images, units, quality, mark == marks[0], max_zoom, mark=max(mark - 1, 0)
        )
        paint = {name: role for name, role in selected.items() if role.role == rf.ROLE_PAINT}
        if paint and len(paint) != len(selected) and not gate.failures:
            painted = run(
                paint,
                images,
                units,
                quality,
                False,
                max_zoom,
                mark=max(mark - 1, 0),
                key="paint",
            )
            for name, results in painted.results.items():
                gate.results["paint_" + name] = results
            gate.failures.extend("paint_" + failure for failure in painted.failures)
        yield mark, gate


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    svg_path, dds = Path(sys.argv[1]), sys.argv[2]
    s = st.load(st.template_path(svg_path))
    drawing = svg_reticle.read_reticle_svg(svg_path)
    cal, errors = rb.calibrate(drawing, s)
    if drawing.errors or errors:
        sys.exit("\n".join(drawing.errors + errors))
    failed = False
    for mark, gate in run_marks(
        rb.load_roles(drawing, cal, s.paint_glows, s.colour_mode != "legacy"),
        rb.read_dds(dds),
        s.units,
        s.quality,
        s.max_zoom,
    ):
        if mark:
            print(f"mark {mark}:")
        print("\n".join(gate.summary()))
        for f in gate.failures[:60]:
            print("  FAIL", f)
        failed = failed or bool(gate.failures)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
