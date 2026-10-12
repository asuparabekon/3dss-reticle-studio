"""Makes a game-ready MSDF reticle from an SVG drawing.

usage: python make_reticle.py <drawing.svg> [--out <folder>] [--no-gate]

Reads <drawing>.reticle.ini next to the SVG (a commented template is written there the first time) and writes
<drawing>_reticle/ (or --out) with:
  gamedata/textures/<texture>.dds    the reticle, at the path of the bitmap it replaces (the settings' texture)
  preview_*.png                      how it looks at 1x, 4x and 10x, lit and unlit (per Mark Switch mark)
  report.txt                         what was found, sizes, checks and problems
Copy the gamedata folder into a mod that loads after the one with the bitmap. See AUTHORING.md.
"""

import argparse
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

import gate_reticle
import reticle_build as rb
import reticle_eval as ev
import reticle_format as rf
import settings as st
import svg_reticle

PREVIEW_DENSITY_MOA = {"1x": 0.47, "4x": 1.9, "10x": 4.7}  # screen px per MOA on a 1440p screen


class Report:
    def __init__(self):
        self.lines, self.errors, self.warnings = [], [], []

    def add(self, text=""):
        self.lines.append(text)

    def text(self):
        out = list(self.lines)
        if self.warnings:
            out += ["", "Warnings"] + [f"  - {w}" for w in self.warnings]
        if self.errors:
            out += ["", "Errors (DDS export blocked)"] + [f"  - {e}" for e in self.errors]
        return "\n".join(out) + "\n"


def merged_roles(roles):
    """The groups of every mark folded together by role, for the subtension checks."""
    out = {}
    for key, role in roles.items():
        base = "illum" if role.role == rf.ROLE_ILLUM else key.rsplit(" mark ", 1)[0]
        if base not in out:
            out[base] = rb.Role(base, role.role, role.colour, role.glows)
        out[base].paths.extend(role.paths)
        out[base].ids.extend(role.ids)
    return out


def run_checks(settings, roles, report):
    roles = merged_roles(roles)
    ok = True
    for name, values in settings.checks:
        try:
            if name == "dot":
                lit = roles["illum"]
                dots = [
                    c
                    for c in lit.contours
                    if max(abs(v) for v in svg_reticle.bounds([c])) < float(values[0])
                ]
                x0, y0, x1, y1 = svg_reticle.bounds(dots)
                got, want, tol = (
                    max(x1 - x0, y1 - y0),
                    float(values[0]),
                    0.02 * float(values[0]),
                )
                detail = f"centre dot diameter {got:.3f}, centred at {(x0 + x1) / 2:+.3f}, {(y0 + y1) / 2:+.3f}"
                good = (
                    abs(got - want) <= tol
                    and abs((x0 + x1) / 2) <= tol
                    and abs((y0 + y1) / 2) <= tol
                )
            elif name == "ring":
                lit = roles["illum"]
                inner, outer = float(values[0]), float(values[1])
                radii = [
                    math.hypot(*p)
                    for c in lit.contours
                    for p in svg_reticle.contour_polygon(c, 64)
                ]
                ring = [r for r in radii if r > inner / 4]
                got = (2 * min(ring), 2 * max(ring))
                good = (
                    abs(got[0] - inner) <= 0.03 * inner and abs(got[1] - outer) <= 0.03 * outer
                )
                detail = f"ring {got[0]:.3f} / {got[1]:.3f}"
                want = f"{inner} / {outer}"
            else:
                sid, side, want = values[0], values[1], float(values[2])
                tol = float(values[3]) if len(values) > 3 else 0.02
                paths = [p for r in roles.values() for i, p in zip(r.ids, r.paths) if i == sid]
                if not paths:
                    raise KeyError(f"no shape with id {sid}")
                x0, y0, x1, y1 = svg_reticle.bounds(paths[0])
                got = {
                    "left": x0,
                    "right": x1,
                    "top": y0,
                    "bottom": y1,
                    "centre_x": (x0 + x1) / 2,
                    "centre_y": (y0 + y1) / 2,
                }[side]
                good = abs(got - want) <= tol
                detail = f"{sid} {side} {got:.3f}"
        except (KeyError, IndexError, ValueError) as e:
            report.errors.append(f"check '{name}': {e}")
            ok = False
            continue
        ok &= good
        report.add(f"  {'ok  ' if good else 'FAIL'} {name}: {detail} (wanted {want})")
        if not good:
            report.warnings.append(
                f"check '{name}' failed: {detail}, wanted {want}; is the scale or centre right?"
            )
    return ok


