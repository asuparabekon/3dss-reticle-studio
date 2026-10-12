"""Form controls for reticle authors; serialization stays behind the UI."""

import math
from contextlib import nullcontext

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QSlider,
    QHBoxLayout,
    QVBoxLayout,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QColorDialog,
    QComboBox,
    QLabel,
    QLineEdit,
)
import settings as st


class Slider(QWidget):
    changed = Signal(float)

    def __init__(self, lo, hi, value, logarithmic=False):
        super().__init__()
        self.lo, self.hi, self.logarithmic = lo, hi, logarithmic
        self.track = QSlider(Qt.Orientation.Horizontal)
        self.track.setRange(0, 1000)
        self.track.setMinimumHeight(26)
        self.number = QDoubleSpinBox()
        self.number.setRange(lo, hi)
        self.number.setDecimals(max(3, math.ceil(-math.log10(lo)) + 1) if logarithmic else 3)
        self.number.setSingleStep(0.01)
        self.number.setFixedWidth(92)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.track)
        layout.addWidget(self.number)
        self.track.valueChanged.connect(self.from_track)
        self.number.valueChanged.connect(self.from_number)
        self.set_value(value)

    def set_value(self, value):
        with QSignalBlocker(self.number):
            self.number.setValue(value)
        value = self.number.value()
        ratio = (
            math.log(value / self.lo) / math.log(self.hi / self.lo)
            if self.logarithmic
            else (value - self.lo) / (self.hi - self.lo)
        )
        with QSignalBlocker(self.track):
            self.track.setValue(round(ratio * 1000))

    def from_track(self, value):
        value = (
            self.lo * (self.hi / self.lo) ** (value / 1000)
            if self.logarithmic
            else self.lo + (self.hi - self.lo) * value / 1000
        )
        self.set_value(value)
        self.changed.emit(self.number.value())

    def from_number(self, value):
        self.set_value(value)
        self.changed.emit(value)


class Choice(QComboBox):
    def __init__(self, choices):
        super().__init__()
        for label, value in choices:
            self.addItem(label, value)

    def setCurrentText(self, value):
        index = self.findData(value)
        if index < 0:
            raise ValueError(f"Unknown option: {value}")
        self.setCurrentIndex(index)

    def currentText(self):
        return self.currentData()


class NumberField(QWidget):
    textEdited = Signal(str)

    def __init__(self, lo, hi, default=1, optional=None):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.auto = QCheckBox(optional) if optional else None
        self.number = QDoubleSpinBox()
        self.number.setRange(lo, hi)
        self.number.setDecimals(4)
        self.number.setValue(default)
        self.number.setKeyboardTracking(False)
        if self.auto:
            layout.addWidget(self.auto)
            self.auto.toggled.connect(self.changed)
        layout.addWidget(self.number)
        self.number.valueChanged.connect(self.changed)

    def changed(self, *args):
        self.number.setEnabled(not self.auto or not self.auto.isChecked())
        self.textEdited.emit(self.text())

    def text(self):
        return "" if self.auto and self.auto.isChecked() else f"{self.number.value():g}"

    def setText(self, value):
        with QSignalBlocker(self):
            if self.auto:
                self.auto.setChecked(not str(value).strip())
            if str(value).strip():
                self.number.setValue(float(value))
            self.number.setEnabled(not self.auto or not self.auto.isChecked())


