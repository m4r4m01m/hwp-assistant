"""Durable bot inbox and bounded notifications; never resumes a model run.

A committed report UUID is reused after a lost response. The existing report
executor owns generation. Ambiguous outbound delivery is visible in /hwp 내요청;
it is not automatically replayed as a second model request or duplicate DM.
"""
import asyncio
import json
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .account_settings import engine_context
from .audit import actor_context
from .auth import require_access
from .db import ReportTask, transaction
from .mattermost import configured, instance, linked_account, mapping_lock
from .mattermost_client import BotClient, safe_text
from .mattermost_store import MattermostRequest


TERMINAL = {"completed", "cancelled", "failed", "suppressed", "notify_failed"}


def check_link(db, config, row):
    if row.instance_id != instance(config) or row.team_id != config.team_id:
        raise HTTPException(403, "연결이 변경되었습니다.")
    link = linked_account(db, config, row.mattermost_user_id)
    if link.user_id != row.user_id or link.generation != row.generation:
        raise HTTPException(403, "연결이 변경되었습니다.")


def claim(engine, config, request_id):
    with transaction(engine) as db:
        mapping_lock(db)
        row = db.get(MattermostRequest, request_id, with_for_update=True)
        if not row or row.status in TERMINAL or row.next_attempt_at > time.time():
            return None
        if row.status == "sending":
            # Process may have died after Mattermost accepted the message.
            row.status, row.outcome = "notify_failed", "delivery_uncertain"
            return None
        try:
            check_link(db, config, row)
        except HTTPException:
            row.status, row.outcome, row.payload = "suppressed", "link_changed", ""
            return None
        row.status, row.next_attempt_at = "processing", time.time() + 90
        return {key: getattr(row, key) for key in ("id", "user_id", "mattermost_user_id", "kind", "payload", "generation")}


def prepare_report(app, config, item):
    from .reports import CreateReport, create_report_task, fail_report
    from .report_errors import ReportGenerationError
    def guard(db):
        mapping_lock(db)
        row = db.get(MattermostRequest, item["id"], with_for_update=True)
        if not row or row.status in TERMINAL:
            raise HTTPException(409, "요청이 종료됐습니다.")
        check_link(db, config, row)
        row.status = "waiting"
    actor = actor_context.set(item["user_id"])
    context = engine_context.set(app.state.engine)
    try:
        # Poll a previously admitted task without reserving AI capacity again.
        # In particular, a later ACL/config change cannot regenerate that task.
        with transaction(app.state.engine) as db:
            guard(db)
            report = db.get(ReportTask, item["id"], with_for_update=True)
            if report:
                if report.owner_id != item["user_id"]:
                    raise HTTPException(404, "작업을 확인할 수 없습니다.")
                if report.status == "running" and time.time() - report.created_at > 300:
                    fail_report(report, ReportGenerationError("timeout"))
                return report.status
        create_report_task(app, CreateReport.model_validate_json(item["payload"]), item["user_id"], admission_guard=guard)
        with transaction(app.state.engine) as db:
            report = db.get(ReportTask, item["id"], with_for_update=True)
            if not report or report.owner_id != item["user_id"]:
                raise HTTPException(404, "작업을 확인할 수 없습니다.")
            if report.status == "running" and time.time() - report.created_at > 300:
                fail_report(report, ReportGenerationError("timeout"))
            return report.status
    finally:
        actor_context.reset(actor)
        engine_context.reset(context)


def mark_waiting(engine, request_id):
    with transaction(engine) as db:
        row = db.get(MattermostRequest, request_id, with_for_update=True)
        if row and row.status not in TERMINAL:
            row.status, row.next_attempt_at = "waiting", time.time() + 2


