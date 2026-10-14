"""Bounded stdio transport for one report, reusing the CLI tree ownership guard."""
import json
import os
import queue
import signal
import subprocess
import threading
import time
from .cli_process import WindowsJob


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
            if not output:
                # Drain diagnostic output without retaining credentials or unlimited logs.
                while pipe.read(4096):
                    if self.stop.is_set(): break
                return
            while not self.stop.is_set():
                line = pipe.readline(1048577)
                if not line: break
                if len(line) > 1048576:
                    raise RuntimeError("Codex event exceeded the report stream limit")
                self._put(json.loads(line))
        except Exception:
            self._put(RuntimeError("Codex report stream could not be decoded"))
        finally:
            if output: self._put(None)

    def send(self, value):
        done, errors = threading.Event(), []
        def write():
            try:
                self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
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

    def receive(self):
        while True:
            remaining = self._check()
            try:
                value = self.messages.get(timeout=min(.25, remaining))
            except queue.Empty:
                continue
            if isinstance(value, Exception): raise value
            if value is None: raise RuntimeError("Codex closed before report completion")
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
                self.process.wait()
                for reader in self.readers: reader.join()
                for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                    try: pipe.close()
                    except (BrokenPipeError, OSError): pass
