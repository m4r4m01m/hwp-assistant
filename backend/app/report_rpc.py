"""Bounded stdio transport for one report, reusing the CLI tree ownership guard."""
import json
import codecs
import os
import queue
import selectors
import signal
import subprocess
import threading
import time
from .cli_process import WindowsJob
from .report_errors import ReportGenerationError


class ReportStopped(RuntimeError):
    pass


class ReportRpc:
    def __init__(self, args, *, environment, cwd, timeout, active=lambda: True):
        self.args, self.environment, self.cwd = args, environment, cwd
        self.deadline = time.monotonic() + timeout
        self.active = active
        self.checked_at = 0
        self.messages = queue.Queue(maxsize=64)
        self.stop = threading.Event()
        self.readers = []
        self.process = self.job = None

    def __enter__(self):
        try:
            self.job = WindowsJob() if os.name == "nt" else None
            options = {"creationflags": 4 | subprocess.CREATE_NO_WINDOW} if self.job else {"start_new_session": True}
            self.process = subprocess.Popen(self.args, cwd=self.cwd, env=self.environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="strict", **options)
            if self.job:
                self.job.attach_and_resume(self.process)
            else:
                # An escaped descendant may retain inherited pipe handles even
                # after the owned process group exits. Poll raw nonblocking FDs
                # so cancellation never waits for that unrelated EOF.
                for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                    os.set_blocking(pipe.fileno(), False)
            for pipe, output in ((self.process.stdout, True), (self.process.stderr, False)):
                reader = threading.Thread(target=self._read, args=(pipe, output), daemon=True)
                reader.start(); self.readers.append(reader)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _put(self, value):
        while not self.stop.is_set():
            try:
                self.messages.put(value, timeout=.1)
                return
            except queue.Full:
                pass

    def _read(self, pipe, output):
        try:
            if os.name != "nt":
                self._read_posix(pipe, output)
                return
            if not output:
                # Drain diagnostic output without retaining credentials or unlimited logs.
                while pipe.read(4096):
                    if self.stop.is_set(): break
                return
            while not self.stop.is_set():
                line = pipe.readline(1048577)
                if not line: break
                if len(line) > 1048576:
                    raise ReportGenerationError("output_limit")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ReportGenerationError("protocol_error")
                self._put(event)
        except ReportGenerationError as exc:
            self._put(exc)
        except Exception:
            self._put(ReportGenerationError("protocol_error"))
        finally:
            pipe.close()
            if output: self._put(None)

    def _read_posix(self, pipe, output):
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        pending = ""
        with selectors.DefaultSelector() as selector:
            selector.register(pipe, selectors.EVENT_READ)
            while not self.stop.is_set():
                if not selector.select(.1):
                    continue
                chunk = os.read(pipe.fileno(), 65536)
                if output:
                    pending += decoder.decode(chunk, final=not chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        if len(line) > 1048576:
                            raise ReportGenerationError("output_limit")
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ReportGenerationError("protocol_error")
                        self._put(event)
                    if len(pending) > 1048576:
                        raise ReportGenerationError("output_limit")
                if not chunk:
                    if output and pending:
                        event = json.loads(pending)
                        if not isinstance(event, dict):
                            raise ReportGenerationError("protocol_error")
                        self._put(event)
                    return

    def send(self, value):
        done, errors = threading.Event(), []
        def write():
            try:
                text = json.dumps(value, ensure_ascii=False) + "\n"
                if os.name == "nt":
                    self.process.stdin.write(text)
                    self.process.stdin.flush()
                else:
                    remaining = memoryview(text.encode("utf-8"))
                    with selectors.DefaultSelector() as selector:
                        selector.register(self.process.stdin, selectors.EVENT_WRITE)
                        while remaining and not self.stop.is_set():
                            if selector.select(.1):
                                try:
                                    count = os.write(self.process.stdin.fileno(), remaining)
                                    remaining = remaining[count:]
                                except BlockingIOError:
                                    continue
            except Exception as exc: errors.append(exc)
            finally: done.set()
        writer = threading.Thread(target=write, daemon=True)
        writer.start(); self.readers.append(writer)
        while not done.wait(.05): self._check()
        writer.join(); self.readers.remove(writer)
        if errors: raise errors[0]

    def _check(self):
        now = time.monotonic()
        if now - self.checked_at >= .25:
            if not self.active(): raise ReportStopped("Report reception stopped")
            self.checked_at = now
        remaining = self.deadline - now
        if remaining <= 0: raise TimeoutError("Report stream timed out")
        return remaining

    def receive(self, *, on_idle=None):
        while True:
            remaining = self._check()
            try:
                value = self.messages.get(timeout=min(.25, remaining))
            except queue.Empty:
                if on_idle is not None: on_idle()
                continue
            if isinstance(value, Exception): raise value
            if value is None: raise ReportGenerationError("connection_lost")
            return value

    def __exit__(self, *args):
        self.stop.set()
        try:
            if self.job: self.job.close()
            elif self.process:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
        finally:
            if self.process:
                if self.process.poll() is None: self.process.kill()
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired: pass
                cleanup_deadline = time.monotonic() + 2
                for reader in self.readers:
                    reader.join(timeout=max(0, cleanup_deadline - time.monotonic()))
                for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                    # Reader-owned pipes close in their finally block. Do not
                    # contend on a TextIO lock if an OS reader is still stuck.
                    if any(reader.is_alive() for reader in self.readers):
                        continue
                    try: pipe.close()
                    except (BrokenPipeError, OSError): pass
