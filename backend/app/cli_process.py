"""Bound one owned CLI tree to a synchronous call, including timeout cleanup.

Windows: assign the suspended child to a private kill-on-close Job Object before
resuming its initial thread. Never enumerate/terminate unrelated processes.
This is execution cleanup, not a task queue or remote-inference cancellation.
"""
import os
import signal
import subprocess
import time


class WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.api = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, c.c_void_p], w.BOOL),
            "Thread32Next": ([w.HANDLE, c.c_void_p], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        class Limits(c.Structure):
            _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64),
                        ("flags", w.DWORD), ("min_ws", c.c_size_t), ("max_ws", c.c_size_t),
                        ("active", w.DWORD), ("affinity", c.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [("basic", Limits), ("io", c.c_uint64 * 6), ("memory", c.c_size_t * 4)]
        class Accounting(c.Structure):
            _fields_ = [("times", c.c_int64 * 4), ("faults", w.DWORD),
                        ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]
        self.accounting = Accounting
        self.handle = self.api.CreateJobObjectW(None, None)
        self.check(self.handle)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self.check(self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)))
        except BaseException:
            self.api.CloseHandle(self.handle)
            raise

    def check(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    def attach_and_resume(self, process):
        c = self.c
        from ctypes import wintypes as w
        self.check(self.api.AssignProcessToJobObject(self.handle, int(process._handle)))
        class Thread(c.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("id", w.DWORD),
                        ("owner", w.DWORD), ("base", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = Thread(); entry.size = c.sizeof(entry)
            found = self.api.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.owner == process.pid:
                    handle = self.check(self.api.OpenThread(2, False, entry.id))  # SUSPEND_RESUME
                    try:
                        if self.api.ResumeThread(handle) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                        return
                    finally:
                        self.api.CloseHandle(handle)
                found = self.api.Thread32Next(snapshot, c.byref(entry))
            raise RuntimeError("Owned suspended CLI thread was not found")
        finally:
            self.api.CloseHandle(snapshot)

    def close(self):
        try:
            self.check(self.api.TerminateJobObject(self.handle, 1))
            # Keep the execution slot until all owned processes have actually exited.
            info = self.accounting()
            while True:
                self.check(self.api.QueryInformationJobObject(self.handle, 1, self.c.byref(info), self.c.sizeof(info), None))
                if info.active == 0:
                    break
                time.sleep(.01)
        finally:
            self.api.CloseHandle(self.handle)


def run_process(args, *, input=None, capture_output=False, timeout=None, **kwargs):
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    job = WindowsJob() if os.name == "nt" else None
    if job:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 4 | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    process = None
    try:
        process = subprocess.Popen(args, **kwargs)
        if job:
            job.attach_and_resume(process)
        stdout, stderr = process.communicate(input, timeout=timeout)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    finally:
        try:
            if job:
                job.close()
            elif process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if process:
                if process.poll() is None:
                    process.kill()  # Also handles failure before Windows job assignment.
                process.communicate()  # Join reader threads and close all pipes before returning.
