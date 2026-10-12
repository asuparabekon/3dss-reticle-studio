"""Edit one switch state at a time, using the selected pattern's SVG materials."""

from dataclasses import replace
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QCheckBox,
    QPushButton,
    QLabel,
    QFormLayout,
)
from authoring_controls import ColourField, Slider
from authoring_help import HelpLabel
from reticle_states import (
    SwitchState,
    MaterialOverride,
    MAX_STATES,
    material_value,
    states_text,
)
import settings


class StateEditor(QWidget):
    changed = Signal(str)
    selected = Signal(int)

    def __init__(self):
        super().__init__()
        self._states = ()
        self.materials = ()
        self.patterns = [1]
        self.authored = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.selection = QComboBox()
        self.selection.currentIndexChanged.connect(self.select)
        self.add = QPushButton("Add state")
        self.add.clicked.connect(self.append)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self.remove_selected)
        bar.addWidget(self.selection, 1)
        bar.addWidget(self.add)
        bar.addWidget(self.remove)
        layout.addLayout(bar)
        form = QFormLayout()
        self.pattern = QComboBox()
        self.pattern.currentIndexChanged.connect(self.change_pattern)
        form.addRow(
            HelpLabel(
                "Pattern",
                "Choose the SVG Mark group this state draws. Colour changes reuse its geometry.",
            ),
            self.pattern,
        )
        layout.addLayout(form)
        self.body = QWidget()
        layout.addWidget(self.body)

    def states(self):
        return self._states

    def set_states(self, states, patterns, authored=True, materials=()):
        self._states = tuple(states)
        self.patterns = patterns or [1]
        self.authored = authored
        self.materials = tuple(materials)
        self.refresh_selection(self.selection.currentIndex())

    def refresh_selection(self, index):
        with QSignalBlocker(self.selection):
            self.selection.clear()
            for i in range(len(self._states)):
                self.selection.addItem(f"State {i + 1}")
            self.selection.setCurrentIndex(min(max(index, 0), len(self._states) - 1))
        self.add.setEnabled(len(self._states) < MAX_STATES)
        self.remove.setEnabled(len(self._states) > 1)
        self.show_materials()

    def select(self, index):
        if index != self.selection.currentIndex():
            self.selection.setCurrentIndex(index)
            return
        self.show_materials()
        self.selected.emit(index)

    def append(self):
        if len(self._states) >= MAX_STATES:
            return
        previous = (
            self._states[self.selection.currentIndex()]
            if self._states
            else SwitchState(self.patterns[0])
        )
        self._states += (previous,)
        self.refresh_selection(len(self._states) - 1)
        self.emit_states()
        self.selected.emit(self.selection.currentIndex())

    def remove_selected(self):
        if len(self._states) <= 1:
            return
        i = self.selection.currentIndex()
        self._states = self._states[:i] + self._states[i + 1 :]
        self.refresh_selection(i)
        self.emit_states()
        self.selected.emit(self.selection.currentIndex())

    def change_pattern(self):
        i = self.selection.currentIndex()
        if i < 0 or self.pattern.currentData() is None:
            return
        self.replace_state(replace(self._states[i], pattern=self.pattern.currentData()))
        self.show_materials()

    def replace_state(self, state):
        i = self.selection.currentIndex()
        self._states = self._states[:i] + (state,) + self._states[i + 1 :]
        self.emit_states()

    def show_materials(self):
        i = self.selection.currentIndex()
        if not 0 <= i < len(self._states):
            return
        state = self._states[i]
        with QSignalBlocker(self.pattern):
            self.pattern.clear()
            for p in sorted(set(self.patterns + [state.pattern])):
                self.pattern.addItem(
                    f"Pattern {p}" + (" (missing)" if p not in self.patterns else ""), p
                )
            self.pattern.setCurrentIndex(self.pattern.findData(state.pattern))
        old = self.body
        self.body = QWidget()
        self.layout().replaceWidget(old, self.body)
        old.hide()
        old.deleteLater()
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 8, 0, 0)
        self.controls = {}
        self.visibility = {}
        for role, heading in (("paint", "Paint"), ("illum", "Illumination")):
            groups = [
                m for m in self.materials if m.role == role and m.pattern in (0, state.pattern)
            ]
            if not groups:
                continue
            layout.addWidget(QLabel(heading))
            for material in groups:
                self.add_material(layout, material, material_value(state, material))
        known = {m.key for m in self.materials}
        if any(v.material not in known for v in state.materials):
            clear = QPushButton("Remove settings for missing SVG groups")
            clear.clicked.connect(lambda: self.clear_missing(known))
            layout.addWidget(clear)

    def add_material(self, layout, material, value):
        form = QFormLayout()
        name = material.name + (" · shared" if material.pattern == 0 else "")
        layout.addWidget(
            HelpLabel(
                name,
                "Named SVG group. Shared groups appear in every pattern. Each state can change this group's appearance without copying its geometry.",
            )
        )
        visible = QCheckBox("Visible")
        visible.setChecked(value.visible)
        visible.setToolTip(
            "Show this group in this state. Turning it off preserves its colour and brightness."
        )
        form.addRow(visible)
        self.visibility[material.key] = visible
        source = QComboBox()
        source.addItems(["SVG fill", "Custom colour"])
        source.setCurrentIndex(int(value.colour is not None))
        colour = ColourField()
        colour.setText(", ".join(str(v) for v in (value.colour or (1, 0, 0))))
        colour.setVisible(value.colour is not None)
        editable = material.role == "paint" or self.authored
        form.addRow(
            HelpLabel(
                "Paint colour" if material.role == "paint" else "Illum colour",
                "SVG fill preserves the colours inside this group. Custom colour replaces them for this state. In 3DSS mode the game supplies illumination colour.",
            ),
            source,
        )
        form.addRow(colour)
        brightness = Slider(0, 1, value.brightness) if material.role == "illum" else None
        if brightness:
            form.addRow(
                HelpLabel(
                    "Illum brightness",
                    "0 turns this group's light off. Paint stays visible. In 3DSS mode this multiplies the game's brightness.",
                ),
                brightness,
            )

        def update_enabled():
            source.setEnabled(visible.isChecked() and editable)
            colour.setEnabled(visible.isChecked() and editable)
            if brightness:
                brightness.setEnabled(visible.isChecked())

        update_enabled()
        visible.toggled.connect(lambda _: update_enabled())
        visible.toggled.connect(lambda _: self.change_material(material))
        layout.addLayout(form)
        self.controls[material.key] = source, colour, brightness
        source.currentIndexChanged.connect(
            lambda _: colour.setVisible(source.currentIndex() == 1)
        )
        source.currentIndexChanged.connect(lambda _: self.change_material(material))
        colour.textEdited.connect(lambda _: self.change_material(material))
        if brightness:
            brightness.changed.connect(lambda _: self.change_material(material))

    def clear_missing(self, known):
        state = self._states[self.selection.currentIndex()]
        self.replace_state(
            replace(state, materials=tuple(v for v in state.materials if v.material in known))
        )
        self.show_materials()

    def change_material(self, material):
        source, colour, brightness = self.controls[material.key]
        value = MaterialOverride(
            material.key,
            None if source.currentIndex() == 0 else tuple(settings.colour(colour.text())),
            brightness.number.value() if brightness else 1.0,
            self.visibility[material.key].isChecked(),
        )
        state = self._states[self.selection.currentIndex()]
        others = tuple(v for v in state.materials if v.material != material.key)
        self.replace_state(replace(state, materials=others + (value,)))

    def emit_states(self):
        self.changed.emit(states_text(self._states))
