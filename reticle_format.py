"""Self-describing MSDF atlas codec, formats 2 through 8.

Every mip whose reserved cell is at least 8 header texels wide carries its own header in row 0 (x = 0..7) and a
copy of the descriptor from row 1 on, laid out in rows of `desc_width` texels. Format 3 is RGBA8 UNORM: a header
texel is a texel, its bytes decode as round(v * 255). Format 4 is BC5 (block_compress.py): a header texel is a
4 x 4 block whose endpoints hold its four bytes exactly, so the header grid is a quarter of the mip in each
direction; the log2 sizes and the reserved cell size below stay in real texels. reticle_msdf.h in the GAMMA patch
reads exactly this layout.

Header texels
  t0 'R','M','S','D'                 t1 'F','A','T','L'
  t2 version, original mip, log2 width, log2 height
  t3 total mips, usable levels from this mip, tile count, descriptor rows
  t4 descriptor width (texels), descriptor texel count low byte, angle unit, flags
  t5 descriptor byte sum (lo, hi), reserved cell size log2, descriptor texel count high byte (v7/8 only)
  t6 0xA5, 0x5A, 0xC3, 0x3C
  t7 header byte sum t0..t6 (lo, hi), 255 - original mip, 0x96

Descriptor bytes: global record (8 bytes in v2, 40 in v3/4, 168 in v5/6, 1144 in v7/8), then 24 bytes per tile.
Formats 7/8 retain RGBA8/BC5 storage and add material appearance, with a 128-header-texel reserved cell:
  bytes 6..7 of each state slot: little-endian hidden-material bitmask; zero keeps all visible.
  byte 42: material count, 1..16.
  bytes 168..183: each tile's zero-based material index, unused entries zero.
  bytes 184..1143: 15 states x 16 materials x RGBA bytes, unused entries zero.
    RGB overrides the original SVG fill when alpha bit 7 is set; otherwise RGB is ignored.
    Alpha bits 0..6 encode illumination brightness / 127; paint uses full brightness.
  State pattern references remain at byte 48. The material table resolves legacy state-wide defaults;
  the renderer must not multiply those defaults again. Old formats keep their original evaluation.
Formats 5/6 retain v3/4 storage respectively and add explicit paint/emission semantics:
  bytes 40..47: state count (1..15), colour source (0 authored, 1 3DSS), six zero bytes.
  bytes 48..167: fifteen 8-byte state slots: pattern, override-colour flag, R, G, B,
                brightness * 255, two zero bytes. Unused slots are zero.
Pattern count stays at byte 7; tile mark bits reference patterns, not switch-state slots.
The reserved cell is at least 64 header texels at mip 0, so the complete table fits resident mip 2.
  global  u16 calibrated magnification * 100, u8 focal plane (0 FFP, 1 SFP), u8 holo,
          u16 reticle radius * 16 (units), u8 tile count, u8 Mark Switch mark count (at least 1),
          format 3 only:
          u16 legacy span * 8 (units across one UV of the bitmap the atlas replaces, as the legacy shaders map
          it; 0 = the true angular mapping), u16 illumination luminance * 16 (0 = the default),
          u8 glow strength * 32, u8 glow size * 32 (scale on the engine's glow lobes, 0 = 1), u8 brightness step
          count (0 = the Mark Switch
          colour's own magnitude sets the brightness), u8 0, u8 x 8 brightness steps * 255,
          hologram: u8 speckle depth * 255, u8 speckle grain * 100 (units), u8 speckle eye shift * 100 (grains
          per mm), u8 animated noise depth * 255, u8 noise speed * 16 (per second), u8 noise grain * 100 (units),
          u8 softness * 32, u8 level * 100, u8 ghost level * 255, s8 ghost offset x, y * 8 (units),
          u8 colour fringe * 100 (units), u8 low-step flicker depth * 255, u8 x 3 0
A format 2 atlas reads as true angular mapping with default illumination, glow and hologram.
  tile    u8 role (1 ink, 2 illum, 3 paint), u8 flags (bit0 paint glows with illumination, bit1 format 4: a
          multi-channel tile, R and G distance in its square and B distance and coverage in the square to its right;
          else one true distance in R and coverage in G; bits 4-7 the Mark Switch mark 1..15 the tile belongs to,
          0 = every mark),
          u8 r, g, b (sRGB; paint albedo, or an illuminated tile's mark colour under the own colours flag),
          u8 last usable original mip,
          u16 u0, v0, size (texture-normalised * 65536),
          s16 centre x, centre y (units * 64), u16 half extent (units * 64),
          u16 valid region half size (units * 64), u16 distance range (tile-normalised * 65536),
          u16 thinnest stroke in mip 0 texels * 256 (0 = unknown)
"""