def compose_message(engine, config, item, result=None, error=None):
    """Recheck the live account and document versions just before delivery."""
    with transaction(engine) as db:
        mapping_lock(db)
        row = db.get(MattermostRequest, item["id"], with_for_update=True)
        if not row or row.status in TERMINAL:
            return None
        try:
            check_link(db, config, row)
        except HTTPException:
            row.status, row.outcome, row.payload = "suppressed", "link_changed", ""
            return None
        if error:
            message = "요청을 완료하지 못했습니다. " + safe_text(error, 250)
            row.outcome = "request_failed"
        elif row.kind == "draft":
            report = db.get(ReportTask, row.id)
            if not report or report.owner_id != row.user_id:
                return None
            if report.status == "running":
                return None
            label = {"ready": "초안 내용이 준비됐습니다. 웹에서 내용을 검토한 뒤 서식 미리보기·저장을 진행하세요.",
                     "failed": "초안 생성에 실패했습니다. 웹에서 작업 상태를 확인하세요.",
                     "cancelled": "초안 생성이 취소됐습니다."}.get(report.status, "작업 상태를 확인하세요.")
            message = f"{label}\n[초안 작업 열기]({config.public_app_url}?report={report.id})"
            row.outcome = "report_" + report.status
        else:
            lines = ["**관련 문서 검색 결과**"]
            count = 0
            for hit in (result or {}).get("results", []):
                try:
                    doc = require_access(db, hit["document_id"], row.user_id, fresh=True)
                except HTTPException:
                    continue
                if doc.current_version != hit["version_id"]:
                    continue
                count += 1
                lines.append(f"{count}. [{safe_text(doc.name, 160)}]({config.public_app_url}?document={doc.id}&version={hit['version_id']})")
                lines.append("   " + safe_text(hit["excerpt"], 450))
                if hit.get("content_partial"):
                    lines.append("   일부 텍스트만 검색에 반영된 문서입니다.")
            if not count:
                lines.append("현재 확인 가능한 관련 문서를 찾지 못했습니다. 표현을 바꾸거나 검색 자료 갱신 후 다시 시도하세요.")
            if not (result or {}).get("semantic_available"):
                lines.append("의미 검색을 사용할 수 없어 제목·본문 단어 검색 결과를 보여드립니다.")
            if (result or {}).get("indexing"):
                lines.append("일부 문서의 검색 자료를 갱신 중입니다.")
            if (result or {}).get("warning"):
                lines.append(safe_text(result["warning"], 350))
            if (result or {}).get("has_more") or (result or {}).get("search_limited"):
                lines.append("추가 결과가 있을 수 있습니다. 검색어를 구체적으로 입력하면 범위를 좁힐 수 있습니다.")
            message = "\n".join(lines)
            row.outcome = "search_complete"
        message += f"\n요청번호: `{row.id}`"
        row.status, row.next_attempt_at = "sending", time.time() + 90
        row.attempts += 1
        return message


def delivery_receipt(engine, request_id, post_id):
    with transaction(engine) as db:
        row = db.get(MattermostRequest, request_id, with_for_update=True)
        if not row or row.status in TERMINAL:
            return
        if row.status != "sending":
            # Bot/DM preflight failed before a post was attempted. Stop rather
            # than leaving the same request active indefinitely on bad config.
            row.status, row.outcome = "notify_failed", "delivery_preflight_failed"
            row.attempts += 1
            return
        row.status = ({"request_failed": "failed", "report_failed": "failed",
                       "report_cancelled": "cancelled"}.get(row.outcome, "completed") if post_id else "notify_failed")
        row.delivered_post_id = post_id
        if not post_id:
            row.outcome = "delivery_uncertain"
        # Preserve the frozen report request until it finishes; final report data
        # lives in ReportTask. Search receipts retain no extracted document text.


async def process_request(app, request_id, config=None):
    config = config or configured()
    item = await run_in_threadpool(claim, app.state.engine, config, request_id)
    if item is None:
        return
    result, error = None, None
    try:
        if item["kind"] == "draft":
            state = await run_in_threadpool(prepare_report, app, config, item)
            if state == "running":
                await run_in_threadpool(mark_waiting, app.state.engine, request_id)
                return
        else:
            from .document_search import search_documents
            value = json.loads(item["payload"])
            result = await run_in_threadpool(search_documents, app.state.engine, app.state.folder,
                                             item["user_id"], value["query"], limit=5)
    except HTTPException as exc:
        error = str(exc.detail)
    except Exception:
        error = "검색·AI 연결 상태를 확인한 뒤 새 명령으로 요청하세요."
    async def before_send():
        return await run_in_threadpool(compose_message, app.state.engine, config, item, result, error)
    try:
        post_id = await BotClient(config).send_dm(item["mattermost_user_id"], "", before_send=before_send)
    except Exception:
        post_id = None
    await run_in_threadpool(delivery_receipt, app.state.engine, request_id, post_id)


async def bot_loop(app, stop_event):
    # Single dispatcher owns delivery leases. Persistent rows recover pre-send
    # interruption without inventing AI progress or automatically re-running AI.
    while not stop_event.is_set():
        try:
            config = configured()
            with Session(app.state.engine) as db:
                ids = list(db.scalars(select(MattermostRequest.id).where(
                    MattermostRequest.status.not_in(TERMINAL), MattermostRequest.next_attempt_at <= time.time())
                    .order_by(MattermostRequest.created_at).limit(4)))
            for request_id in ids:
                if stop_event.is_set():
                    break
                await process_request(app, request_id, config)
        except Exception:
            # DB/transport/config failures do not terminate the app or disclose content.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)
        except TimeoutError:
            pass
