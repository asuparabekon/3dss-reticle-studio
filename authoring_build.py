"""Own the active compiler and its immutable source snapshot."""

import copy

from PySide6.QtCore import QObject, Signal

from authoring_jobs import BuildSnapshot, ExportJob
from reticle_preview import geometry_key


class BuildController(QObject):
    started = Signal()
    progress = Signal(str)
    finished = Signal(object, object, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.job = None
        self.requested_geometry = None

    def start(self, document, destination, preview_only=False):
        if self.job is not None:
            return
        snapshot = BuildSnapshot(
            document.svg, copy.deepcopy(document.settings(preview=preview_only)), document.text
        )
        if preview_only:
            self.requested_geometry = geometry_key(snapshot.source, snapshot.settings)
        job = ExportJob(document, destination, self, preview_only=preview_only)
        self.job = job
        job.progress.connect(self.progress)
        job.finished.connect(lambda path, error: self.complete(snapshot, path, error, preview_only))
        try:
            self.started.emit()
            job.start()
        except Exception:
            job.deleteLater()
            self.job = None
            raise

    def complete(self, snapshot, path, error, preview_only):
        self.job.deleteLater()
        self.job = None
        self.finished.emit(snapshot, path, error, preview_only)

    def cancel(self):
        if self.job is not None:
            self.job.cancel()

    def close(self):
        if self.job is not None:
            job = self.job
            job.cancel()
            job.process.waitForFinished(5000)
