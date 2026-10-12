"""Latest-only preview scheduling and an isolated, cancellable export process."""

import os
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, QThreadPool, QTimer, Signal
from preview_process import PreviewCancelled
from settings import Settings


@dataclass(frozen=True)
class BuildSnapshot:
    source: bytes
    settings: Settings
    text: str


@dataclass(frozen=True)
class BuiltAtlas:
    snapshot: BuildSnapshot
    path: Path


@dataclass(frozen=True)
class WorkingAtlas:
    path: Path
    key: tuple | None


class ResultSignals(QObject):
    done = Signal(int, object, object)


class PreviewTask(QRunnable):
    def __init__(self, revision, renderer, request):
        super().__init__()
        self.revision, self.renderer, self.request = revision, renderer, request
        self.signals = ResultSignals()

    def run(self):
        try:
            self.signals.done.emit(self.revision, self.renderer.render(**self.request), None)
        except (ValueError, PreviewCancelled) as e:
            self.signals.done.emit(self.revision, None, str(e))
        except Exception as e:
            traceback.print_exc()
            self.signals.done.emit(self.revision, None, str(e))


class PreviewQueue(QObject):
    frame = Signal(object)
    error = Signal(str)

    def __init__(self, renderer, parent=None):
        super().__init__(parent)
        self.renderer, self.revision, self.pending, self.active = renderer, 0, None, None
        self.minimum_revision = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    def invalidate(self):
        self.revision += 1
        self.minimum_revision = self.revision
        self.pending = None

    def request(self, request):
        self.revision += 1
        self.pending = (self.revision, request)
        self.start_pending()

    def start_pending(self):
        if self.active is not None or self.pending is None:
            return
        revision, request = self.pending
        self.pending = None
        self.active = PreviewTask(revision, self.renderer, request)
        self.active.signals.done.connect(self.finished)
        self.pool.start(self.active)

    def finished(self, revision, result, error):
        self.active = None
        # Show completed frames during a drag; only explicit invalidation suppresses them.
        # The single pending request is always replaced with the newest settings.
        if revision >= self.minimum_revision:
            if error:
                self.error.emit(error)
            else:
                self.frame.emit(result)
        self.start_pending()


def publish_package(staged, destination):
    """Only called for a successful gated worker. Each DDS replacement is atomic."""
    staged, destination = Path(staged), Path(destination)
    dds = list(staged.glob("gamedata/textures/**/*.dds"))
    if len(dds) != 1:
        raise ValueError("Build did not produce exactly one validated DDS")
    target = destination / dds[0].relative_to(staged)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Prepare supporting files first; no fallible file work follows DDS commit.
    for pattern in ("preview_*.png", "report.txt"):
        for source in staged.glob(pattern):
            shutil.copy2(source, destination / source.name)
    fd, temporary = tempfile.mkstemp(prefix=".reticle-", suffix=".dds", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(dds[0], temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


class ExportJob(QObject):
    progress = Signal(str)
    finished = Signal(object, str)

    def __init__(self, document, destination, parent=None, preview_only=False):
        super().__init__(parent)
        self.folder = tempfile.TemporaryDirectory(prefix="reticle-export-")
        self.source = document.snapshot(self.folder.name, preview=preview_only)
        self.output = Path(self.folder.name) / "output"
        self.destination = Path(destination)
        self.cancelled = False
        self.preview_only = preview_only
        self.log_position = 0
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.process_error)
        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self.read_log)

    def start(self):
        arguments = [
            "--preview-job" if self.preview_only else "--build-job",
            str(self.source),
            str(self.output),
        ]
        if not getattr(sys, "frozen", False):
            arguments.insert(0, str(Path(__file__).with_name("author_reticle.py")))
        self.process.start(sys.executable, arguments)
        self.poll.start()

    def read_log(self):
        log = Path(self.folder.name) / "worker.log"
        if log.exists():
            with log.open("r", encoding="utf-8") as f:
                f.seek(self.log_position)
                text = f.read()
                self.log_position = f.tell()
            if text.strip():
                self.progress.emit(text.strip())

    def read_progress(self):
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        if text:
            self.progress.emit(text)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.poll.stop()
            self.finished.emit(None, self.process.errorString())
            self.folder.cleanup()

    def cancel(self):
        self.cancelled = True
        pid = int(self.process.processId())
        if pid and os.name == "nt":
            # Kill only this owned compiler tree, including a running msdfgen child.
            self.terminator = QProcess(self)
            self.terminator.start("taskkill", ["/PID", str(pid), "/T", "/F"])
        else:
            self.process.kill()

    def complete(self, code, status):
        self.poll.stop()
        target, error = None, ""
        try:
            self.read_log()
            self.read_progress()
            if self.cancelled:
                error = "Build cancelled; exported DDS was preserved"
            elif code != 0 or status != QProcess.ExitStatus.NormalExit:
                report = self.output / "report.txt"
                error = (
                    report.read_text(encoding="utf-8")
                    if report.exists()
                    else "Build failed; see diagnostics"
                )
            else:
                self.progress.emit("Publishing verified DDS")
                target = publish_package(self.output, self.destination)
        except Exception as e:
            error = str(e)
        finally:
            self.finished.emit(target, error)
            self.folder.cleanup()