import struct

MAGIC0 = b"RMSD"
MAGIC1 = b"FATL"
VERSION = 3
VERSION_BC5 = 4  # compressed: tiles hold one signed distance (R) and coverage (G)
SENTINEL = bytes((0xA5, 0x5A, 0xC3, 0x3C))
HEADER_TEXELS = 8
GLOBAL_BYTES_MATERIALS = 1144
VERSION_MATERIALS, VERSION_MATERIALS_BC5 = 7, 8
GLOBAL_BYTES_STATES = 168
VERSION_STATES, VERSION_STATES_BC5 = 5, 6
GLOBAL_BYTES = 40
GLOBAL_BYTES_V2 = 8
MAX_STEPS = 8
DEFAULT_LUMINANCE = 8.0
TILE_BYTES = 24
MAX_TILES = 16

ROLE_INK, ROLE_ILLUM, ROLE_PAINT = 1, 2, 3
ROLES = {"ink": ROLE_INK, "illum": ROLE_ILLUM, "paint": ROLE_PAINT}
UNIT_MOA, UNIT_MRAD = 1, 2
FLAG_ILLUMINATES, FLAG_PAINT, FLAG_HOLO, FLAG_SFP, FLAG_OWN_COLOURS = 1, 2, 4, 8, 16
TILE_PAINT_GLOWS = 1
TILE_MSDF = (
    2  # format 4: R, G distance here, B distance and coverage in the square to the right
)
TILE_MARK_SHIFT = 4
MAX_MARKS = 15


class Tile:
    def __init__(
        self,
        role,
        colour,
        level_max,
        uv0,
        uv_size,
        centre,
        half_extent,
        region_half,
        distance_range,
        flags=0,
        stroke_texels=0.0,
        material=0,
    ):
        self.material = material
        self.role = role
        self.colour = colour
        self.level_max = level_max
        self.uv0 = uv0
        self.uv_size = uv_size
        self.centre = centre
        self.half_extent = half_extent
        self.region_half = region_half
        self.distance_range = distance_range
        self.flags = flags
        self.stroke_texels = stroke_texels

    @property
    def mark(self):
        return self.flags >> TILE_MARK_SHIFT


class Descriptor:
    def __init__(
        self,
        tiles,
        calibrated_magnification,
        focal_plane_sfp,
        holo,
        radius,
        unit=UNIT_MOA,
        marks=1,
        own_colours=False,
        legacy_span=0.0,
        luminance=DEFAULT_LUMINANCE,
        glow_strength=1.0,
        steps=(),
        hologram=None,
        glow_size=1.0,
        colour_mode="legacy",
        states=(),
        material_values=(),
    ):
        self.material_values = tuple(material_values)
        self.colour_mode = colour_mode
        self.states = tuple(states)
        self.tiles = tiles
        self.legacy_span = legacy_span  # units per legacy bitmap UV, 0 = true angles
        self.luminance = (
            luminance  # lit core at the top step, in scene luminance (exposure scaled in game)
        )
        self.glow_strength = glow_strength
        self.glow_size = glow_size  # scale on the engine's glow lobe sizes
        self.steps = tuple(
            steps
        )  # per Mark Switch mark brightness 0..1, empty = the colour's magnitude
        self.hologram = dict(HOLOGRAM_DEFAULTS, **(hologram or {}))
        self.marks = marks
        self.own_colours = own_colours  # illuminated tiles carry their mark's colour
        self.calibrated_magnification = calibrated_magnification
        self.sfp = focal_plane_sfp
        self.holo = holo
        self.radius = radius
        self.unit = unit

    def flags(self):
        f = FLAG_SFP if self.sfp else 0
        if any(t.role == ROLE_ILLUM or t.flags & TILE_PAINT_GLOWS for t in self.tiles):
            f |= FLAG_ILLUMINATES
        if any(t.role == ROLE_PAINT for t in self.tiles):
            f |= FLAG_PAINT
        if self.holo:
            f |= FLAG_HOLO
        if self.own_colours:
            f |= FLAG_OWN_COLOURS
        return f


