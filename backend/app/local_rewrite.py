"""Single local-provider call, with no network I/O inside a database transaction."""
import asyncio
import json
import time
from fastapi import HTTPException
from .db import transaction
from .rewrite_jobs import lock_task, require_current, store_candidates, update_execution, fail_task
from .rewrite_context import frozen_context


def run_local_rewrite(engine, task_id, provider):
    started = time.monotonic()
    evidence = {"executor": "openai-compatible", "provider": "llm", "model": provider.model,
                "completed": False, "generation_calls": 0}
    try:
        with transaction(engine) as db:
            task, doc = lock_task(db, task_id)
            require_current(db, task, doc)
            snapshot, instruction = json.loads(task.snapshot), task.instruction
            context = frozen_context(task)
        # The instance, model and snapshot were fixed when the request was accepted.
        # No context/conversation re-read, process execution or fallback occurs here.
        evidence["generation_calls"] = 1

        async def generate():
            call = provider.rewrite(snapshot["evidence"]["text"], instruction, context=context) if context is not None else provider.rewrite(snapshot["evidence"]["text"], instruction)
            return await asyncio.wait_for(call,
                                          timeout=provider.timeout + 5)

        pair = asyncio.run(generate())
        evidence.update(completed=True, duration_ms=round((time.monotonic() - started) * 1000))
        with transaction(engine) as db:
            task, doc = lock_task(db, task_id)
            require_current(db, task, doc)
            store_candidates(db, task, pair)
            task.status, task.error = "ready", ""
            update_execution(task, evidence, "ready")
        # Only a committed transaction may expose ready + two candidates.
    except HTTPException as exc:
        fail_task(engine, task_id, str(exc.detail), {**evidence, "error_status": exc.status_code})
    except TimeoutError:
        fail_task(engine, task_id, "AI 후보 생성 제한 시간을 초과했습니다. 문서는 변경되지 않았습니다.", evidence)
    except Exception:
        # Raw exceptions can contain URLs, credentials, prompts or model responses.
        fail_task(engine, task_id, "AI 후보 생성 또는 보관에 실패했습니다. 문서는 변경되지 않았습니다.", evidence)
