"""Geometry-only DDS size bounds; no distance-field generation on the UI thread."""

import reticle_build as rb
import svg_reticle


def dds_bytes(width, height, compressed):
    total = 148 if compressed else 128
    for mip in range(max(width, height).bit_length()):
        w, h = max(1, width >> mip), max(1, height >> mip)
        total += -(-w // 4) * -(-h // 4) * 16 if compressed else w * h * 4
    return total


class Estimator:
    def render(self, source, settings):
        drawing = svg_reticle.read_reticle_svg(source)
        calibration, errors = rb.calibrate(drawing, settings)
        if drawing.errors or errors:
            raise ValueError("; ".join(drawing.errors + errors))
        roles = rb.load_roles(
            drawing, calibration, settings.paint_glows, settings.colour_mode != "legacy"
        )
        plans = []
        for role in roles.values():
            plans.extend(
                rb.plan_role(role, settings.quality, settings.units, rb.density_cap(settings))
            )
        for plan in plans:
            plan["materials"] = settings.colour_mode != "legacy"
        if not plans:
            raise ValueError("SVG has no reticle shapes")
        rgba = dds_bytes(*rb.layout_size(plans, None), False)
        if settings.compression == "none":
            return f"Approx. DDS: {rgba / 1048576:.2f} MiB (RGBA8, including all mips)"
        try:
            small = dds_bytes(*rb.layout_size(plans, [rb.SDF] * len(plans)), True)
            large = dds_bytes(*rb.layout_size(plans, [rb.MSDF] * len(plans)), True)
        except rb.AtlasPackingError:
            if settings.compression != "auto":
                raise
            return f"Approx. DDS: {rgba / 1048576:.2f} MiB RGBA8 (compressed layout exceeds atlas limit)"
        label = f"Approx. DDS: {small / 1048576:.2f}-{large / 1048576:.2f} MiB BC5"
        if settings.compression == "auto":
            label += f"; RGBA8 fallback {rgba / 1048576:.2f} MiB"
        return label + " (layout estimate; final size after compile)"