def preview(atlas_images, roles, settings, folder):

    atlas = ev.Atlas(atlas_images, 0)
    to_moa = 1.0 if settings.units == "moa" else rb.MOA_PER_MIL
    x0, y0, x1, y1 = svg_reticle.bounds([c for r in roles.values() for c in r.contours])
    reach = max(abs(x0), abs(x1), abs(y0), abs(y1)) * 1.05
    top = settings.preview_colour
    peak = max(top) or 1
    marks = sorted({r.mark for r in roles.values()} - {0})
    written = []
    if atlas.desc.states:
        marks = list(range(1, len(atlas.desc.states) + 1))
    for mark in marks or [0]:
        tag = f"state{mark}_" if atlas.desc.states else f"mark{mark}_" if marks else ""
        written += preview_mark(
            atlas, settings, folder, reach, to_moa, top, peak, max(mark - 1, 0), tag
        )
    return written


# exposure of a bright day and of a night scene (the game's tone map scale) for the lit previews
PREVIEW_LIGHT = {"day": (0.75, 0.6), "night": (0.04, 8.0)}


def preview_mark(atlas, settings, folder, reach, to_moa, top, peak, mark, tag):
    from PIL import Image

    written = []
    steps = settings.steps
    level = steps[min(mark, len(steps) - 1)] if steps else 1.0
    # reticle_msdf_illumination: the lit hue saturated (RMSDF_HUE_SATURATION), the level from the steps or the colour
    colour = (np.array(top, np.float32) / peak) ** 3.0 * (1.0 if steps else peak)
    if atlas.desc.states:
        state = atlas.desc.states[mark % len(atlas.desc.states)]
        level = state.brightness
        if atlas.desc.colour_mode == "authored":
            colour = np.asarray(state.colour or (1, 1, 1), np.float32)
        if atlas.desc.material_values:
            level = 1.0
            if atlas.desc.colour_mode == "authored":
                colour = np.ones(3, np.float32)
    for label, density in PREVIEW_DENSITY_MOA.items():
        px_per_unit = density * to_moa
        half = int(min(reach * px_per_unit, 900)) + 8
        w = h = 2 * half
        p, jx, jy = ev.screen_grid(w, h, (half, half), 1 / px_per_unit)
        m = ev.marks(atlas, p, jx, jy, mark=mark)
        paint = m["paint_rgb"] / np.maximum(m["paint"], 1e-6)[:, None]
        core = ev.emission(m, colour, settings.luminance * level)
        holo = settings.holo and atlas.header["flags"] & rf.FLAG_HOLO
        if holo:
            core *= ev.hologram(atlas.desc.hologram, p, level, jx=jx, jy=jy)[:, None]
        core = core.reshape(h, w, 3)
        for light, (grey, exposure) in PREVIEW_LIGHT.items():
            base = np.full((len(p), 3), grey, np.float32)
            base = base * (1 - m["paint"][:, None]) + paint * grey * m["paint"][:, None]
            base = (base * (1 - m["ink"])[:, None]).reshape(h, w, 3)
            for lit, name in ((False, "off"), (True, "on")):
                if not lit and light == "night":
                    continue
                img = (
                    ev.compose(core, base, exposure, settings.glow_strength, settings.glow_size)
                    if lit
                    else base
                )
                out = (np.clip(img, 0, 1) * 255).astype(np.uint8)
                path = folder / f"preview_{tag}{label}_{light + '_' if lit else ''}{name}.png"
                Image.fromarray(out).save(path)
                written.append(path.name)
        if holo and atlas.desc.hologram["noise_animated"] > 0 and label == "1x":
            # the animated noise over a second: frames side by side, at night
            grey, exposure = PREVIEW_LIGHT["night"]
            base = np.full((h, w, 3), grey, np.float32) * (1 - m["ink"]).reshape(h, w, 1)
            frames = []
            for k in range(4):
                c = ev.emission(m, colour, settings.luminance * level)
                c *= ev.hologram(atlas.desc.hologram, p, level, time=k * 0.25, jx=jx, jy=jy)[
                    :, None
                ]
                frames.append(
                    np.clip(
                        ev.compose(
                            c.reshape(h, w, 3),
                            base,
                            exposure,
                            settings.glow_strength,
                            settings.glow_size,
                        ),
                        0,
                        1,
                    )
                )
            path = folder / f"preview_{tag}{label}_noise_strip.png"
            Image.fromarray((np.concatenate(frames, 1) * 255).astype(np.uint8)).save(path)
            written.append(path.name)
    return written