# a hologram's look (the [holo] keys of the settings); a format 2 atlas draws with these
HOLOGRAM_DEFAULTS = dict(
    speckle=0.3,
    speckle_size=0.35,
    speckle_eye_shift=0.23,
    noise_animated=0.0,
    noise_speed=1.0,
    noise_size=1.0,
    softness=1.25,
    level=0.75,
    ghost_level=0.0,
    ghost_offset=(0.0, 0.0),
    fringe=0.0,
    flicker=0.0,
)


def u8(v, scale):
    i = int(round(v * scale))
    if not 0 <= i <= 0xFF:
        raise ValueError(f"{v} out of u8 range at scale {scale}")
    return i


def s8(v, scale):
    i = int(round(v * scale))
    if not -0x80 <= i <= 0x7F:
        raise ValueError(f"{v} out of s8 range at scale {scale}")
    return i


def q16(v, scale):
    i = int(round(v * scale))
    if not 0 <= i <= 0xFFFF:
        raise ValueError(f"{v} out of u16 range at scale {scale}")
    return i


def s16(v, scale):
    i = int(round(v * scale))
    if not -0x8000 <= i <= 0x7FFF:
        raise ValueError(f"{v} out of s16 range at scale {scale}")
    return i


def encode_descriptor(d):
    if not 1 <= len(d.tiles) <= MAX_TILES:
        raise ValueError("tile count out of range")
    if not 1 <= d.marks <= MAX_MARKS:
        raise ValueError("mark count out of range")
    if len(d.steps) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} brightness steps")
    steps = [int(round(min(max(v, 0.0), 1.0) * 255)) for v in d.steps] + [0] * (
        MAX_STEPS - len(d.steps)
    )
    out = struct.pack(
        "<HBBHBBHHBBBB8B",
        q16(d.calibrated_magnification, 100),
        1 if d.sfp else 0,
        1 if d.holo else 0,
        q16(d.radius, 16),
        len(d.tiles),
        d.marks,
        q16(d.legacy_span, 8),
        q16(d.luminance, 16),
        min(q16(d.glow_strength, 32), 255),
        u8(d.glow_size, 32),
        len(d.steps),
        0,
        *steps,
    )
    h = d.hologram
    out += struct.pack(
        "<BBBBBBBBBbbBBBBB",
        u8(h["speckle"], 255),
        u8(h["speckle_size"], 100),
        u8(h["speckle_eye_shift"], 100),
        u8(h["noise_animated"], 255),
        u8(h["noise_speed"], 16),
        u8(h["noise_size"], 100),
        u8(h["softness"], 32),
        u8(h["level"], 100),
        u8(h["ghost_level"], 255),
        s8(h["ghost_offset"][0], 8),
        s8(h["ghost_offset"][1], 8),
        u8(h["fringe"], 100),
        u8(h["flicker"], 255),
        0,
        0,
        0,
    )
    if d.colour_mode != "legacy":
        if not 1 <= len(d.states) <= 15 or d.colour_mode not in ("authored", "3dss"):
            raise ValueError("Invalid switch-state table")
        out += bytes(
            (
                len(d.states),
                int(d.colour_mode == "3dss"),
                len(d.material_values[0]) if d.material_values else 0,
                0,
                0,
                0,
                0,
                0,
            )
        )
        for index, state in enumerate(d.states):
            hidden = (
                sum(
                    1 << i
                    for i, value in enumerate(d.material_values[index])
                    if not value.visible
                )
                if d.material_values
                else 0
            )
            if not 1 <= state.pattern <= d.marks:
                raise ValueError("State pattern outside atlas pattern range")
            colour = state.colour or (0, 0, 0)
            out += bytes(
                (
                    state.pattern,
                    int(state.colour is not None),
                    *(u8(v, 255) for v in colour),
                    u8(state.brightness, 255),
                    hidden & 255,
                    hidden >> 8,
                )
            )
        out += bytes(8 * (15 - len(d.states)))
    if d.material_values:
        count = len(d.material_values[0])
        if not 1 <= count <= 16 or len(d.material_values) != len(d.states):
            raise ValueError("Invalid material table dimensions")
        if any(not 0 <= t.material < count for t in d.tiles):
            raise ValueError("Tile refers to a missing material")
        out += bytes([t.material for t in d.tiles] + [0] * (16 - len(d.tiles)))
        for row in d.material_values:
            if len(row) != count:
                raise ValueError("Invalid material table row")
            for value in row:
                out += bytes(
                    (
                        *(u8(v, 255) for v in (value.colour or (0, 0, 0))),
                        u8(value.brightness, 127) | (128 if value.colour is not None else 0),
                    )
                )
            out += bytes(4 * (16 - count))
        out += bytes(64 * (15 - len(d.states)))
    for t in d.tiles:
        out += struct.pack(
            "<BB3BBHHHhhHHHH",
            t.role,
            t.flags,
            *t.colour,
            t.level_max,
            q16(t.uv0[0], 65536),
            q16(t.uv0[1], 65536),
            q16(t.uv_size, 65536),
            s16(t.centre[0], 64),
            s16(t.centre[1], 64),
            q16(t.half_extent, 64),
            q16(t.region_half, 64),
            q16(t.distance_range, 65536),
            q16(min(t.stroke_texels, 255.0), 256),
        )
    out += b"\0" * (-len(out) % 4)
    return out


