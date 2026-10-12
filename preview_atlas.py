"""Restyle a compiled atlas through the authoritative descriptor/header codec.

Payload blocks are retained byte for byte. Geometry, calibration and layout changes
require compilation; this function only changes authored appearance metadata.
"""

import copy
from pathlib import Path

import numpy as np
import block_compress as bc
import reticle_format as rf
from reticle_dds import layout


def restyle(source, destination, settings):
    data = bytearray(Path(source).read_bytes())
    dds = layout(data)
    compressed, mips = dds.compressed, len(dds.mips)
    desc = None
    encoded_desc = None
    for mip, level in enumerate(dds.mips):
        h, w, offset, size = level.height, level.width, level.offset, level.size
        if compressed:
            bh, bw = -(-h // 4), -(-w // 4)
            if w < rf.HEADER_TEXELS * 4 or h < 4:
                continue
            blocks = np.frombuffer(data, np.uint8, size, offset).reshape(bh, bw, 16)
            # Header texels are exact block endpoints, not interpolated payload.
            grid = np.stack([blocks[..., 0], blocks[..., 8], blocks[..., 1], blocks[..., 9]], 2)
        else:
            grid = np.frombuffer(data, np.uint8, size, offset).reshape(h, w, 4)
        if grid.shape[1] >= rf.HEADER_TEXELS:
            header, original = rf.read_block(grid, mips - mip, 4 if compressed else 1)
            if header:
                if desc is None:
                    desc = copy.deepcopy(original)
                    desc.holo = settings.holo
                    desc.luminance, desc.glow_strength, desc.glow_size = (
                        settings.luminance,
                        settings.glow_strength,
                        settings.glow_size,
                    )
                    desc.steps = tuple(settings.steps)
                    desc.hologram = dict(rf.HOLOGRAM_DEFAULTS, **settings.hologram)
                    if desc.colour_mode != "legacy":
                        from reticle_states import resolve_states

                        desc.colour_mode = settings.colour_mode
                        desc.states = resolve_states(
                            settings, [tile.mark for tile in desc.tiles]
                        )
                        if original.material_values:
                            from reticle_materials import state_values

                            desc.material_values = state_values(desc.states, settings.materials)
                    desc.own_colours = desc.colour_mode == "legacy" and bool(
                        settings.mark_colours
                    )
                    for tile in desc.tiles:
                        if (
                            desc.colour_mode == "legacy"
                            and tile.role == rf.ROLE_ILLUM
                            and settings.mark_colours
                        ):
                            colour = settings.mark_colours[
                                min(max(tile.mark - 1, 0), len(settings.mark_colours) - 1)
                            ]
                            tile.colour = tuple(round(min(max(v, 0), 1) * 255) for v in colour)
                cell = header["cell"] // (4 if compressed else 1)
                rf.write_block(
                    grid,
                    desc,
                    mip,
                    mips,
                    header["usable"],
                    cell,
                    header["version"],
                    4 if compressed else 1,
                )
                # Validate what was encoded, rather than trusting the input values.
                _, checked = rf.read_block(grid, mips - mip, 4 if compressed else 1)
                if encoded_desc is None:
                    encoded_desc = checked
                if compressed:
                    blocks[:cell, :cell] = bc.exact_blocks(grid[:cell, :cell])
    if desc is None:
        raise ValueError("No valid resident MSDF header")
    Path(destination).write_bytes(data)
    return encoded_desc
