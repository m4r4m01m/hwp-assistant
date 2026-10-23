"""Bound immediate HTTP model calls in the existing single-worker deployment."""
import os
import time
from collections import deque
from contextlib import contextmanager
from threading import Lock
from fastapi import HTTPException


class UserAIBudget:
    def __init__(self):
        try:
            self.limit = int(os.getenv("HWP_AI_REQUESTS_PER_MINUTE", "20"))
            if not 1 <= self.limit <= 120:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("HWP_AI_REQUESTS_PER_MINUTE must be between 1 and 120") from exc
        self.lock, self.active, self.recent = Lock(), set(), {}

    @contextmanager
    def reserve(self, user):
        with self.lock:
            now = time.monotonic()
            recent = self.recent.setdefault(user, deque())
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if user in self.active:
                raise HTTPException(429, "AI 응답을 생성 중입니다. 완료된 뒤 다시 요청하세요.", headers={"Retry-After": "2"})
            if len(recent) >= self.limit:
                raise HTTPException(429, "AI 요청이 잠시 많습니다. 1분 뒤 다시 요청하세요.", headers={"Retry-After": "60"})
            recent.append(now)
            self.active.add(user)
        try:
            yield
        finally:
            with self.lock:
                self.active.discard(user)
