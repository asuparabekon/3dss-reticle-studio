"""Keep wheel navigation from changing form values."""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QSlider, QTabBar


class FormWheelFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and isinstance(
            watched, (QComboBox, QAbstractSpinBox, QSlider, QTabBar)
        ):
            # Ignoring lets Qt propagate to the surrounding scroll area. Popup
            # lists, scrollbars and the preview canvas keep their wheel handling.
            event.ignore()
            return True
        return False


def configure_wheel_navigation(app):
    if not hasattr(app, "form_wheel_filter"):
        app.form_wheel_filter = FormWheelFilter(app)
        app.installEventFilter(app.form_wheel_filter)