def build_reticle(
    svg_path, settings, out, no_gate=False, progress=lambda message: None, previews=True
):
    """Shared CLI/editor export. Validate before atomically replacing the DDS."""
    svg_path, out = Path(svg_path), Path(out)
    progress("Reading SVG and calibration")
    report = Report()
    report.add(f"Reticle report for {svg_path.name}")
    report.add("")
    drawing = svg_reticle.read_reticle_svg(svg_path)
    report.errors += drawing.errors
    report.warnings += drawing.warnings
    counts = {"ink": 0, "illum": 0, "paint": 0}
    for sh in drawing.shapes:
        counts[sh.role] += 1
    report.add(
        f"Found {counts['ink']} ink shapes, {counts['illum']} illuminated, {counts['paint']} painted."
    )
    marks = sorted({sh.mark for sh in drawing.shapes} - {0})
    if marks:
        report.add(f"SVG patterns: {', '.join(str(m) for m in marks)}")
        if marks != list(range(1, max(marks) + 1)):
            report.warnings.append(
                f"marks {marks} skip numbers; a skipped mark shows only the shared shapes"
            )
    if settings.colour_mode != "legacy" and not drawing.errors:
        from reticle_states import resolve_states

        states = resolve_states(settings, marks)
        report.add(
            f"Switch states: {len(states)}; illumination source: {settings.colour_mode}. The sight must supply these Mark Switch selections."
        )
    calibration, errors = rb.calibrate(drawing, settings)
    report.errors += errors
    if report.errors:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.txt").write_text(report.text())
        return report
    for note in calibration.notes:
        report.add(note)
    unit = settings.units.upper()
    report.add(f"1 SVG unit = {calibration.units_per_user_unit:.5g} {unit}")
    roles = rb.load_roles(
        drawing, calibration, settings.paint_glows, settings.colour_mode != "legacy"
    )
    x0, y0, x1, y1 = svg_reticle.bounds([c for r in roles.values() for c in r.contours])
    extent = max(abs(x0), abs(x1), abs(y0), abs(y1))
    thinnest = min(rb.stroke_width(p) for r in roles.values() for p in r.paths)
    to_moa = 1.0 if settings.units == "moa" else rb.MOA_PER_MIL
    report.add(
        f"The reticle reaches {extent:.2f} {unit} from the centre; the thinnest stroke is {thinnest:.3f} {unit}."
    )
    if not 2 <= extent * to_moa <= 3000:
        report.warnings.append(
            f"the reticle reaches {extent:.3g} {unit} from the centre, which is unusual; "
            "check the calibrate line or the scale setting"
        )
    if thinnest * to_moa < 0.05:
        report.warnings.append(
            f"the thinnest stroke is {thinnest:.3g} {unit}; strokes below 0.05 MOA vanish in game"
        )
    if settings.checks:
        report.add("")
        report.add("Checks from the settings file")
        if not run_checks(settings, roles, report):
            report.errors.append("Authored dimension checks failed; export blocked")
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.txt").write_text(report.text())
            return report
    progress("Generating distance fields and coverage mips")
    plans = rb.plan_tiles(roles, settings)
    kinds, kind_notes = None, []
    if settings.compression != "none":
        progress("Choosing compression without losing corners")
        try:
            kinds, kind_notes = gate_reticle.choose_kinds(roles, plans, settings)
        except rb.AtlasPackingError:
            if settings.compression != "auto":
                raise
            report.warnings.append(
                "Automatic compression kept the texture uncompressed: the compressed "
                "layout exceeds the atlas limit. Detail is unchanged."
            )
            progress("Compressed layout is too large; keeping full detail in RGBA8")
    atlas = rb.assemble(plans, roles, settings, kinds)
    if settings.compression == "auto" and kinds is not None:
        uncompressed = rb.assemble(plans, roles, settings)
        if not gate_reticle.compressed_corners_ok(
            roles, atlas.images, uncompressed.images, settings
        ):
            kinds = None
            atlas = uncompressed
            report.warnings.append(
                "compression = auto kept the texture uncompressed: compressed, it misses the corners "
                "check the uncompressed texture meets"
            )
    h, w = atlas.images[0].shape[:2]
    report.add("")
    report.add(
        f"Texture {settings.texture}.dds: {w} x {h} {'BC5' if atlas.compressed else 'RGBA8'}, "
        f"{len(atlas.images)} mips, {atlas.bytes / 1048576:.1f} MB of video memory"
    )
    for i, p in enumerate(atlas.plans):
        texels = p["size"] / (2 * p["half_extent"])
        kind = (
            "" if kinds is None else (" distance" if kinds[i] == rb.SDF else " multi-channel")
        )
        report.add(
            f"  {p['role'].key:14s} tile {p['size']:4d} px{kind} covering +-{p['region_half']:.1f} {unit} "
            f"around ({p['centre'][0]:.1f}, {p['centre'][1]:.1f}): {texels:.1f} px per {unit}, "
            f"{texels * p['thin']:.1f} px across its thinnest stroke"
        )
    if kinds is not None:
        report.add(
            "  compression, per tile (the corners check with the tile as one distance): "
            + "; ".join(kind_notes)
        )
    dds = out / "gamedata" / "textures" / (settings.texture.replace("\\", "/") + ".dds")
    dds.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".reticle-", suffix=".dds", dir=dds.parent)
    os.close(fd)
    try:
        # Check the actual serialized DDS, including its resident headers and mips.
        rb.write_dds(temporary, atlas)
        encoded_images = rb.read_dds(temporary)
        if not no_gate:
            progress("Checking every mark and resident mip")
            report.add("")
            report.add("Quality checks")
            for mark, gate in gate_reticle.run_marks(
                roles, encoded_images, settings.units, settings.quality, settings.max_zoom
            ):
                prefix = f"mark {mark}: " if mark else ""
                report.lines += ["  " + prefix + line for line in gate.summary()]
                for f in gate.failures[:20]:
                    report.errors.append(f"quality check {prefix}{f}")
        out.mkdir(parents=True, exist_ok=True)
        if report.errors:
            (out / "report.txt").write_text(report.text())
            return report
        progress("Writing previews and validated DDS")
        shots = preview(encoded_images, roles, settings, out) if previews else []
        report.add("")
        report.add("Previews: " + ", ".join(shots))
        if no_gate:
            report.warnings.append("Quality checks skipped: this is an unverified draft")
        report.add("")
        report.add(f"Output: {out}")
        (out / "report.txt").write_text(report.text())
        os.replace(temporary, dds)
        return report
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description="Make an MSDF reticle from an SVG drawing.")
    ap.add_argument("svg")
    ap.add_argument("--out")
    ap.add_argument(
        "--no-gate", action="store_true", help="skip quality checks for an unverified draft"
    )
    a = ap.parse_args()
    svg_path = Path(a.svg).resolve()
    if not svg_path.exists():
        sys.exit(f"no such file: {svg_path}")
    ini = st.template_path(svg_path)
    if not ini.exists():
        st.write_template(svg_path)
        print(
            f"wrote a settings template: {ini}\nfill it in (at least the scale or a calibrate layer) and run again"
        )
        return 1
    try:
        settings = st.load(ini)
    except (st.SettingsError, ValueError) as e:
        sys.exit(f"{ini.name}: {e}")
    out = Path(a.out).resolve() if a.out else svg_path.with_name(svg_path.stem + "_reticle")
    report = build_reticle(svg_path, settings, out, a.no_gate)
    print(report.text())
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
