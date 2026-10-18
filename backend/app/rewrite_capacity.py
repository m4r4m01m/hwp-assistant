"""Per-app/process admission, no queue. Logical task status does not release resources."""
import os
from threading import BoundedSemaphore, Lock
from fastapi import HTTPException


def configured_capacity():
    try:
        value = int(os.getenv("HWP_REWRITE_MAX_ACTIVE", "2"))
        if not 1 <= value <= 64:
            raise ValueError()
    except ValueError as exc:
        raise ValueError("HWP_REWRITE_MAX_ACTIVE must be an integer from 1 to 64") from exc
    return BoundedSemaphore(value)


class ExecutionSlot:
    def __init__(self, capacity):
        self.capacity, self.owned, self.started = capacity, False, False
        self.lock = Lock()

    def __enter__(self):
        return self

    def reserve(self):
        if not self.capacity.acquire(blocking=False):
            raise HTTPException(429, "후보 생성 실행이 사용 중입니다. 실행 종료 후 다시 요청하세요. 문서는 변경되지 않았습니다.")
        self.owned = True

    def release(self):
        with self.lock:
            if self.owned:
                self.owned = False
                self.capacity.release()

    def start(self, thread_type, target, args):
        def execute(*arguments):
            try:
                target(*arguments)
            finally:
                # Includes provider client/CLI cleanup; never released by cancel or ready.
                self.release()
        thread_type(target=execute, args=args, daemon=True).start()
        self.started = True

    def __exit__(self, *_):
        if not self.started:
            self.release()  # Includes task commit and dispatch failures.