class PairField(QWidget):
    textEdited = Signal(str)

    def __init__(self, lo=-100000, hi=100000, automatic=False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.auto = QCheckBox("Automatic") if automatic else None
        if self.auto:
            layout.addWidget(self.auto)
            self.auto.toggled.connect(self.changed)
        self.values = []
        for axis in ("X", "Y"):
            layout.addWidget(QLabel(axis))
            field = QDoubleSpinBox()
            field.setRange(lo, hi)
            field.setDecimals(4)
            field.setKeyboardTracking(False)
            field.valueChanged.connect(self.changed)
            layout.addWidget(field)
            self.values.append(field)

    def changed(self, *args):
        for field in self.values:
            field.setEnabled(not self.auto or not self.auto.isChecked())
        self.textEdited.emit(self.text())

    def text(self):
        return (
            ""
            if self.auto and self.auto.isChecked()
            else ", ".join(f"{f.value():g}" for f in self.values)
        )

    def setText(self, text):
        with QSignalBlocker(self):
            if self.auto:
                self.auto.setChecked(not text.strip())
            for field, value in zip(
                self.values, text.split(",") if text.strip() else ("0", "0")
            ):
                field.setValue(float(value))
            self.changed()


class ColourField(QWidget):
    textEdited = Signal(str)

    def __init__(self, hex_output=False):
        super().__init__()
        self.hex_output = hex_output
        self.value = (1, 0.3, 0.3)
        self.raw = "scope_red2"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.chip = QPushButton()
        self.chip.setFixedSize(28, 24)
        self.chip.setToolTip("Choose colour")
        self.label = QLabel()
        self.button = QPushButton("Change...")
        self.chip.clicked.connect(self.choose)
        self.button.clicked.connect(self.choose)
        row.addWidget(self.chip)
        row.addWidget(self.label, 1)
        row.addWidget(self.button)
        self.refresh()

    def refresh(self):
        colour = QColor.fromRgbF(*[min(max(v, 0), 1) for v in self.value])
        self.label.setText(colour.name())
        self.chip.setStyleSheet(
            f"QPushButton {{ background: {colour.name()}; border: 1px solid #999999; border-radius: 3px; padding: 0; }}"
        )

    def setText(self, text):
        self.raw = text
        if text.startswith("#"):
            c = QColor(text)
            self.value = (c.redF(), c.greenF(), c.blueF())
        else:
            self.value = st.colour(text)
        self.refresh()

    def text(self):
        return self.raw

    def choose(self):
        c = QColorDialog.getColor(
            QColor.fromRgbF(*[min(max(v, 0), 1) for v in self.value]),
            self,
            "Choose reticle colour",
        )
        if c.isValid():
            self.raw = (
                c.name()
                if self.hex_output
                else ", ".join(f"{v:.6g}" for v in (c.redF(), c.greenF(), c.blueF()))
            )
            self.setText(self.raw)
            self.textEdited.emit(self.raw)

    def setCurrentText(self, value):
        self.setText(value)


class ListField(QWidget):
    textEdited = Signal(str)

    def __init__(self, kind, limit=15):
        super().__init__()
        self.kind = kind
        self.limit = limit
        self.rows = []
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel(
            "Use the sight's settings" if kind != "paint" else "No painted colours emit light"
        )
        self.layout.addWidget(self.empty)
        self.add = QPushButton("Add colour" if kind != "steps" else "Add brightness step")
        self.add.clicked.connect(lambda: self.append())
        self.layout.addWidget(self.add)

    def append(self, value=None, emit=True):
        if len(self.rows) >= self.limit:
            return
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(str(len(self.rows) + 1))
        row.addWidget(label)
        field = (
            NumberField(0, 1, 1) if self.kind == "steps" else ColourField(self.kind == "paint")
        )
        field.setText(
            value
            if value is not None
            else (
                "1"
                if self.kind == "steps"
                else "#ffffff" if self.kind == "paint" else "scope_red2"
            )
        )
        field.textEdited.connect(self.changed)
        row.addWidget(field, 1)
        remove = QPushButton("Remove")
        row.addWidget(remove)
        item = (container, label, field)
        remove.clicked.connect(lambda: self.remove(item))
        self.rows.append(item)
        self.layout.insertWidget(self.layout.count() - 1, container)
        if emit:
            self.changed()

    def remove(self, item):
        self.rows.remove(item)
        self.layout.removeWidget(item[0])
        item[0].hide()
        item[0].deleteLater()
        self.changed()

    def changed(self, *args):
        for i, (_, label, _) in enumerate(self.rows):
            label.setText(str(i + 1))
        self.empty.setVisible(not self.rows)
        self.add.setEnabled(len(self.rows) < self.limit)
        self.textEdited.emit(self.text())

    def text(self):
        return (" | " if self.kind == "colours" else ", ").join(
            field.text() for _, _, field in self.rows
        )

    def setText(self, text):
        with QSignalBlocker(self):
            for container, _, _ in self.rows:
                self.layout.removeWidget(container)
                container.hide()
                container.deleteLater()
            self.rows = []
            for value in text.split("|" if self.kind == "colours" else ","):
                if value.strip():
                    self.append(value.strip(), False)
            self.changed()


class ChecksEditor(QWidget):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.rows = []
        self.layout = QVBoxLayout(self)
        self.add = QPushButton("Add dimension check")
        self.add.clicked.connect(lambda: self.append())
        self.layout.addWidget(self.add)

    def append(self, name=None, values=None, emit=True):
        card = QWidget()
        box = QVBoxLayout(card)
        top = QHBoxLayout()
        box.addLayout(top)
        kind = Choice(
            [
                ("Shape position", "shape"),
                ("Centre dot diameter", "dot"),
                ("Ring diameters", "ring"),
            ]
        )
        kind.setCurrentText(name if name in ("dot", "ring") else "shape")
        title = QLineEdit(
            name if name not in (None, "dot", "ring") else f"check_{len(self.rows)+1}"
        )
        title.setPlaceholderText("Check name")
        top.addWidget(kind)
        top.addWidget(title)
        remove = QPushButton("Remove")
        top.addWidget(remove)
        shape = QLineEdit()
        shape.setPlaceholderText("SVG shape ID, e.g. path16")
        side = Choice(
            [
                (v.replace("_", " ").title(), v)
                for v in ("left", "right", "top", "bottom", "centre_x", "centre_y")
            ]
        )
        middle = QHBoxLayout()
        middle.addWidget(shape)
        middle.addWidget(side)
        box.addLayout(middle)
        bottom = QHBoxLayout()
        a_label = QLabel()
        b_label = QLabel()
        a = NumberField(-100000, 100000, 1)
        b = NumberField(-100000, 100000, 0.02)
        bottom.addWidget(a_label)
        bottom.addWidget(a)
        bottom.addWidget(b_label)
        bottom.addWidget(b)
        box.addLayout(bottom)
        row = dict(
            card=card,
            kind=kind,
            name=title,
            shape=shape,
            side=side,
            a=a,
            b=b,
            a_label=a_label,
            b_label=b_label,
        )

        def update(*args):
            role = kind.currentData()
            title.setVisible(role == "shape")
            shape.setVisible(role == "shape")
            side.setVisible(role == "shape")
            a_label.setText(
                "Inner" if role == "ring" else "Diameter" if role == "dot" else "Position"
            )
            b_label.setText("Outer" if role == "ring" else "Tolerance")
            b.setVisible(role != "dot")
            b_label.setVisible(role != "dot")
            self.changed.emit(self.values())

        self.rows.append(row)
        self.layout.insertWidget(self.layout.count() - 1, card)
        if values:
            if name == "dot":
                a.setText(values[0])
            elif name == "ring":
                a.setText(values[0])
                b.setText(values[1])
            else:
                shape.setText(values[0])
                side.setCurrentText(values[1])
                a.setText(values[2])
                b.setText(values[3] if len(values) > 3 else ".02")
        for field in (title, shape):
            field.textEdited.connect(update)
        for field in (a, b):
            field.textEdited.connect(update)
        kind.currentIndexChanged.connect(update)
        side.currentIndexChanged.connect(update)
        remove.clicked.connect(lambda: self.remove(row))
        with QSignalBlocker(self) if not emit else nullcontext():
            update()

    def remove(self, row):
        self.rows.remove(row)
        self.layout.removeWidget(row["card"])
        row["card"].hide()
        row["card"].deleteLater()
        self.changed.emit(self.values())

    def values(self):
        result = []
        for row in self.rows:
            kind = row["kind"].currentData()
            if kind == "dot":
                result.append(("dot", [row["a"].text()]))
            elif kind == "ring":
                result.append(("ring", [row["a"].text(), row["b"].text()]))
            else:
                result.append(
                    (
                        row["name"].text(),
                        [
                            row["shape"].text(),
                            row["side"].currentData(),
                            row["a"].text(),
                            row["b"].text(),
                        ],
                    )
                )
        return result

    def set_values(self, checks):
        for row in self.rows:
            self.layout.removeWidget(row["card"])
            row["card"].hide()
            row["card"].deleteLater()
        self.rows = []
        for name, values in checks:
            self.append(name, values, False)
