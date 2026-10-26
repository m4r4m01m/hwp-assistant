"""Shared publication guards for task-scoped Codex and configured local model jobs."""
import json
import secrets
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from .auth import require_access, require_lease_owner
from .db import Document, RewriteTask, RewriteCandidate, transaction
from .hwpx import digest
from .rewrite_contract import CandidatePair

MAX_JOB_SECONDS = 600


def effective_status(task):
    # Read-only expiry also covers a server crash between task commit and dispatch.
    # Never restart a model call from a poll/retry, or delete a partial candidate.
    return "expired" if task.status == "running" and time.time() - task.created_at >= MAX_JOB_SECONDS else task.status


def lock_task(db, task_id):
    # Call before staging writes. Refuse to discard/flush a caller's pending work.
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Publication locking requires a clean Session before staging changes")
    with db.no_autoflush:
        task = db.get(RewriteTask, task_id, populate_existing=True)
        if not task:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
        doc = db.scalar(select(Document).where(Document.id == task.document_id)
                        .with_for_update().execution_options(populate_existing=True))
        db.refresh(task)  # Another publisher may have finished while we waited for the document lock.
    return task, doc


def require_current(db, task, doc):
    require_access(db, doc.id, task.requested_by, "editor", fresh=True)
    if task.status != "running" or time.time() - task.created_at >= MAX_JOB_SECONDS:
        raise HTTPException(409, "후보 생성 작업이 종료되었거나 제한 시간을 초과했습니다.")
    if doc.current_version != task.base_version:
        raise HTTPException(409, "후보 생성 중 기준 버전이 변경되었습니다. 새 원문으로 다시 요청하세요.")
    execution = json.loads(task.execution)
    if execution.get("provider") == "codex":
        from .account_settings import registered_codex_allowed
        if not registered_codex_allowed(task.requested_by, db.get_bind()):
            raise HTTPException(403, "계정 또는 AI 이용 권한이 변경되어 후보 생성을 중단했습니다.")
    lease_hash = execution.get("_lease_sha256")
    if lease_hash:
        require_lease_owner(db, doc, task.requested_by, fresh=True)
        if doc.lease_until <= time.time() or not secrets.compare_digest(lease_hash, digest((doc.lease_token or "").encode())):
            raise HTTPException(409, "후보 생성 중 편집 세션이 만료되거나 변경되었습니다.")


def store_candidates(db, task, pair: CandidatePair):
    # Validate even internal providers; typed mocks are not a trust boundary.
    pair = CandidatePair.model_validate(pair.model_dump())
    ids = []
    for ordinal, item in enumerate(pair.candidates, 1):
        candidate_id = str(uuid.uuid5(uuid.UUID(task.id), f"candidate-{ordinal}"))
        old = db.get(RewriteCandidate, candidate_id)
        if old and (old.replacement, old.reason) != (item.replacement, item.reason):
            raise HTTPException(409, "이미 보관된 후보를 덮어쓸 수 없습니다.")
        if not old:
            db.add(RewriteCandidate(id=candidate_id, task_id=task.id, ordinal=ordinal, **item.model_dump()))
        ids.append(candidate_id)
    return ids


def update_execution(task, evidence, outcome):
    task.execution = json.dumps({**json.loads(task.execution), **evidence,
                                 "outcome": outcome, "finished_at": time.time()}, ensure_ascii=False)


def invalidate_running(db, document_id, reason, *, user=None):
    """Caller holds document lock in its ACL/lease transaction. Never remove history."""
    query = select(RewriteTask).where(RewriteTask.document_id == document_id, RewriteTask.status == "running")
    if user is not None:
        query = query.where(RewriteTask.requested_by == user)
    for task in db.scalars(query):
        task.status, task.error = "failed", reason
        update_execution(task, {"completed": False, "invalidated": True}, "failed")


def fail_task(engine, task_id, message, evidence=None):
    with transaction(engine) as db:
        task, _ = lock_task(db, task_id)
        if task.status != "running":
            return
        task.status, task.error = "failed", message
        update_execution(task, {**(evidence or {}), "completed": False}, "failed")


def finish_codex(engine, task_id, evidence, error):
    try:
        with transaction(engine) as db:
            task, doc = lock_task(db, task_id)
            require_current(db, task, doc)
            candidates = list(db.scalars(select(RewriteCandidate).where(RewriteCandidate.task_id == task_id)
                                        .order_by(RewriteCandidate.ordinal)))
            if error:
                raise HTTPException(502, error)
            CandidatePair.model_validate({"candidates": [{"replacement": c.replacement, "reason": c.reason} for c in candidates]})
            task.status, task.error = "ready", ""
            update_execution(task, evidence, "ready")
    except HTTPException as exc:
        fail_task(engine, task_id, str(exc.detail), evidence)
    except ValueError:
        fail_task(engine, task_id, "유효한 후보 두 개가 보관되지 않았습니다.", evidence)


def public_execution(task):
    # Old jobs are Codex jobs. Do not relabel their provenance when configuration changes.
    return {"provider": "codex", **{key: value for key, value in json.loads(task.execution).items()
                                    if not key.startswith("_")}}
