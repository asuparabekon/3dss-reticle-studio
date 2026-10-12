"""Desktop MSDF authoring: SVG drop, appearance controls and verified DDS export."""

import copy
import sys
import traceback
import time
from pathlib import Path


def build_job(source, output, preview_only=False):
    # The frozen executable also serves as its own isolated compiler worker.
    import make_reticle
    import settings

    try:
        report = make_reticle.build_reticle(
            source,
            settings.load(settings.template_path(source)),
            output,
            no_gate=preview_only,
            previews=False,
            progress=lambda message: print(message, flush=True),
        )
        print(report.text(), flush=True)
        return 1 if report.errors else 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in ("--build-job", "--preview-job"):
    # A windowed PyInstaller app has no stdout; QProcess supplies a pipe handle.
    if sys.stdout is None:
        sys.stdout = open(
            Path(sys.argv[3]).parent / "worker.log", "w", encoding="utf-8", buffering=1
        )
        sys.stderr = sys.stdout
    sys.exit(build_job(sys.argv[2], sys.argv[3], sys.argv[1] == "--preview-job"))

from PySide6.QtCore import QTimer, QFileSystemWatcher, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QDialog,
    QProgressBar,
    QGraphicsOpacityEffect,
)

import settings as st
import reticle_format as rf
from authoring_adaptation import Adaptation
from authoring_theme import configure_app
from authoring_states import StateEditor
from reticle_states import resolve_states
from authoring_help import HelpLabel, Disclosure
from authoring_diagnostics import error_summary
from authoring_canvas import PreviewCanvas
from authoring_document import Document
from authoring_jobs import BuiltAtlas, WorkingAtlas, PreviewQueue
from authoring_build import BuildController
from reticle_preview import Renderer, PreviewSettings, geometry_key
from authoring_size import Estimator
from authoring_controls import (
    Slider,
    Choice,
    NumberField,
    PairField,
    ColourField,
    ListField,
    ChecksEditor,
)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.document, self.built = None, None
        self.builds = BuildController(self)
        self.builds.started.connect(self.build_started)
        self.builds.progress.connect(self.build_progress)
        self.builds.finished.connect(self.export_complete)
        self.checks_valid = True
        import tempfile

        self.cache_folder = tempfile.TemporaryDirectory(prefix="reticle-studio-")
        self.working = None
        self.preview_settings = None
        self.syncing = False
        self.frame = None
        self.setWindowTitle("MSDF Reticle Studio")
        self.resize(1240, 860)
        self.setAcceptDrops(True)
        self.preview = PreviewQueue(Renderer(), self)
        self.preview.frame.connect(self.show_frame)
        self.preview.error.connect(self.show_error)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(16)
        self.debounce.timeout.connect(self.request_preview)
        self.animation = QTimer(self)
        self.animation.setInterval(16)
        self.animation.timeout.connect(self.animate_frame)
        self.adaptation = Adaptation()
        self.adaptation_last = time.monotonic()
        self.adaptation_timer = QTimer(self)
        self.adaptation_timer.setInterval(16)
        self.adaptation_timer.timeout.connect(self.adapt_frame)
        self.time = 0.0
        self.animation_last = None
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.svg_changed)
        self.watcher.directoryChanged.connect(self.svg_changed)
        self.svg_reload = QTimer(self)
        self.svg_reload.setSingleShot(True)
        self.svg_reload.setInterval(250)
        self.svg_reload.timeout.connect(self.reload_svg)
        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.setInterval(300)
        self.geometry_timer.timeout.connect(self.ensure_geometry)
        self.pending_svg = None
        self.geometry_failed = None
        self.estimate_key = None
        self.estimator = PreviewQueue(Estimator(), self)
        self.estimator.frame.connect(self.show_size_estimate)
        self.estimator.error.connect(
            lambda error: self.size_label.setText(
                "DDS estimate unavailable: " + error_summary(error)
            )
        )
        self.build_ui()

    def build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        for title, action in (
            ("Open SVG…", self.open_dialog),
            ("Save Project", self.save_settings),
        ):
            button = QPushButton(title)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        self.filename = QLabel("Drop a filled SVG here to begin")
        self.filename.setWordWrap(True)
        toolbar.addWidget(self.filename, 1)
        outer.addLayout(toolbar)
        self.mapping_hint = QLabel()
        self.mapping_hint.setWordWrap(True)
        self.mapping_hint.hide()
        splitter = QSplitter()
        outer.addWidget(splitter, 1)
        left = QWidget()
        layout = QVBoxLayout(left)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        appearance = QWidget()
        form = QFormLayout(appearance)
        self.texture = QLineEdit()
        self.texture.setPlaceholderText("wpn\\path\\original_reticle")
        self.texture.textEdited.connect(lambda v: self.edit("reticle", "texture", v))

        self.holo = QCheckBox("Holographic effects")
        self.holo.toggled.connect(self.toggle_holo)
        form.addRow(self.holo)
        self.sliders = {}
        for key, title, lo, hi, value, log in (
            ("illumination_luminance", "Brightness", 0.5, 64, 8, True),
            ("glow_strength", "Glow strength", 0, 7.9, 1, False),
            ("glow_size", "Glow size", 0.25, 4, 1, True),
        ):
            slider = Slider(lo, hi, value, log)
            slider.changed.connect(lambda v, k=key: self.edit("illumination", k, f"{v:.3g}"))
            self.sliders[key] = slider
            explanations = {
                "illumination_luminance": "How much light the reticle emits. More light makes the core brighter and increases its glow. Each Mark Switch state can reduce this level or turn it off.",
                "glow_strength": "How strongly light spreads around illuminated shapes. Zero removes the halo without turning off the reticle.",
                "glow_size": "How far the halo spreads. This changes the light around the reticle, not the SVG shape or its size.",
            }
            form.addRow(HelpLabel(title, explanations[key]), slider)
        self.holo_group = QGroupBox("Hologram")
        holo_form = QFormLayout(self.holo_group)
        labels = {
            "speckle": "Grain contrast",
            "speckle_size": "Grain size",
            "speckle_eye_shift": "Eye shimmer",
            "noise_animated": "Animated noise",
            "noise_speed": "Noise speed",
            "noise_size": "Noise size",
            "softness": "Edge softness",
            "level": "Hologram level",
            "ghost_level": "Ghost strength",
            "fringe": "Colour fringe",
            "flicker": "Low-step flicker",
        }
        for key, lo, hi in st.HOLO_RANGES:
            slider = Slider(lo, hi, rf.HOLOGRAM_DEFAULTS[key])
            slider.changed.connect(lambda v, k=key: self.edit("holo", k, f"{v:.3g}"))
            self.sliders[key] = slider
            holo_form.addRow(labels[key], slider)
        self.ghost = PairField(-15, 15)
        self.ghost.textEdited.connect(lambda v: self.edit("holo", "ghost_offset", v))
        holo_form.addRow("Ghost offset (units)", self.ghost)
        self.holo_group = Disclosure("Grain and hologram details", self.holo_group)
        form.addRow(self.holo_group)
        self.holo_group.setVisible(self.holo.isChecked())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(appearance)
        self.tabs.addTab(scroll, "Appearance")
        mapping = QWidget()
        mapping_form = QFormLayout(mapping)
        mapping_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.fields = {}
        export_options = QWidget()
        export_form = QFormLayout(export_options)
        export_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        quality_options = QWidget()
        quality_form = QFormLayout(quality_options)
        quality_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        export_hint = QLabel(
            "Choose the texture your mod replaces and an output folder. Export DDS checks the result before writing your mod."
        )
        export_hint.setWordWrap(True)
        export_form.addRow(export_hint)
        export_form.addRow(
            HelpLabel(
                "Game texture path",
                "The original texture this DDS replaces, relative to gamedata/textures and without .dds. The bundled examples already set this. Studio writes a mod folder; it does not install it into your game.",
            ),
            self.texture,
        )
        self.texture.setToolTip(
            "Relative to gamedata/textures. You can leave this blank while designing."
        )
        for key, title, values in (
            ("units", "Drawing units", ["moa", "mil"]),
            ("scale_mode", "Mapping", ["true", "3dss"]),
            ("focal_plane", "Focal plane", ["ffp", "sfp"]),
            ("quality", "Texture quality", ["normal", "high"]),
            ("compression", "Compression", ["auto", "none", "bc5"]),
        ):
            labels = {
                "moa": "MOA",
                "mil": "Milliradians",
                "true": "Angular size",
                "3dss": "Match original 3DSS texture",
                "ffp": "Grows with scope zoom (FFP)",
                "sfp": "Fixed size (SFP)",
                "normal": "Standard",
                "high": "High detail",
                "auto": "Automatic (recommended)",
                "none": "Uncompressed",
                "bc5": "BC5 compression",
            }
            combo = Choice([(labels[v], v) for v in values])
            combo.currentIndexChanged.connect(
                lambda _, k=key, field=combo: self.edit("reticle", k, field.currentData())
            )
            self.fields[key] = combo
            help_text = {
                "units": "Angular units used by the drawing calibration: MOA or milliradians. This is not the preview zoom.",
                "scale_mode": "Match original 3DSS texture preserves the installed sight's mapping. Angular size uses your calibrated dimensions directly. Neither choice edits 3DSS configuration.",
                "focal_plane": "FFP grows with scope zoom. SFP keeps a fixed apparent size and uses a calibration magnification.",
                "quality": "Higher detail may allocate larger tiles. Export checks edge quality in either mode.",
                "compression": "Automatic uses BC5 when the atlas fits and passes checks, otherwise uncompressed RGBA8. Keep Automatic unless you need a specific format.",
            }
            (quality_form if key in ("quality", "compression") else mapping_form).addRow(
                HelpLabel(title, help_text[key]), combo
            )
        for key, title in (
            ("scale", "SVG units per angle unit"),
            ("center", "Centre x, y (SVG units)"),
            ("legacy_span", "Legacy bitmap span"),
            ("max_zoom", "Maximum zoom for texture sizing"),
            ("calibrated_magnification", "SFP calibration magnification"),
        ):
            entry = {
                "scale": lambda: NumberField(0.0001, 100000, 1, "Use SVG calibration"),
                "center": lambda: PairField(automatic=True),
                "legacy_span": lambda: NumberField(1, 8191, 100),
                "max_zoom": lambda: NumberField(0.5, 100, 3, "Automatic"),
                "calibrated_magnification": lambda: NumberField(0.1, 100, 1),
            }[key]()
            entry.textEdited.connect(lambda v, k=key: self.edit("reticle", k, v))
            self.fields[key] = entry
            mapping_form.addRow(title, entry)
        self.fields["legacy_span"].setToolTip(
            "Angular width of the original bitmap. Used only for matching 3DSS size."
        )
        colours = QWidget()
        colour_form = QFormLayout(colours)
        material_hint = QLabel(
            "Paint stays visible when illumination is off. Illum layers emit light; their SVG fills set the authored colours. Existing ink layers mean black paint."
        )
        material_hint.setWordWrap(True)
        colour_form.addRow(material_hint)
        self.colour_mode = Choice(
            [
                ("This reticle", "authored"),
                ("Game settings (3DSS)", "3dss"),
                ("Legacy project behaviour", "legacy"),
            ]
        )
        self.colour_mode.currentIndexChanged.connect(self.change_colour_mode)
        mapping_form.addRow(
            HelpLabel(
                "Illumination source",
                "This reticle: SVG colours or your state overrides control the light. Game settings: 3DSS supplies colour and brightness; black means off. Both use Mark Switch to select a state. Existing legacy projects retain their original behaviour until you choose a new mode.",
            ),
            self.colour_mode,
        )
        self.compatibility = QLabel(
            "Compatibility mode: this project keeps its original colouring. Choose an illumination source above to use the new workflow."
        )
        self.compatibility.setWordWrap(True)
        mapping_form.addRow(self.compatibility)
        self.state_editor = StateEditor()
        self.state_editor.changed.connect(self.change_states)
        state_content = QWidget()
        state_layout = QVBoxLayout(state_content)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.addWidget(
            HelpLabel(
                "Mark Switch states",
                "Each press of Mark Switch selects a state. States can reuse one pattern with different colours or brightness. Brightness 0 turns the light off; paint remains. Your sight configuration must already provide enough selections; Studio does not edit it.",
            )
        )
        state_layout.addWidget(self.state_editor)
        self.state_section = state_content
        form.addRow(self.state_section)
        self.colour = ColourField()
        self.colour.textEdited.connect(lambda v: self.edit("illumination", "preview", v))
        colour_form.addRow(
            HelpLabel(
                "Simulated game colour",
                "Preview input for Game settings or legacy mode. It represents the colour supplied by 3DSS; it does not change the exported SVG light colours.",
            ),
            self.colour,
        )
        self.atlas_colours = QCheckBox("Preview the colours saved in this reticle")
        self.atlas_colours.setChecked(True)
        self.atlas_colours.toggled.connect(self.schedule_preview)
        colour_form.addRow(self.atlas_colours)
        self.legacy_colour_rows = []
        for key, title in (
            ("mark_colours", "Colours for each mark"),
            ("brightness_steps", "Brightness steps"),
            ("paint_glows", "Illuminated paint colours"),
        ):
            entry = ListField(
                {
                    "mark_colours": "colours",
                    "brightness_steps": "steps",
                    "paint_glows": "paint",
                }[key],
                8 if key == "brightness_steps" else 15,
            )
            entry.textEdited.connect(lambda v, k=key: self.edit("illumination", k, v))
            self.fields[key] = entry
            label = QLabel(title)
            self.legacy_colour_rows.extend([label, entry])
            colour_form.addRow(label, entry)
        note = QLabel(
            "SVG-only drawings start at 1 SVG unit per MOA unless a calibration layer defines the size.\nAdjust size here; no settings file is required. Keep Automatic selected to centre the drawing."
        )
        note.setWordWrap(True)
        mapping_form.addRow(note)

        def add_page(widget, title):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            self.tabs.addTab(scroll, title)

        mapping_form.addRow(self.mapping_hint)
        self.mapping_hint.show()
        mapping_form.addRow(Disclosure("Compatibility and colour simulation", colours))
        add_page(mapping, "Advanced")
        self.checks = ChecksEditor()
        self.checks.changed.connect(self.edit_checks)
        self.checks_toggle = QPushButton("Dimension checks (optional)")
        self.checks_toggle.setCheckable(True)
        self.checks_toggle.toggled.connect(self.checks.setVisible)
        quality_form.addRow(self.checks_toggle)
        quality_form.addRow(self.checks)
        self.checks.hide()
        mapping_form.addRow(Disclosure("Texture quality and validation", quality_options))
        add_page(export_options, "Export")
        scene_tab = QWidget()
        scene_form = QFormLayout(scene_tab)
        scene_form.addRow(
            QLabel("Preview inputs only — these values are not exported into the reticle.")
        )
        self.frame_size = QComboBox()
        self.frame_size.addItem("2560 × 1440 SDR", (2560, 1440))
        self.frame_size.addItem("1920 × 1080 SDR", (1920, 1080))
        self.frame_size.currentIndexChanged.connect(self.schedule_preview)
        scene_form.addRow("Simulated game frame", self.frame_size)
        self.resident_mip = QComboBox()
        self.resident_mip.addItems(["0 — full texture", "1 — medium texture", "2 — low texture"])
        self.resident_mip.currentIndexChanged.connect(self.schedule_preview)
        scene_form.addRow("Resident mip", self.resident_mip)
        self.player_fields = {}
        for key, title, default, lo, hi in (
            ("brightness", "Player brightness", 1, 0, 8),
            ("holo_level", "Player holo level", 1, 0, 8),
            ("speckle", "Player grain multiplier", 1, 0, 4),
            ("line_weight", "Player thin-line weight", 0, 0, 1),
            ("tight_sigma", "Tight sigma at 1080p", 3, 0.1, 100),
            ("mid_sigma", "Mid sigma at 1080p", 12, 0.1, 100),
            ("wide_sigma", "Wide sigma at 1080p", 40, 0.1, 200),
            ("tight_weight", "Tight weight", 1, 0, 4),
            ("mid_weight", "Mid weight", 0.35, 0, 4),
            ("wide_weight", "Wide weight", 0.15, 0, 4),
            ("shoulder", "White core shoulder", 1, 0, 8),
            ("tint", "Glow tint", 0.5, 0, 1),
            ("halo", "Halo ceiling", 0.55, 0.001, 1),
            ("glow_exposure", "Glow exposure power", 1, 0, 4),
            ("middle_grey", "Tone-map middle grey", 1.3, 0.01, 8),
            ("adaptation_amount", "Tone-map adaptation amount", 1, 0, 1),
            ("game_floor", "Game exposure floor", 0.55, 0.001, 8),
            ("glow_floor", "Glow exposure floor", 0.2, 0.001, 8),
        ):
            field = QDoubleSpinBox()
            field.setRange(lo, hi)
            field.setDecimals(3)
            field.setValue(default)
            field.valueChanged.connect(self.schedule_preview)
            self.player_fields[key] = field
            scene_form.addRow(title, field)
        self.scene_luminance = QDoubleSpinBox()
        self.scene_luminance.setRange(0, 1000)
        self.scene_luminance.setDecimals(4)
        self.scene_luminance.setValue(1.616667)
        scene_form.addRow("Scene average luminance", self.scene_luminance)
        self.adaptation_speed = QDoubleSpinBox()
        self.adaptation_speed.setRange(0.01, 10)
        self.adaptation_speed.setValue(6.5)
        scene_form.addRow("Adaptation speed", self.adaptation_speed)
        self.adaptation_fps = QDoubleSpinBox()
        self.adaptation_fps.setRange(15, 240)
        self.adaptation_fps.setValue(60)
        scene_form.addRow("Simulated game FPS", self.adaptation_fps)
        import_settings = QPushButton("Import game user.ltx...")
        import_settings.clicked.connect(self.import_game_settings)
        scene_form.addRow(import_settings)
        note = QLabel(
            "Day/Night are synthetic luminance samples. Import your game settings and match scene luminance for comparisons; live-scene pixel parity is not verified."
        )
        note.setWordWrap(True)
        scene_form.addRow(note)
        scene_scroll = QScrollArea()
        scene_scroll.setWidgetResizable(True)
        scene_scroll.setWidget(scene_tab)
        self.scene_dialog = QDialog(self)
        self.scene_dialog.setWindowTitle("Preview environment and player settings")
        self.scene_dialog.resize(520, 700)
        QVBoxLayout(self.scene_dialog).addWidget(scene_scroll)
        self.tabs.setMinimumWidth(390)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Current design", "Last exported DDS"])
        self.mode.currentIndexChanged.connect(self.schedule_preview)
        self.mark = QComboBox()
        self.mark.addItem("Mark 1", 1)
        self.context = QComboBox()
        self.context.addItem("Reflex / holo", "reflex")
        self.context.addItem("Scope", "scope")
        for widget in (self.mark, self.context):
            widget.currentIndexChanged.connect(self.schedule_preview)
            controls.addWidget(widget)
        right_layout.addLayout(controls)
        controls = QHBoxLayout()
        self.light = QComboBox()
        self.light.addItems(["Day", "Night"])
        self.light.currentIndexChanged.connect(self.change_light)
        self.density = QComboBox()
        for title, value in (
            ("Fit geometry", 0),
            ("0.47 px/MOA", 0.47),
            ("1.41 px/MOA", 1.41),
            ("4.7 px/MOA", 4.7),
            ("10 px/MOA", 10),
        ):
            self.density.addItem(title, value)
        self.density.currentIndexChanged.connect(self.schedule_preview)
        self.lit = QCheckBox("Light on")
        self.lit.setToolTip(
            "Preview only: untick to inspect the surface with emitted light off. A Mark Switch state with brightness 0 stays off either way."
        )
        self.lit.setChecked(True)
        self.lit.toggled.connect(self.schedule_preview)
        for widget in (self.light, self.lit):
            controls.addWidget(widget)
        right_layout.addLayout(controls)
        self.canvas = PreviewCanvas(
            "Drop an SVG to start\n\nChoose the light colour and glow, then export your DDS."
        )
        right_layout.addWidget(self.canvas, 1)
        self.caption = QLabel(
            "Actual game reticle shaders; fixed clear glass and SDR scene inputs."
        )
        self.caption.setWordWrap(True)
        hint = QLabel("Scroll to zoom  |  Drag to pan  |  Double-click to fit")
        hint.setObjectName("hint")
        right_layout.addWidget(hint)
        right_layout.addWidget(self.caption)
        view_form = QFormLayout()
        self.exposure = Slider(0.0001, 64, 0.6, True)
        self.exposure.changed.connect(self.schedule_preview)
        self.auto_exposure = QCheckBox("Eye adaptation")
        self.auto_exposure.setChecked(True)
        self.auto_exposure.toggled.connect(self.set_auto_exposure)
        self.exposure.setEnabled(False)
        self.eye = Slider(-5, 5, 0)
        self.eye.changed.connect(self.schedule_preview)
        self.inspect = QComboBox()
        for label, value in (("100%", 1), ("200%", 2), ("400%", 4), ("800%", 8)):
            self.inspect.addItem(label, value)
        self.inspect.setEditable(True)
        self.inspect.lineEdit().setReadOnly(True)
        self.inspect.setToolTip(
            "Preview magnification: re-renders MSDF edges at the new zoom. Does not edit exported reticle size."
        )
        self.inspect.activated.connect(lambda i: self.canvas.set_zoom(self.inspect.itemData(i)))
        self.canvas.viewChanged.connect(self.schedule_preview)
        self.canvas.zoomChanged.connect(lambda z: self.inspect.setEditText(f"{z*100:.0f}%"))
        self.animate = QCheckBox("Animate noise / flicker")
        self.animate.toggled.connect(self.toggle_animation)
        self.animate.setVisible(self.holo.isChecked())
        row = QHBoxLayout()
        fit_button = QPushButton("Fit")
        fit_button.setFixedWidth(48)
        fit_button.clicked.connect(self.canvas.fit)
        pixel_button = QPushButton("1:1")
        pixel_button.setFixedWidth(48)
        self.inspect.setFixedWidth(86)
        pixel_button.clicked.connect(self.canvas.actual_pixels)
        row.addWidget(fit_button)
        row.addWidget(pixel_button)
        row.addWidget(self.inspect)
        row.addWidget(self.animate)
        scene_button = QPushButton("Scene settings")
        scene_button.clicked.connect(self.scene_dialog.show)
        scene_form.addRow(
            HelpLabel(
                "Scene simulation",
                "These are synthetic day/night inputs using the game exposure equations. They are not a capture of your live scene. Import user.ltx to match player settings.",
            )
        )
        view_form.addRow(row)
        preview_options = QWidget()
        options_form = QFormLayout(preview_options)
        options_form.addRow("Inspect", self.mode)
        self.mode.show()
        options_form.addRow("Sampling density", self.density)
        options_form.addRow(self.auto_exposure)
        options_form.addRow("Current exposure", self.exposure)
        options_form.addRow("Eye shift (mm)", self.eye)
        options_form.addRow(scene_button)
        right_layout.addLayout(view_form)
        right_layout.addWidget(Disclosure("Preview options", preview_options))
        splitter.addWidget(right)
        splitter.setSizes([480, 650])
        export_row = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("Export folder (mod package, not the live game)")
        choose = QPushButton("Folder…")
        choose.clicked.connect(self.choose_folder)
        self.build = QPushButton("Export DDS")
        self.build.clicked.connect(self.start_export)
        self.build.setEnabled(False)
        self.build.setObjectName("primary")
        self.build.setToolTip(
            "Open an SVG to begin. Export checks the DDS before writing a mod folder."
        )
        export_row.addWidget(self.build)
        self.compile_preview = QPushButton("Rebuild preview")
        self.compile_preview.clicked.connect(lambda: self.start_export(preview_only=True))
        export_form.addRow(Disclosure("Troubleshooting", self.compile_preview))
        self.cancel = QPushButton("Cancel build")
        self.cancel.clicked.connect(self.cancel_export)
        self.cancel.setEnabled(False)
        self.cancel.hide()
        export_row.addWidget(self.cancel)
        export_form.addRow("Output folder", self.destination)
        export_form.addRow(choose)
        outer.addLayout(export_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)
        self.size_label = QLabel("File size estimate appears after opening an SVG")
        self.size_label.setWordWrap(True)
        outer.addWidget(self.size_label)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumBlockCount(2000)
        self.diagnostics.setMaximumHeight(100)
        self.diagnostics.hide()
        details = self.details = QPushButton("Build details")
        details.setCheckable(True)
        details.toggled.connect(self.diagnostics.setVisible)
        outer.addWidget(details)
        outer.addWidget(self.diagnostics)
        self.setCentralWidget(root)
        available = self.screen().availableGeometry()
        self.resize(
            min(1240, int(available.width() * 0.9)), min(860, int(available.height() * 0.9))
        )
        self.adaptation_timer.start()
        self.panel_effect = QGraphicsOpacityEffect(self.tabs)
        self.panel_effect.setOpacity(1)
        self.tabs.setGraphicsEffect(self.panel_effect)
        self.panel_animation = QPropertyAnimation(self.panel_effect, b"opacity", self)
        self.panel_animation.setDuration(160)
        self.panel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.tabs.currentChanged.connect(self.animate_panel)
        view_menu = self.menuBar().addMenu("View")
        reduce_motion = view_menu.addAction("Reduce motion")
        reduce_motion.setCheckable(True)
        reduce_motion.toggled.connect(self.set_reduced_motion)
        open_action = QAction("Open SVG", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_dialog)
        self.addAction(open_action)
        save_action = QAction("Save Project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_settings)
        self.addAction(save_action)
        save_as = QAction("Save Project As...", self)
        save_as.setShortcut("Ctrl+Shift+S")
        save_as.triggered.connect(lambda: self.save_settings(True))
        self.addAction(save_as)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(open_action)
        file_menu.addAction("Open Project...", self.open_project_dialog)
        file_menu.addAction(save_action)
        file_menu.addAction(save_as)
        file_menu.addAction("Reload SVG", self.reload_svg)
        examples = file_menu.addMenu("Examples")
        example_folder = (
            Path(sys.executable).parent / "examples"
            if getattr(sys, "frozen", False)
            else Path(__file__).parent / "reticles"
        )
        for name in ("Cortex", "EOTech", "material_states"):
            path = example_folder / (name + ".svg")
            action = examples.addAction(name.replace("_", " "))
            action.setEnabled(path.exists())
            action.triggered.connect(lambda checked=False, p=path: self.open_path(p))
        self.mark.currentIndexChanged.connect(self.sync_state_selection)
        self.state_editor.selected.connect(self.mark.setCurrentIndex)

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open reticle drawing",
            "",
            "Drawings and projects (*.svg *.reticle);;SVG drawings (*.svg);;Reticle projects (*.reticle)",
        )
        if path:
            self.open_path(path)

    def open_path(self, path):
        if str(path).lower().endswith(".reticle"):
            try:
                import tempfile

                folder = tempfile.mkdtemp(prefix="project-", dir=self.cache_folder.name)
                document = Document.load_project(path, folder)
                self.open_svg(document.path, document=document)
            except Exception as e:
                self.show_error(str(e))
        else:
            self.open_svg(path)

    def open_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open reticle project", "", "Reticle project (*.reticle)"
        )
        if path:
            self.open_path(path)

    def can_replace(self):
        if not self.document or not self.document.dirty:
            return True
        result = QMessageBox.question(
            self,
            "Unsaved settings",
            "Save this reticle project before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Save:
            return self.save_settings()
        return result == QMessageBox.StandardButton.Discard

    def open_svg(self, path, confirmed=False, document=None):
        if self.builds.job and not self.builds.job.preview_only:
            return
        if not confirmed and not self.can_replace():
            return
        if self.builds.job:
            self.pending_svg = (path, document)
            self.builds.cancel()
            return
        try:
            candidate_document = document or Document(path)
            s = candidate_document.settings(preview=True)
            self.preview.invalidate()
            self.estimator.invalidate()
            if self.watcher.files():
                self.watcher.removePaths(self.watcher.files())
            if self.watcher.directories():
                self.watcher.removePaths(self.watcher.directories())
            self.document = candidate_document
            self.diagnostics.clear()
            self.details.setChecked(False)
            self.canvas.clear()
            self.canvas.fit()
            self.frame = None
            self.watcher.addPaths([str(self.document.path), str(self.document.path.parent)])
            self.geometry_failed = None
            self.estimate_key = None
            self.built = None
            self.working = None
            self.mode.setCurrentIndex(0)
            self.destination.setText(
                str(self.document.path.with_name(self.document.path.stem + "_reticle"))
            )
            self.sync_controls()
            example_root = (
                Path(sys._MEIPASS) / "examples/gamedata/textures/wpn"
                if getattr(sys, "frozen", False)
                else Path(__file__).parent / "native/example_textures"
            )
            candidates = (
                example_root / (self.document.path.stem + ".dds"),
                example_root / (s.texture.removeprefix("wpn\\").replace("\\", "/") + ".dds"),
            )
            for candidate in candidates:
                if candidate.exists():
                    self.working = WorkingAtlas(candidate, geometry_key(self.document.svg, s))
                    break
            # Existing DDS is immediately inspectable, but only a recorded source match
            # can stand in for compilation of the drawing that was just opened.
            if self.working and not self.matches_example(s):
                self.working = WorkingAtlas(self.working.path, None)
            self.build.setEnabled(True)
            self.build.setToolTip("Export a checked DDS into the output folder selected in Export.")
            self.schedule_preview()
        except Exception as e:
            self.show_error(str(e))

    def matches_example(self, settings):
        import json

        manifest = (
            Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / ".preview/shaders/examples.json"
        )
        if not manifest.exists():
            return False
        import hashlib

        key = repr(geometry_key(self.document.svg, settings))
        expected = json.loads(manifest.read_text()).get(key)
        return (
            expected
            and hashlib.sha256(Path(self.working.path).read_bytes()).hexdigest() == expected
        )

    def reload_svg(self):
        if self.document:
            try:
                source = self.document.path.read_bytes()
                if str(self.document.path) not in self.watcher.files():
                    self.watcher.addPath(str(self.document.path))
                if source == self.document.svg:
                    return
                self.preview.invalidate()
                self.document.svg = source
                self.update_title()
                self.diagnostics.clear()
                self.details.setChecked(False)
                self.geometry_failed = None
                self.sync_controls()
                self.mode.setCurrentIndex(0)
                self.schedule_preview()
            except Exception as e:
                self.show_error(str(e))

    def sync_controls(self):
        if not self.document:
            return
        try:
            s = self.document.settings(preview=True)
            import configparser

            cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
            cp.read_string(self.document.text)
            self.mapping_hint.setText(
                f"Size: 1 {s.units.upper()} = {s.scale:g} SVG units. Adjust in Advanced."
                if s.scale
                else "Size comes from the SVG calibration layer. Adjust in Advanced."
            )
            self.syncing = True
            self.texture.setText(cp.get("reticle", "texture", fallback=""))
            self.holo.setChecked(s.holo)
            self.holo_group.setVisible(s.holo)
            for key, slider in self.sliders.items():
                value = {
                    "illumination_luminance": s.luminance,
                    "glow_strength": s.glow_strength,
                    "glow_size": s.glow_size,
                }.get(key)
                if value is None:
                    value = s.hologram.get(key, rf.HOLOGRAM_DEFAULTS[key])
                slider.set_value(value)
            self.ghost.setText(cp.get("holo", "ghost_offset", fallback="0, 0"))
            for key, field in self.fields.items():
                section = (
                    "illumination"
                    if key in ("mark_colours", "brightness_steps", "paint_glows")
                    else "reticle"
                )
                value = cp.get(section, key, fallback=str(getattr(s, key, "") or ""))
                if isinstance(field, QComboBox):
                    field.setCurrentText(value)
                else:
                    field.setText(value)
            self.colour.setCurrentText(cp.get("illumination", "preview", fallback="scope_red2"))
            self.checks_valid = True
            self.checks.set_values(s.checks)
            self.checks_toggle.setText(f"Dimension checks ({len(s.checks)} active)")
            from svg_reticle import read_reticle_svg

            drawing = read_reticle_svg(self.document.svg)
            self.fields["scale"].auto.setEnabled(bool(drawing.calibration))
            self.fields["scale"].auto.setToolTip(
                "Uses a calibrate layer in the SVG"
                if drawing.calibration
                else "This SVG has no calibration layer; set its size using the number field."
            )
            self.fields["legacy_span"].setEnabled(s.scale_mode == "3dss")
            self.fields["calibrated_magnification"].setEnabled(s.focal_plane == "sfp")
            self.colour_mode.setCurrentText(s.colour_mode)
            self.patterns = sorted({shape.mark for shape in drawing.shapes} - {0}) or [1]
            states = s.switch_states or resolve_states(s, self.patterns)
            from reticle_materials import discover

            self.state_editor.set_states(
                states,
                self.patterns,
                s.colour_mode == "authored",
                discover(drawing) if s.colour_mode != "legacy" else (),
            )
            self.state_section.setVisible(s.colour_mode != "legacy")
            self.compatibility.setVisible(s.colour_mode == "legacy")
            legacy_index = self.colour_mode.findData("legacy")
            self.colour_mode.view().setRowHidden(legacy_index, s.colour_mode != "legacy")
            self.animate.setVisible(s.holo)
            for widget in self.legacy_colour_rows:
                widget.setVisible(s.colour_mode == "legacy")
            self.atlas_colours.setVisible(s.colour_mode == "legacy")
            previous = self.mark.currentData()
            self.mark.clear()
            count = (
                max([shape.mark for shape in drawing.shapes] + [1])
                if s.colour_mode == "legacy"
                else len(states)
            )
            for mark in range(1, count + 1):
                label = (
                    f"Mark {mark}"
                    if s.colour_mode == "legacy"
                    else self.state_label(mark, states[mark - 1])
                )
                self.mark.addItem(label, mark)
            self.mark.setCurrentIndex(min(max((previous or 1) - 1, 0), count - 1))
            self.context.setCurrentIndex(0 if s.holo else 1)
            self.sync_state_selection()
            self.update_title()
        except Exception as e:
            self.show_error(str(e))
        finally:
            self.syncing = False

    def state_label(self, number, state):
        return f"State {number} · Pattern {state.pattern}"

    def sync_state_selection(self, *args):
        index = self.mark.currentIndex()
        if index >= 0 and index != self.state_editor.selection.currentIndex():
            self.state_editor.select(index)

    def change_colour_mode(self):
        if self.syncing or not self.document:
            return
        self.edit("illumination", "colour_mode", self.colour_mode.currentData())
        self.sync_controls()

    def change_states(self, text):
        if self.syncing or not self.document:
            return
        self.edit("illumination", "switch_states", text)
        previous = self.state_editor.selection.currentIndex()
        self.mark.blockSignals(True)
        self.mark.clear()
        for i, state in enumerate(self.state_editor.states(), 1):
            self.mark.addItem(self.state_label(i, state), i)
        self.mark.setCurrentIndex(min(max(previous, 0), self.mark.count() - 1))
        self.mark.blockSignals(False)
        self.sync_state_selection()

    def edit(self, section, key, value):
        if self.syncing or not self.document:
            return
        self.document.update(section, key, value)
        if section == "reticle" and key == "scale_mode":
            self.fields["legacy_span"].setEnabled(value == "3dss")
            if value == "3dss":
                self.document.update("reticle", "legacy_span", self.fields["legacy_span"].text())
        if section == "reticle" and key == "focal_plane":
            self.fields["calibrated_magnification"].setEnabled(value == "sfp")
        self.update_title()
        if section == "reticle" and key in ("scale", "units"):
            try:
                settings = self.document.settings(preview=True)
                self.mapping_hint.setText(
                    f"Size: 1 {settings.units.upper()} = {settings.scale:g} SVG units. Adjust in Advanced."
                    if settings.scale
                    else "Size uses the SVG calibration layer when scale is blank."
                )
            except (ValueError, st.configparser.Error) as error:
                self.mapping_hint.setText(error_summary(str(error)))
        self.schedule_preview()

    def toggle_holo(self, enabled):
        self.holo_group.setVisible(enabled)
        self.animate.setVisible(enabled)
        if not enabled:
            self.animate.setChecked(False)
            self.animation.stop()
        if not self.syncing:
            self.context.setCurrentIndex(0 if enabled else 1)
        self.edit("reticle", "holo", "yes" if enabled else "no")

    def edit_checks(self, checks):
        if self.syncing or not self.document:
            return
        names = [name for name, _ in checks]
        if any(not name.strip() for name in names) or len(set(names)) != len(names):
            self.checks_valid = False
            self.show_error("Give each dimension check a unique name.")
            return
        self.checks_valid = True
        import re

        lines = self.document.text.splitlines(keepends=True)
        section = ""
        kept = []
        for line in lines:
            heading = re.match(r"\s*\[([^]]+)\]", line)
            if heading:
                section = heading[1].lower()
            if section == "checks" and "=" in line and not line.lstrip().startswith(";"):
                continue
            kept.append(line)
        self.document.text = "".join(kept)
        for name, values in checks:
            self.document.update("checks", name, ", ".join(values))
        self.checks_toggle.setText(f"Dimension checks ({len(checks)} active)")
        self.update_title()

    def update_title(self):
        if self.document:
            self.filename.setText(
                self.document.path.name + (" • unsaved settings" if self.document.dirty else "")
            )
            self.setWindowTitle(self.document.path.name + " — MSDF Reticle Studio")

    def save_settings(self, save_as=False):
        if not self.document:
            return False
        try:
            if not self.checks_valid:
                raise ValueError("Fix the dimension check names in Advanced before saving.")
            path = None if save_as else self.document.project_path
            if path is None:
                chosen, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save reticle project",
                    str(self.document.path.with_suffix(".reticle")),
                    "Reticle project (*.reticle)",
                )
                if not chosen:
                    return False
                path = Path(chosen).with_suffix(".reticle")
            self.document.save_project(path)
            self.update_title()
            self.statusBar().showMessage(f"Project saved: {path}")
            return True
        except Exception as e:
            self.show_error(str(e))
            return False

    def svg_changed(self, *args):
        self.svg_reload.start()

    def schedule_preview(self, *args):
        if not self.syncing:
            # Throttle without restarting: dragging must keep producing frames.
            if not self.debounce.isActive():
                self.debounce.start()

    def toggle_animation(self, enabled):
        self.animation_last = time.monotonic() if enabled else None
        self.animation.start() if enabled else self.animation.stop()

    def show_size_estimate(self, text):
        self.size_label.setText(text)

    def ensure_geometry(self):
        if not self.document:
            return
        try:
            key = geometry_key(self.document.svg, self.document.settings(preview=True))
            if self.working and self.working.key == key or self.geometry_failed == key:
                return
            if self.builds.job:
                # Retire only an obsolete temporary compiler; final exports keep their snapshot.
                if (
                    self.builds.job.preview_only
                    and key != self.builds.requested_geometry
                    and not self.builds.job.cancelled
                ):
                    self.builds.cancel()
                self.geometry_timer.start()
                return
            self.start_export(preview_only=True)
        except Exception as e:
            self.show_error(str(e))

    def change_light(self):
        self.scene_luminance.setValue(1.616667 if self.light.currentIndex() == 0 else 0.02)
        if not self.auto_exposure.isChecked():
            self.exposure.set_value(0.6 if self.light.currentIndex() == 0 else 8)
        self.schedule_preview()

    def set_auto_exposure(self, enabled):
        self.exposure.setEnabled(not enabled)
        self.adaptation_last = time.monotonic()
        self.adaptation.remainder = 0
        if enabled:
            self.adaptation.exposure = self.exposure.number.value()

    def adapt_frame(self):
        now = time.monotonic()
        elapsed = now - self.adaptation_last
        self.adaptation_last = now
        if not self.auto_exposure.isChecked() or not self.document:
            return
        value = self.adaptation.advance(
            elapsed,
            self.scene_luminance.value(),
            self.adaptation_speed.value(),
            self.player_fields["middle_grey"].value(),
            self.player_fields["adaptation_amount"].value(),
            self.player_fields["game_floor"].value(),
            self.adaptation_fps.value(),
        )
        if abs(value - self.exposure.number.value()) >= 0.0005:
            self.exposure.set_value(value)
            self.schedule_preview()

    def import_game_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import game exposure and glow settings", "", "Game settings (*.ltx)"
        )
        if not path:
            return
        try:
            from authoring_game_settings import import_settings

            values = import_settings(Path(path))
            for key, value in values.items():
                if key == "adaptation_speed":
                    self.adaptation_speed.setValue(value)
                elif key in self.player_fields:
                    self.player_fields[key].setValue(value)
            self.statusBar().showMessage(
                "Imported game exposure/glow settings; scene luminance remains a preview input"
            )
        except Exception as error:
            self.show_error(str(error))

    def animate_frame(self):
        now = time.monotonic()
        if self.animation_last is not None:
            self.time += now - self.animation_last
        self.animation_last = now
        if self.preview.active is None and not self.debounce.isActive():
            self.request_preview()

    def request_preview(self):
        if self.canvas.drag is not None:
            return
        if not self.document:
            return
        try:
            source = self.document.svg
            if (
                self.preview_settings is None
                or self.preview_settings.source != source
                or self.preview_settings.text != self.document.text
            ):
                settings = self.document.settings(preview=True)
                self.preview_settings = PreviewSettings(
                    source, self.document.text, settings, geometry_key(source, settings)
                )
            settings, key = self.preview_settings.settings, self.preview_settings.key
            if key != self.estimate_key:
                self.estimate_key = key
                self.estimator.request(dict(source=source, settings=copy.deepcopy(settings)))
            dds = None
            if self.mode.currentIndex() == 1:
                if not self.built:
                    raise ValueError("Build and verify a DDS first")
                source = self.built.snapshot.source
                settings = self.built.snapshot.settings
                dds = self.built.path
            else:
                if not self.working or self.working.key != key:
                    if self.geometry_failed == key:
                        return
                    self.preview.invalidate()
                    self.caption.setText(
                        "Preparing your preview... Detailed drawings may take a few minutes on the first build."
                    )
                    self.canvas.setText(
                        "Building preview...\nDetailed SVGs can take a few minutes on the first build."
                    )
                    self.frame = None
                    if not self.geometry_timer.isActive():
                        self.geometry_timer.start()
                    return
                dds = self.working.path
            view = dict(
                mark=self.mark.currentData() or 1,
                size=(
                    max(16, round(self.canvas.width() * self.canvas.devicePixelRatioF())),
                    max(16, round(self.canvas.height() * self.canvas.devicePixelRatioF())),
                ),
                zoom=self.canvas.zoom,
                pan=(
                    self.canvas.offset.x() * self.canvas.devicePixelRatioF(),
                    self.canvas.offset.y() * self.canvas.devicePixelRatioF(),
                ),
                density=self.density.currentData(),
                light=self.light.currentText().lower(),
                exposure=(
                    float(self.adaptation.exposure)
                    if self.auto_exposure.isChecked()
                    else self.exposure.number.value()
                ),
                context=self.context.currentData(),
                lit=self.lit.isChecked(),
                eye=self.eye.number.value(),
                time=self.time,
            )
            view["restyle"] = self.mode.currentIndex() == 0
            view["atlas_colours"] = self.atlas_colours.isChecked()
            view["frame"] = self.frame_size.currentData()
            view["mip"] = self.resident_mip.currentIndex()
            view["player"] = {key: field.value() for key, field in self.player_fields.items()}
            self.preview.request(dict(source=source, settings=settings, view=view, dds=dds))
        except Exception as e:
            self.show_error(str(e))

    def show_frame(self, result):
        if self.canvas.drag is not None:
            return
        pixels, caption, notes = result
        h, w = pixels.shape[:2]
        self.frame = QPixmap.fromImage(
            QImage(pixels.data, w, h, pixels.strides[0], QImage.Format.Format_RGB888).copy()
        )
        self.canvas.render_zoom = notes[-1]["zoom"]
        from PySide6.QtCore import QPointF

        self.canvas.render_offset = QPointF(*notes[-1]["pan"]) / self.canvas.devicePixelRatioF()
        self.draw_frame()
        stale = self.built and (
            self.built.snapshot.source != self.document.svg
            or self.built.snapshot.text != self.document.text
        )
        if self.mode.currentIndex() == 1 and stale:
            caption += " | STALE: rebuild to export current settings"
        caption = caption.replace("Game HLSL / D3D11 | ", "Preview | ")
        self.caption.setText(
            caption + "\nClear-glass SDR scene. Use Scene settings to match your game view."
        )
        self.caption.setToolTip(
            "Uses the game reticle and glow shaders. Full live-scene pixel parity has not been measured."
        )
        self.statusBar().showMessage(
            "Preview ready" if self.mode.currentIndex() == 0 else "Showing last exported DDS"
        )

    def animate_panel(self, *args):
        if self.canvas.motion:
            self.panel_animation.stop()
            self.panel_animation.setStartValue(0.65)
            self.panel_animation.setEndValue(1.0)
            self.panel_animation.start()

    def set_reduced_motion(self, reduced):
        self.canvas.motion = not reduced
        self.canvas.zoom_animation.stop()
        self.panel_animation.stop()
        self.panel_effect.setOpacity(1)

    def draw_frame(self):
        if self.frame:
            self.canvas.setPixmap(self.frame)

    def show_error(self, error):
        error = error.replace(
            "set 'scale' in the settings file", "set SVG units per angle unit in Size"
        )
        summary = error_summary(error)
        self.statusBar().showMessage(summary)
        self.caption.setText(summary)
        self.diagnostics.setPlainText(error)
        self.details.setChecked(True)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "Choose export folder", self.destination.text()
        )
        if path:
            self.destination.setText(path)

    def start_export(self, checked=False, preview_only=False):
        if not self.document or self.builds.job:
            return
        try:
            if not preview_only and not self.checks_valid:
                raise ValueError("Fix the dimension check names in Export before exporting.")
            if not preview_only and not self.destination.text().strip():
                raise ValueError("Choose an export folder")
            destination = (
                Path(self.cache_folder.name) / "compiled"
                if preview_only
                else self.destination.text()
            )
            self.builds.start(self.document, destination, preview_only)
        except Exception as e:
            self.build.setEnabled(True)
            self.cancel.setEnabled(False)
            self.cancel.hide()
            self.compile_preview.setEnabled(True)
            self.progress.hide()
            self.show_error(str(e))

    def build_started(self):
        self.build.setEnabled(False)
        self.cancel.setEnabled(True)
        self.cancel.show()
        self.compile_preview.setEnabled(False)
        self.progress.show()
        self.diagnostics.clear()
        self.diagnostics.appendPlainText(
            "Building a snapshot; source SVG and settings are unchanged. Final builds may take several minutes."
        )

    def build_progress(self, message):
        self.diagnostics.appendPlainText(message)
        last = message.strip().splitlines()[-1] if message.strip() else "Preparing preview"
        self.statusBar().showMessage(last[:180])

    def export_complete(self, snapshot, path, error, preview_only=False):
        self.progress.hide()
        self.build.setEnabled(True)
        self.cancel.setEnabled(False)
        self.cancel.hide()
        self.compile_preview.setEnabled(True)
        if self.pending_svg is not None:
            path_to_open, self.pending_svg = self.pending_svg, None
            self.open_svg(path_to_open[0], confirmed=True, document=path_to_open[1])
            return
        if error:
            if preview_only:
                self.geometry_failed = geometry_key(snapshot.source, snapshot.settings)
            if error == "Build failed; see diagnostics":
                lines = self.diagnostics.toPlainText().strip().splitlines()
                if lines:
                    error = lines[-1]
            self.details.setChecked(True)
            if preview_only:
                self.canvas.setText("Preview could not be built.\n" + error_summary(error))
            self.show_error(error)
        else:
            key = geometry_key(snapshot.source, snapshot.settings)
            try:
                current = geometry_key(self.document.svg, self.document.settings(preview=True))
            except (ValueError, st.configparser.Error) as error:
                self.show_error(str(error))
                return
            if preview_only and key != current:
                self.geometry_timer.start()
                return
            self.working = WorkingAtlas(path, key)
            if not preview_only:
                self.built = BuiltAtlas(snapshot, path)
                self.diagnostics.appendPlainText(f"Verified export: {path}")
            size = path.stat().st_size
            self.size_label.setText(
                f"DDS size: {size / 1048576:.2f} MiB ({size:,} bytes; includes all mips)"
            )
            self.mode.setCurrentIndex(0 if preview_only else 1)
            self.schedule_preview()

    def cancel_export(self):
        if self.builds.job:
            self.cancel.setEnabled(False)
            self.cancel.hide()
            self.builds.cancel()

    def dragEnterEvent(self, event):
        if any(
            url.isLocalFile() and url.toLocalFile().lower().endswith((".svg", ".reticle"))
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith((".svg", ".reticle")):
                self.open_path(url.toLocalFile())
                event.acceptProposedAction()
                return

    def closeEvent(self, event):
        if not self.can_replace():
            event.ignore()
            return
        self.animation.stop()
        self.adaptation_timer.stop()
        self.debounce.stop()
        self.geometry_timer.stop()
        self.svg_reload.stop()
        self.estimator.invalidate()
        self.estimator.pool.waitForDone()
        self.preview.invalidate()
        self.preview.renderer.abort()
        self.builds.close()
        self.preview.pool.waitForDone()
        self.preview.renderer.close()
        self.cache_folder.cleanup()
        event.accept()


def main():
    app = QApplication(sys.argv)
    configure_app(app)
    window = Window()
    window.show()
    if len(sys.argv) > 1:
        window.open_path(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
