"""Actual game HLSL on a persistent D3D11 device; no CPU lighting approximation."""

import struct
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import reticle_build as rb
import reticle_eval as ev
import preview_atlas
from settings import Settings
from preview_process import PreviewProcess


@dataclass(frozen=True)
class PreviewSettings:
    source: bytes
    text: str
    settings: Settings
    key: tuple


def geometry_key(source, settings):
    import hashlib

    key = (
        hashlib.sha256(source).digest(),
        settings.units,
        settings.scale,
        settings.center,
        tuple(settings.paint_glows),
        settings.focal_plane,
        settings.calibrated_magnification,
        settings.scale_mode,
        settings.legacy_span,
        settings.quality,
        settings.compression,
        settings.max_zoom,
    )

    return key if settings.colour_mode == "legacy" else key + ("material-groups",)


class Renderer:
    def __init__(self):
        self.process = None
        self.process_lock = threading.Lock()
        self.closing = False
        self.folder = tempfile.TemporaryDirectory(prefix="reticle-gpu-")
        self.root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / ".preview"
        self.error_file = open(Path(self.folder.name) / "gpu.log", "w+b")
        self.candidate_key = None
        self.descriptor = None

    def close(self):
        if self.process:
            self.process.close()
            self.process = None
        self.error_file.close()
        self.folder.cleanup()

    def abort(self):
        with self.process_lock:
            self.closing = True
            if self.process:
                self.process.abort()

    def start(self):
        exe = self.root / "reticle_preview.exe"
        if not exe.exists():
            raise ValueError(
                "D3D11 preview host missing. Run build_native.py or use the standalone bundle."
            )
        import hashlib
        import json

        manifest = json.loads((self.root / "shaders/manifest.json").read_text())
        for name, expected in manifest.items():
            if name == "reticle_preview.cpp":
                continue
            actual = hashlib.sha256((self.root / "shaders" / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"Preview shader changed: {name}; rebuild the native preview")
        with self.process_lock:
            if self.closing:
                raise RuntimeError("Preview closed")
            self.process = PreviewProcess(
                [str(exe), str(self.root / "shaders")], self.error_file
            )

    def render(self, source, settings, view, dds=None):
        from reticle_materials import from_source

        settings = replace(
            settings, materials=from_source(source) if settings.colour_mode != "legacy" else ()
        )
        if not dds:
            raise ValueError(
                "Compile the SVG preview first; geometry must be an actual MSDF atlas"
            )
        candidate = Path(self.folder.name) / "candidate.dds"
        info = Path(dds).stat()
        stamp = (str(dds), info.st_mtime_ns, info.st_size)
        key = (
            stamp,
            view.get("restyle", True),
            settings.holo,
            settings.luminance,
            settings.glow_strength,
            settings.glow_size,
            tuple(settings.steps),
            repr(settings.hologram),
            settings.colour_mode,
            settings.switch_states,
            tuple(settings.mark_colours),
        )
        if key == self.candidate_key:
            desc = self.descriptor
        elif view.get("restyle", True):
            desc = preview_atlas.restyle(dds, candidate, settings)
        else:
            import shutil

            shutil.copyfile(dds, candidate)
            desc = ev.Atlas(rb.read_dds(candidate)).desc
        self.candidate_key, self.descriptor = key, desc
        size = view.get("size", 384)
        crop_width, crop_height = (size, size) if isinstance(size, int) else size
        density = view["density"]
        moa_per_unit = 1 if desc.unit == 1 else rb.MOA_PER_MIL
        selected_pattern = (
            desc.states[(view["mark"] - 1) % len(desc.states)].pattern
            if desc.states
            else view["mark"]
        )
        if density == 0:
            reach = max(
                max(abs(t.centre[0]), abs(t.centre[1])) + t.region_half
                for t in desc.tiles
                if t.mark in (0, selected_pattern)
            )
            density = (384 - 96) / max(2 * reach * moa_per_unit, 1)
        density *= view.get("zoom", 1)
        width, height = view.get("frame", (2560, 1440))
        crop_width, crop_height = min(crop_width, width), min(crop_height, height)
        colour = (
            (0, 0, 0)
            if desc.own_colours and view.get("atlas_colours", True)
            else settings.preview_colour
        )
        player = view.get("player", {})
        constants = [
            1 / (density * moa_per_unit),
            float(view["context"] == "scope"),
            0.75 if view["light"] == "day" else 0.04,
            float(view["lit"]),
            view["exposure"],
            view.get("time", 0),
            view.get("eye", 0),
            desc.glow_size,
            *colour,
            float(view["mark"] - 1),
            player.get("holo_level", 1),
            player.get("brightness", 1),
            player.get("speckle", 1),
            player.get("line_weight", 0),
            player.get("tight_weight", 1),
            player.get("mid_weight", 0.35),
            player.get("wide_weight", 0.15),
            player.get("shoulder", 1),
            player.get("tint", 0.5),
            player.get("glow_exposure", 1),
            player.get("halo", 0.55),
            0,
            1 + (player.get("middle_grey", 1) - 1) * player.get("adaptation_amount", 0),
            player.get("adaptation_amount", 0),
            1 + (player.get("game_floor", 1) - 1) * player.get("adaptation_amount", 0),
            1 + (player.get("glow_floor", 0.2) - 1) * player.get("adaptation_amount", 0),
            player.get("tight_sigma", 3),
            player.get("mid_sigma", 12),
            player.get("wide_sigma", 40),
            0,
        ]
        packet = struct.pack(
            "<4I36f",
            width,
            height,
            view.get("mip", 0),
            height,
            *constants,
            *view.get("pan", (0, 0)),
            crop_width,
            crop_height,
        )
        path = str(candidate).encode("utf-8")
        if self.process is None:
            self.start()
        payload = self.process.exchange(
            packet + struct.pack("<I", len(path)) + path, crop_width * crop_height * 4
        )
        frame = np.frombuffer(payload, np.uint8).reshape(crop_height, crop_width, 4)
        image = np.ascontiguousarray(frame[:, :, :3])
        caption = f"Game HLSL / D3D11 | {width}x{height} SDR | {density:.3g} px/MOA | resident mip {view.get('mip', 0)}"
        notes = [
            "Clear glass, fixed angle mapping; scene/player controls specify the shader inputs"
        ]
        notes.append(dict(zoom=view.get("zoom", 1), pan=view.get("pan", (0, 0))))
        return image, caption, notes
