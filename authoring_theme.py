"""Neutral desktop palette and control styling."""

import sys
from pathlib import Path
from PySide6.QtGui import QFont, QFontDatabase


def configure_app(app):
    import os
    from authoring_input import configure_wheel_navigation

    configure_wheel_navigation(app)
    app.setApplicationName("MSDF Reticle Studio")
    app.setStyle("Fusion")
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    from PySide6.QtGui import QPalette, QColor

    palette = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, "#282828"),
        (QPalette.ColorRole.WindowText, "#d4d4d4"),
        (QPalette.ColorRole.Base, "#222222"),
        (QPalette.ColorRole.AlternateBase, "#303030"),
        (QPalette.ColorRole.Text, "#d4d4d4"),
        (QPalette.ColorRole.Button, "#353535"),
        (QPalette.ColorRole.ButtonText, "#d4d4d4"),
        (QPalette.ColorRole.Highlight, "#65615a"),
        (QPalette.ColorRole.HighlightedText, "#eeeeee"),
        (QPalette.ColorRole.ToolTipBase, "#333333"),
        (QPalette.ColorRole.ToolTipText, "#d4d4d4"),
    ):
        palette.setColor(role, QColor(colour))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#777777"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#777777"))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { color: #d4d4d4; background: #282828; }
        QLineEdit, QComboBox, QPlainTextEdit {
            background: #222222; border: 1px solid #484848; border-radius: 4px;
            padding: 5px; selection-background-color: #65615a; selection-color: #eeeeee;
        }
        QPushButton { background: #353535; border: 1px solid #484848; border-radius: 4px; padding: 6px 10px; }
        QPushButton#primary { background: #655e4f; border-color: #b7aa8e; font-weight: bold; }
        QToolButton { padding: 3px; border: 1px solid #555555; border-radius: 4px; }
        QToolButton:hover { background: #414141; }
        QPushButton:hover { background: #414141; border-color: #686868; }
        QPushButton:pressed, QPushButton:checked { background: #4b4945; }
        QWidget:disabled { color: #777777; }
        QTabWidget::pane { border: 1px solid #444444; border-radius: 5px; }
        QTabBar::tab { padding: 9px 14px; background: #2e2e2e; border-bottom: 2px solid transparent; }
        QTabBar::tab:selected { background: #383838; border-bottom: 2px solid #aaa18e; }
        QGroupBox { border: 1px solid #444444; border-radius: 5px; margin-top: 12px; padding-top: 12px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QCheckBox { spacing: 8px; }
        QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #a0a0a0; border-radius: 3px; background: #202020; }
        QCheckBox::indicator:checked { background: #c4b79a; border: 3px solid #c4b79a; image: none; }
        QCheckBox::indicator:unchecked:hover { border-color: #eeeeee; background: #454545; }
        QCheckBox::indicator:disabled { border-color: #555555; background: #303030; }
        QSlider::groove:horizontal { height: 4px; margin: 0 7px; background: #484848; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: #a69c87; border-radius: 2px; }
        QSlider::handle:horizontal { background: #c6c0b4; border: 1px solid #a69c87; width: 14px; margin: -5px -7px; border-radius: 7px; }
        QProgressBar { border: 0; background: #363636; max-height: 4px; }
        QProgressBar::chunk { background: #a69c87; }
        QLabel#hint { color: #a0a0a0; font-size: 11px; }
        QSplitter::handle { background: #444444; width: 2px; }
        QToolTip { color: #d4d4d4; background: #333333; border: 1px solid #555555; }
    """)

    check_icon = (
        Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / ".preview/shaders/check.png"
    )
    app.setStyleSheet(
        app.styleSheet()
        + 'QCheckBox::indicator:checked { image: url("'
        + check_icon.as_posix()
        + '"); }'
    )
