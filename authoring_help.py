"""Small disclosure and help controls used by the authoring panels."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QToolButton,
    QToolTip,
)


class HelpLabel(QLabel):
    def __init__(self, title, explanation):
        super().__init__(title)
        self.setToolTip(explanation)
        self.setAccessibleDescription(explanation)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.help_delay = QTimer(self)
        self.help_delay.setSingleShot(True)
        self.help_delay.setInterval(250)
        self.help_delay.timeout.connect(self.show_help)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        # Let a scroll area's focus handling settle before placing the tooltip.
        self.help_delay.start()

    def show_help(self):
        if self.hasFocus():
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), self.toolTip(), self)

    def focusOutEvent(self, event):
        self.help_delay.stop()
        QToolTip.hideText()
        super().focusOutEvent(event)


class Disclosure(QWidget):
    def __init__(self, title, content):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.toggled.connect(content.setVisible)
        self.toggle.toggled.connect(
            lambda opened: self.toggle.setArrowType(
                Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
            )
        )
        layout.addWidget(self.toggle)
        layout.addWidget(content)
        content.hide()