def decode_descriptor(raw, version=VERSION):
    cal, sfp, holo, radius, count, marks = struct.unpack_from("<HBBHBB", raw, 0)
    extra = dict(
        legacy_span=0.0,
        luminance=DEFAULT_LUMINANCE,
        glow_strength=1.0,
        steps=(),
        hologram=None,
        glow_size=1.0,
    )
    if version >= 3:
        span, lum, strength, size, nsteps, _, *steps = struct.unpack_from("<HHBBBB8B", raw, 8)
        extra = dict(
            legacy_span=span / 8,
            luminance=lum / 16 if lum else DEFAULT_LUMINANCE,
            glow_strength=strength / 32,
            glow_size=size / 32 if size else 1.0,
            steps=tuple(v / 255 for v in steps[: min(nsteps, MAX_STEPS)]),
        )
        v = struct.unpack_from("<BBBBBBBBBbbBB", raw, 24)
        extra["hologram"] = dict(
            speckle=v[0] / 255,
            speckle_size=v[1] / 100,
            speckle_eye_shift=v[2] / 100,
            noise_animated=v[3] / 255,
            noise_speed=v[4] / 16,
            noise_size=v[5] / 100,
            softness=v[6] / 32,
            level=v[7] / 100,
            ghost_level=v[8] / 255,
            ghost_offset=(v[9] / 8, v[10] / 8),
            fringe=v[11] / 100,
            flicker=v[12] / 255,
        )
    if version >= VERSION_STATES:
        from reticle_states import SwitchState

        count_states, mode = raw[40:42]
        if (
            not 1 <= count_states <= 15
            or mode > 1
            or any(raw[43:48])
            or (version < VERSION_MATERIALS and raw[42])
        ):
            raise ValueError("Invalid switch-state header")
        states = []
        for i in range(count_states):
            pattern, flags, r, g, b, brightness, pad0, pad1 = raw[48 + 8 * i : 56 + 8 * i]
            if (
                not 1 <= pattern <= marks
                or flags > 1
                or (version < VERSION_MATERIALS and (pad0 or pad1))
            ):
                raise ValueError("Invalid switch-state record")
            states.append(
                SwitchState(
                    pattern, (r / 255, g / 255, b / 255) if flags else None, brightness / 255
                )
            )
        extra.update(colour_mode="3dss" if mode else "authored", states=states)
    if version >= VERSION_MATERIALS:
        from reticle_states import MaterialOverride

        materials = raw[42]
        if not 1 <= materials <= 16:
            raise ValueError("Invalid material count")
        values = []
        for state in range(count_states):
            hidden = int.from_bytes(raw[54 + state * 8 : 56 + state * 8], "little")
            if hidden >> materials:
                raise ValueError("Visibility mask refers to a missing material")
            row = []
            for material in range(materials):
                start = 184 + (state * 16 + material) * 4
                r, g, b, flags = raw[start : start + 4]
                row.append(
                    MaterialOverride(
                        str(material),
                        (r / 255, g / 255, b / 255) if flags & 128 else None,
                        (flags & 127) / 127,
                        not bool(hidden & (1 << material)),
                    )
                )
            values.append(tuple(row))
        extra["material_values"] = tuple(values)
    base = global_bytes(version)
    tiles = []
    for i in range(count):
        v = struct.unpack_from("<BB3BBHHHhhHHHH", raw, base + i * TILE_BYTES)
        tiles.append(
            Tile(
                v[0],
                v[2:5],
                v[5],
                (v[6] / 65536, v[7] / 65536),
                v[8] / 65536,
                (v[9] / 64, v[10] / 64),
                v[11] / 64,
                v[12] / 64,
                v[13] / 65536,
                v[1],
                v[14] / 256,
            )
        )
    if version >= VERSION_MATERIALS:
        for i, tile in enumerate(tiles):
            tile.material = raw[168 + i]
            if tile.material >= materials:
                raise ValueError("Tile refers to a missing material")
    return Descriptor(
        tiles,
        cal / 100,
        bool(sfp),
        bool(holo),
        radius / 16,
        marks=max(1, min(marks, MAX_MARKS)),
        **extra,
    )


