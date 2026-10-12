"""Owned native-preview process with cancellable, bounded request I/O."""

import os
import struct
import subprocess
import threading
import time


class PreviewCancelled(RuntimeError):
    """The owner stopped the preview while a request was pending."""


class PreviewProcess:
    def __init__(self, command, error_file, timeout=30):
        self.command = command
        self.error_file = error_file
        self.timeout = timeout
        self.process = None
        self.condition = threading.Condition()
        self.deadline = None
        self.stopping = False
        self.timed_out = False
        self.watchdog = threading.Thread(target=self.watch, daemon=True)
        self.watchdog.start()

    def watch(self):
        with self.condition:
            while not self.stopping:
                if self.deadline is None:
                    self.condition.wait()
                else:
                    remaining = self.deadline - time.monotonic()
                    if remaining > 0:
                        self.condition.wait(remaining)
                    else:
                        self.timed_out = True
                        self.deadline = None
                        self.process.kill()

    def abort(self):
        """May run on the UI thread; releases any worker blocked in pipe I/O."""
        with self.condition:
            self.stopping = True
            if self.process is not None and self.deadline is not None:
                self.process.kill()
            self.condition.notify_all()

    def discard(self, graceful=False):
        with self.condition:
            self.deadline = None
            process, self.process = self.process, None
            self.condition.notify_all()
        if process is not None:
            if not graceful:
                process.kill()
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass  # The child may have exited before consuming the pending request.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()

    def close(self):
        """Call after the request worker has stopped."""
        self.abort()
        self.watchdog.join()
        self.discard(graceful=True)

    def exchange(self, packet, expected_bytes):
        with self.condition:
            if self.stopping:
                raise PreviewCancelled("Preview closed")
            if self.process is None:
                self.process = subprocess.Popen(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self.error_file,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            process = self.process
            self.timed_out = False
            self.deadline = time.monotonic() + self.timeout
            self.condition.notify_all()

        def read(count):
            chunks = []
            while count:
                part = process.stdout.read(count)
                if not part:
                    if self.timed_out:
                        raise TimeoutError("Native preview timed out")
                    self.error_file.seek(0)
                    raise RuntimeError(
                        "D3D11 preview failed: "
                        + self.error_file.read().decode("utf-8", errors="replace")
                    )
                chunks.append(part)
                count -= len(part)
            return b"".join(chunks)

        try:
            process.stdin.write(packet)
            process.stdin.flush()
            count = struct.unpack("<I", read(4))[0]
            if count != expected_bytes:
                raise ValueError("Invalid GPU frame length")
            return read(count)
        except (OSError, ValueError, RuntimeError):
            self.discard()
            if self.stopping:
                raise PreviewCancelled("Preview closed") from None
            raise
        finally:
            with self.condition:
                self.deadline = None
                self.condition.notify_all()
