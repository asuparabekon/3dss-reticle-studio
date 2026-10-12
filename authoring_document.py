"""Editable sidecar settings. Opening a drawing never writes to its source folder."""

import os
import re
import tempfile
from pathlib import Path

import settings as st


def set_value(text, section, key, value):
    """Replace one value while retaining comments, unknown keys and other sections."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    current, end = None, len(lines)
    for i, line in enumerate(lines):
        heading = re.match(r"\s*\[([^]]+)\]", line)
        if heading:
            if current == section:
                end = i
                break
            current = heading[1].lower()
        elif current == section:
            match = re.match(
                r"([ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*)([^\r\n]*)", line, re.I
            )
            if match:
                comment = match[2].partition(";")[2]
                lines[i] = match[1] + str(value) + (" ;" + comment if comment else "") + newline
                return "".join(lines)
    if current != section:
        return (
            text.rstrip()
            + newline * 2
            + f"[{section}]"
            + newline
            + f"{key} = {value}"
            + newline
        )
    if end and not lines[end - 1].endswith(("\n", "\r")):
        lines[end - 1] += newline
    lines.insert(end, f"{key} = {value}" + newline)
    return "".join(lines)


def atomic_text(path, text):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".reticle-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Document:
    def __init__(self, path, text=None):
        self.project_path = None
        self.path = Path(path).resolve()
        self.svg = self.path.read_bytes()
        self.saved_svg = self.svg
        self.ini = st.template_path(self.path)
        self.text = (
            text
            if text is not None
            else (
                self.ini.read_bytes().decode("utf-8-sig")
                if self.ini.exists()
                else st.TEMPLATE.format(svg=self.path.name)
            )
        )
        self.saved_text = self.text
        # SVG-only authoring starts with an explicit editable unit convention.
        # An SVG calibration layer takes precedence over this initial default.
        import configparser
        import svg_reticle

        cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        cp.read_string(self.text)
        drawing = svg_reticle.read_reticle_svg(self.path)
        if not cp.get("reticle", "scale", fallback="").strip() and not drawing.calibration:
            self.text = set_value(self.text, "reticle", "scale", "1")
        if not self.ini.exists():
            # Opening a drawing is not an edit and does not create a settings file.
            self.saved_text = self.text

    @property
    def dirty(self):
        return self.text != self.saved_text or (
            self.project_path is not None and self.svg != self.saved_svg
        )

    def settings(self, preview=False):
        text = self.text
        # A new drawing may be previewed before its destination is chosen.
        if preview:
            import configparser

            cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
            cp.read_string(text)
            if not cp.get("reticle", "texture", fallback="").strip():
                text = set_value(text, "reticle", "texture", "wpn\\unsaved_reticle")
        return st.parse(text)

    def update(self, section, key, value):
        self.text = set_value(self.text, section, key, value)

    def save(self):
        self.settings(preview=True)
        atomic_text(self.ini, self.text)
        self.saved_text = self.text

    def snapshot(self, folder, preview=False):
        """Export and preview jobs see the exact loaded SVG and current settings."""
        settings = self.settings(preview=preview)
        path = Path(folder) / self.path.name
        path.write_bytes(self.svg)
        text = (
            set_value(self.text, "reticle", "texture", settings.texture)
            if preview
            else self.text
        )
        st.template_path(path).write_text(text, encoding="utf-8")
        return path

    def save_project(self, path):
        """A portable project carries the SVG and settings; no adjacent file is needed."""
        import zipfile
        import json

        path = Path(path)
        self.settings(preview=True)
        fd, temporary = tempfile.mkstemp(prefix=".reticle-project-", dir=path.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as project:
                project.writestr("drawing.svg", self.svg)
                project.writestr("settings.ini", self.text)
                project.writestr(
                    "project.json",
                    json.dumps(
                        {"version": 1, "name": self.path.name, "source": str(self.path)}
                    ),
                )
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.project_path = path.resolve()
        self.saved_text = self.text
        self.saved_svg = self.svg

    @classmethod
    def load_project(cls, path, folder):
        import zipfile
        import json

        with zipfile.ZipFile(path) as project:
            if any(
                project.getinfo(name).file_size > 64 * 1024 * 1024
                for name in ("project.json", "drawing.svg", "settings.ini")
            ):
                raise ValueError("Project file is too large")
            meta = json.loads(project.read("project.json"))
            if meta.get("version") != 1:
                raise ValueError("This project version is not supported")
            svg, text = project.read("drawing.svg"), project.read("settings.ini").decode(
                "utf-8-sig"
            )
        name = Path(meta["name"].replace("\\", "/")).name
        if not name.lower().endswith(".svg"):
            raise ValueError("The project drawing must be an SVG")
        source = Path(meta.get("source", ""))
        try:
            matches_source = (
                source.is_file()
                and source.stat().st_size == len(svg)
                and source.read_bytes() == svg
            )
        except OSError:
            matches_source = False
        if not matches_source:
            source = Path(folder) / name
            source.write_bytes(svg)
        document = cls(source, text=text)
        document.text = document.saved_text = text
        document.project_path = Path(path).resolve()
        document.settings(preview=True)
        return document