def global_bytes(version):
    return (
        GLOBAL_BYTES_MATERIALS
        if version >= VERSION_MATERIALS
        else (
            GLOBAL_BYTES_STATES
            if version >= VERSION_STATES
            else GLOBAL_BYTES if version >= 3 else GLOBAL_BYTES_V2
        )
    )


def header_bytes(
    d,
    desc_raw,
    orig_mip,
    log2w,
    log2h,
    total_mips,
    usable_levels,
    desc_width,
    cell_log2,
    version=VERSION,
):
    texels = len(desc_raw) // 4
    rows = -(-texels // desc_width)
    s = sum(desc_raw)
    t = [
        MAGIC0,
        MAGIC1,
        bytes((version, orig_mip, log2w, log2h)),
        bytes((total_mips, usable_levels, len(d.tiles), rows)),
        bytes((desc_width, texels & 255, d.unit, d.flags())),
        bytes(
            (
                s & 255,
                (s >> 8) & 255,
                cell_log2,
                texels >> 8 if version >= VERSION_MATERIALS else 0,
            )
        ),
        SENTINEL,
    ]
    hs = sum(b"".join(t))
    t.append(bytes((hs & 255, (hs >> 8) & 255, 255 - orig_mip, 0x96)))
    return t


def block_fits(d, cell):
    """cell: the reserved cell in header texels"""
    width = min(cell, 32)
    return cell >= HEADER_TEXELS and 1 + -(-(len(encode_descriptor(d)) // 4) // width) <= cell


def write_block(img, d, orig_mip, total_mips, usable_levels, cell, version=VERSION, texel=1):
    """Writes header + descriptor into the top-left reserved cell of one mip. img: its header texels (H x W x 4
    uint8; for format 4 the block grid, texel = 4 real texels per header texel); cell in header texels.
    """
    desc = encode_descriptor(d)
    width = min(cell, 32)
    rows_needed = 1 + -(-(len(desc) // 4) // width)
    if cell < HEADER_TEXELS or rows_needed > cell:
        raise ValueError(f"descriptor does not fit a {cell} texel cell")
    h, w = img.shape[0] * texel, img.shape[1] * texel
    for i, b in enumerate(
        header_bytes(
            d,
            desc,
            orig_mip,
            w.bit_length() - 1,
            h.bit_length() - 1,
            total_mips,
            usable_levels,
            width,
            (cell * texel).bit_length() - 1,
            version,
        )
    ):
        img[0, i] = list(b)
    for k in range(len(desc) // 4):
        img[1 + k // width, k % width] = list(desc[4 * k : 4 * k + 4])


def read_block(img, levels=None, texel=1):
    """Validates and decodes one resident mip. img: its header texels (format 4: the bytes
    of its blocks, block_compress.header_texels, texel = 4). levels: mips resident from this one (the texture's mip
    count as GetDimensions reports it); None = the full chain the header declares. Returns (header dict,
    Descriptor)."""
    h, w = img.shape[0] * texel, img.shape[1] * texel
    if img.shape[0] < 1 or img.shape[1] < HEADER_TEXELS:
        raise ValueError("mip too small for a header")
    t = [bytes(int(x) for x in img[0, i]) for i in range(HEADER_TEXELS)]
    if t[0] != MAGIC0 or t[1] != MAGIC1:
        return None, None
    hs = sum(b"".join(t[:7]))
    errors = []
    version, orig_mip, lw, lh = t[2]
    total, usable, count, rows = t[3]
    desc_width, texels, unit, flags = t[4]
    if version >= VERSION_MATERIALS:
        texels |= t[5][3] << 8
    if version not in (
        (2, VERSION, VERSION_STATES, VERSION_MATERIALS)
        if texel == 1
        else (VERSION_BC5, VERSION_STATES_BC5, VERSION_MATERIALS_BC5)
    ):
        errors.append("version")
    if (1 << lw) != w or (1 << lh) != h:
        errors.append("dimensions")
    if t[6] != SENTINEL or t[7] != bytes((hs & 255, (hs >> 8) & 255, 255 - orig_mip, 0x96)):
        errors.append("header checksum")
    if (
        not 1 <= count <= MAX_TILES
        or texels != (global_bytes(version) + count * TILE_BYTES) // 4
        or desc_width == 0
    ):
        errors.append("counts")
    if usable == 0 or orig_mip + usable > total:
        errors.append("levels")
    if unit not in (UNIT_MOA, UNIT_MRAD):
        errors.append("unit")
    if levels is None:
        levels = total - orig_mip
    if levels < usable:
        errors.append("resident mips")
    if errors:
        raise ValueError(", ".join(errors))
    if desc_width > img.shape[1] or 1 + (texels - 1) // desc_width >= img.shape[0]:
        raise ValueError("descriptor exceeds mip bounds")
    raw = b"".join(
        bytes(int(x) for x in img[1 + k // desc_width, k % desc_width]) for k in range(texels)
    )
    s = sum(raw)
    if t[5][0] != (s & 255) or t[5][1] != ((s >> 8) & 255):
        raise ValueError("descriptor checksum")
    if raw[6] != count or not 1 <= raw[7] <= MAX_MARKS:
        raise ValueError("descriptor counts")
    d = decode_descriptor(raw, version)
    # These fields live in the bootstrap header, not the descriptor bytes.
    d.unit = unit
    d.own_colours = bool(flags & FLAG_OWN_COLOURS)
    cell = 1 << t[5][2]
    for tile in d.tiles:
        u0, v0, size = tile.uv0[0], tile.uv0[1], tile.uv_size
        wide = 2 if texel == 4 and tile.flags & TILE_MSDF else 1
        if (
            size <= 0
            or u0 + wide * size > 1
            or v0 + size * w / h > 1
            or (u0 < cell / w and v0 < cell / h)
        ):
            raise ValueError("tile bounds")
        if tile.half_extent <= 0 or tile.distance_range <= 0 or tile.region_half <= 0:
            raise ValueError("tile ranges")
        if tile.role not in (ROLE_INK, ROLE_ILLUM, ROLE_PAINT) or tile.mark > d.marks:
            raise ValueError("tile role or mark")
        if not orig_mip <= tile.level_max < total or tile.level_max - orig_mip >= levels:
            raise ValueError("tile levels")
    header = dict(
        orig_mip=orig_mip,
        total=total,
        usable=usable,
        flags=flags,
        unit=unit,
        cell=cell,
        version=version,
    )
    return header, d
