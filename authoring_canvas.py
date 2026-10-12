"""Interactive MSDF preview navigation."""

from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget


class PreviewCanvas(QWidget):
    zoomChanged = Signal(float)
    viewChanged = Signal()

    def __init__(self, text=""):
        super().__init__()
        self.image = None
        self.message = text
        self.zoom = 1.0
        self.render_zoom = 1.0
        self.render_offset = QPointF()
        self.offset = QPointF()
        self.drag = None
        self.motion = True
        self.zoom_anchor = QPointF()
        self.zoom_animation = QVariantAnimation(self)
        self.zoom_animation.setDuration(120)
        self.zoom_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.zoom_animation.valueChanged.connect(
            lambda v: self.set_zoom(float(v), self.zoom_anchor)
        )
        self.fitting = True
        self.setMinimumSize(240, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "Scroll to zoom at the pointer. Drag to pan. Double-click or F to fit. Press 1 for actual pixels."
        )

    def setPixmap(self, image):
        first = self.image is None
        self.image = image
        self.background = image.toImage().pixelColor(0, 0)
        self.message = ""
        if self.fitting and first:
            self.fit()
        self.update()

    def setText(self, text):
        self.message = text
        self.image = None
        self.update()

    def text(self):
        return self.message

    def clear(self):
        self.setText("")

    def centre(self):
        return QPointF(self.width() / 2, self.height() / 2)

    def set_zoom(self, value, anchor=None):
        value = min(32.0, max(0.125, value))
        anchor = self.centre() if anchor is None else anchor
        self.offset = (
            anchor - self.centre() - (anchor - self.centre() - self.offset) * (value / self.zoom)
        )
        self.zoom = value
        self.fitting = False
        self.zoomChanged.emit(value)
        self.viewChanged.emit()
        self.update()

    def actual_pixels(self):
        self.zoom_animation.stop()
        self.offset = QPointF()
        self.set_zoom(1)

    def fit(self):
        self.zoom_animation.stop()
        if self.image:
            ratio = self.devicePixelRatioF()
            self.set_zoom(
                min((self.width() - 32) * ratio / 384, (self.height() - 32) * ratio / 384)
            )
        self.offset = QPointF()
        self.fitting = True
        self.viewChanged.emit()
        self.update()

    def resizeEvent(self, event):
        if self.fitting:
            self.fit()
        self.viewChanged.emit()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        if self.image:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if self.motion:
                base = (
                    self.zoom_animation.endValue()
                    if self.zoom_animation.state() == QVariantAnimation.State.Running
                    else self.zoom
                )
                self.zoom_animation.stop()
                self.zoom_anchor = event.position()
                self.zoom_animation.setStartValue(float(self.zoom))
                self.zoom_animation.setEndValue(min(32.0, max(0.125, base * 2 ** (delta / 480))))
                self.zoom_animation.start()
            else:
                self.set_zoom(self.zoom * 2 ** (delta / 480), event.position())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.zoom_animation.stop()
            self.drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setFocus()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            self.offset += event.position() - self.drag
            self.drag = event.position()
            self.fitting = False
            self.viewChanged.emit()
            self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.viewChanged.emit()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):
        self.fit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F:
            self.fit()
        elif event.key() == Qt.Key.Key_1:
            self.actual_pixels()
        elif event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_zoom(self.zoom * 1.25)
        elif event.key() == Qt.Key.Key_Minus:
            self.set_zoom(self.zoom / 1.25)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.background if self.image else QColor("#202020"))
        if self.image:
            size = self.image.size().toSizeF() * (
                self.zoom / self.render_zoom / self.devicePixelRatioF()
            )
            top = (
                self.centre()
                + self.offset
                - self.render_offset * (self.zoom / self.render_zoom)
                - QPointF(size.width() / 2, size.height() / 2)
            )
            rect = QRectF(top, size)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(rect, self.image, QRectF(self.image.rect()))
        else:
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
