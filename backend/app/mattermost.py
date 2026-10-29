"""Explicit Mattermost ingress and user-confirmed account links.

Only two POST routes have integration authentication. All other API paths keep
the ordinary browser/token fences. The bot never acquires an editing lease.
"""
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from collections import OrderedDict, deque
from threading import Lock
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .accounts import UserAccount
from .audit import record_event
from .db import ReportTask, transaction
from .mattermost_client import BotClient, settings, safe_text, valid_id
from .mattermost_store import MattermostChallenge, MattermostLink, MattermostRequest
from .report_contract import canonical, report_hash

COMMAND_PATH = "/api/integrations/mattermost/command"
DIALOG_PATH = "/api/integrations/mattermost/dialog"
INGRESS = frozenset({("POST", COMMAND_PATH), ("POST", DIALOG_PATH)})
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
HELP = ("**HWP Assistant**\n\n"
        "`/hwp 연결` — 웹 계정 연결\n"
        "`/hwp 검색 찾고 싶은 내용` — 제목·본문 관련 문서 찾기\n"
        "`/hwp 초안` — 작성 입력창 열기\n"
        "`/hwp 초안 작성할 내용` — 기본 양식으로 요청\n"
        "`/hwp 내요청` — 최근 요청과 초안 확인\n"
        "`/hwp 취소 요청번호` — 본인 작업 취소\n"
        "`/hwp 연결해제` — 계정 연결 해제\n\n"
        "검색 결과·완료 안내는 봇과의 개인 메시지로 전달됩니다. 초안은 웹에서 검토 후 저장하세요.")


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def instance(config):
    return fingerprint(config.base_url)


def configured():
    try:
        config = settings()
    except ValueError:
        raise HTTPException(503, "매터모스트 봇 설정을 확인하세요.") from None
    if config is None or os.getenv("HWP_ACCOUNTS_ENABLED") != "1":
        raise HTTPException(404, "매터모스트 봇이 활성화되지 않았습니다.")
    return config


def ephemeral(message):
    return {"response_type": "ephemeral", "text": message}


def mapping_lock(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(706281028)"))


def personal_account(db, user):
    account = db.get(UserAccount, user, populate_existing=True)
    if not account or account.kind != "member" or not account.is_active or account.approval_status != "approved":
        raise HTTPException(403, "승인된 개인 계정으로 연결하세요. 공용 체험 계정은 연결할 수 없습니다.")
    return account


def linked_account(db, config, mm_user):
    link = db.get(MattermostLink, (instance(config), config.team_id, mm_user), populate_existing=True)
    if not link:
        raise HTTPException(403, "먼저 `/hwp 연결`로 서비스의 개인 계정을 연결하세요.")
    personal_account(db, link.user_id)
    return link


class CommandLimiter:
    """Bounded process-local admission, consistent with the one-worker service."""
    def __init__(self):
        self.lock, self.users, self.all = Lock(), OrderedDict(), deque()

    def admit(self, key):
        now = time.monotonic()
        with self.lock:
            while self.all and self.all[0] < now - 60:
                self.all.popleft()
            events = self.users.pop(key, deque())
            while events and events[0] < now - 60:
                events.popleft()
            self.users[key] = events
            while len(self.users) > 2048:
                self.users.popitem(last=False)
            if len(events) >= 20 or len(self.all) >= 120:
                raise HTTPException(429, "요청이 많습니다. 잠시 후 다시 시도하세요.")
            events.append(now); self.all.append(now)


async def bounded_input(request, *, form=False):
    expected = "application/x-www-form-urlencoded" if form else "application/json"
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != expected:
        raise HTTPException(415, "지원하지 않는 요청 형식입니다.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 32768:
            raise HTTPException(413, "요청 크기를 초과했습니다.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        value = unique(parse_qsl(data.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                                 max_num_fields=25)) if form else json.loads(data, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise HTTPException(422, "요청 내용을 확인하세요.") from None


def challenge(engine, config, payload, kind, generation=None):
    token = secrets.token_urlsafe(32)
    with transaction(engine) as db:
        mapping_lock(db)
        # Short-lived challenges contain hashes only; command admission bounds creation.
        db.execute(delete(MattermostChallenge).where(MattermostChallenge.expires_at < time.time()))
        db.add(MattermostChallenge(token_hash=fingerprint(token), instance_id=instance(config),
               team_id=config.team_id, mattermost_user_id=payload["user_id"], channel_id=payload["channel_id"],
               kind=kind, expires_at=time.time() + 300, generation=generation))
    return token


def frozen_report(task_id, prompt, preset="appendix-1", research=False):
    from .reports import CreateReport
    from .report_templates import catalog
    from .visual_standards import builtin_selections, VisualSelection
    templates = catalog()
    template = next(item for item in templates["items"] if item["id"] == templates["default_id"])
    visual = next((item for item in builtin_selections() if item["settings"].get("preset_id") == preset), None)
    if visual is None:
        raise HTTPException(422, "문서 양식을 확인하세요.")
    try:
        value = CreateReport(id=task_id, prompt=prompt, research=research, research_policy="allow_unverified",
                             template=template, visual_standard={key: visual[key] for key in VisualSelection.model_fields})
    except ValueError:
        raise HTTPException(422, "작성할 내용을 5~6,000자로 입력하세요.") from None
    return value.model_dump(mode="json", exclude_none=True)


def enqueue(engine, config, mm_user, external, kind, value, expected_generation):
    """Freeze one user-bound request before any AI work or outbound delivery."""
    payload = canonical(value)
    with transaction(engine) as db:
        mapping_lock(db)
        link = linked_account(db, config, mm_user)
        if link.generation != expected_generation:
            raise HTTPException(409, "계정 연결이 변경됐습니다. 새 명령으로 요청하세요.")
        existing = db.scalar(select(MattermostRequest).where(MattermostRequest.external_key == external))
        if existing:
            if existing.user_id != link.user_id or existing.generation != link.generation or existing.payload_sha256 != report_hash(value):
                raise HTTPException(409, "기존 요청과 다릅니다. 새 명령으로 요청하세요.")
            return existing.id
        active = db.scalars(select(MattermostRequest.id).where(MattermostRequest.user_id == link.user_id,
                             MattermostRequest.status.in_(["accepted", "processing", "waiting"])).limit(5)).all()
        if len(active) >= 5:
            raise HTTPException(429, "진행 중인 요청을 먼저 확인하세요.")
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, external))
        db.add(MattermostRequest(id=request_id, external_key=external, instance_id=instance(config),
               team_id=config.team_id, mattermost_user_id=mm_user, user_id=link.user_id, generation=link.generation,
               kind=kind, payload=payload, payload_sha256=report_hash(value), status="accepted",
               created_at=time.time(), next_attempt_at=0, attempts=0, outcome="accepted"))
        record_event(db, link.user_id, "mattermost.request", "mattermost_request", request_id, details={"mode": kind})
        return request_id


def accepted(request_id):
    return ephemeral(f"요청을 접수했습니다. 결과는 개인 메시지로 안내합니다.\n요청번호: `{request_id}`\n`/hwp 내요청`으로 확인할 수 있습니다.")


def recent_requests(engine, config, mm_user):
    with Session(engine) as db:
        link = linked_account(db, config, mm_user)
        rows = db.scalars(select(MattermostRequest).where(MattermostRequest.user_id == link.user_id,
                          MattermostRequest.instance_id == instance(config), MattermostRequest.generation == link.generation)
                          .order_by(MattermostRequest.created_at.desc()).limit(8)).all()
        lines = ["**최근 내 요청**"]
        labels = {"accepted": "접수", "processing": "처리 중", "waiting": "생성 중", "completed": "완료",
                  "failed": "실패", "cancelled": "취소", "suppressed": "연결 해제", "notify_failed": "알림 전송 확인 필요"}
        for row in rows:
            report = db.get(ReportTask, row.id) if row.kind == "draft" else None
            state = {"running": "생성 중", "ready": "내용 검토 가능", "failed": "생성 실패", "cancelled": "취소"}.get(report.status) if report else None
            title = "초안 작성" if row.kind == "draft" else "문서 검색"
            line = f"- {title} · {state or labels.get(row.status, '처리 중')} · `{row.id}`"
            if state and row.status == "notify_failed":
                line += " · 알림 전송 확인 필요"
            if report and report.owner_id == link.user_id:
                line += f" · [작업 열기]({config.public_app_url}?report={report.id})"
            lines.append(line)
        if not rows:
            lines.append("아직 요청한 작업이 없습니다.")
        return ephemeral("\n".join(lines))


def cancel_request(engine, config, mm_user, request_id):
    try:
        request_id = str(uuid.UUID(request_id))
    except ValueError:
        raise HTTPException(422, "요청번호를 확인하세요.") from None
    with transaction(engine) as db:
        mapping_lock(db)
        link = linked_account(db, config, mm_user)
        row = db.get(MattermostRequest, request_id, with_for_update=True)
        if not row or row.user_id != link.user_id or row.instance_id != instance(config) or row.generation != link.generation:
            raise HTTPException(404, "요청을 찾을 수 없습니다.")
        report = db.get(ReportTask, request_id, with_for_update=True) if row.kind == "draft" else None
        if report and report.owner_id == link.user_id and report.status == "running":
            report.status = "cancelled"
        if not report or report.status == "cancelled":
            row.status, row.outcome = "cancelled", "cancelled"
        record_event(db, link.user_id, "mattermost.cancel", "mattermost_request", request_id)
    return ephemeral("요청 상태를 확인했습니다. 이미 생성된 초안과 저장 문서는 보존됩니다.")


def unlink(db, link):
    record_event(db, link.user_id, "mattermost.unlink", "account", link.user_id)
    db.delete(link)


def install_mattermost(app):
    engine = app.state.engine
    limiter = CommandLimiter()

    @app.get("/api/mattermost/link")
    def link_status(request: Request):
        if os.getenv("HWP_MATTERMOST_BOT_ENABLED") != "1" or not app.state.account_policy.enabled:
            return {"enabled": False, "linked": False}
        config = configured()
        with Session(engine) as db:
            personal_account(db, request.state.user)
            row = db.scalar(select(MattermostLink).where(MattermostLink.instance_id == instance(config),
                            MattermostLink.user_id == request.state.user))
            return {"enabled": True, "linked": bool(row), **({"mattermost_user_id": row.mattermost_user_id} if row else {})}

    @app.post("/api/mattermost/link")
    async def confirm_link(request: Request):
        config = configured()
        body = await bounded_input(request)
        token = body.get("token", "")
        if set(body) != {"token"} or not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise HTTPException(422, "연결 요청을 확인하세요.")
        def confirm():
            with transaction(engine) as db:
                mapping_lock(db)
                personal_account(db, request.state.user)
                value = db.get(MattermostChallenge, fingerprint(token), with_for_update=True)
                if (not value or value.kind != "link" or value.expires_at < time.time()
                        or value.instance_id != instance(config) or value.team_id != config.team_id):
                    raise HTTPException(409, "연결 요청이 만료됐습니다. `/hwp 연결`을 다시 실행하세요.")
                if value.consumed_by and value.consumed_by != request.state.user:
                    raise HTTPException(409, "이미 사용한 연결 요청입니다.")
                old = db.get(MattermostLink, (instance(config), config.team_id, value.mattermost_user_id))
                ours = db.scalar(select(MattermostLink).where(MattermostLink.instance_id == instance(config),
                                 MattermostLink.user_id == request.state.user))
                if (old and old.user_id != request.state.user) or (ours and (
                        ours.mattermost_user_id != value.mattermost_user_id or ours.team_id != config.team_id)):
                    raise HTTPException(409, "기존 연결을 먼저 해제하세요.")
                # A used token cannot recreate a deliberately disconnected mapping.
                if value.consumed_by and (not old or old.generation != value.generation):
                    raise HTTPException(409, "새 연결 요청이 필요합니다.")
                if not old:
                    old = MattermostLink(instance_id=instance(config), team_id=config.team_id,
                                         mattermost_user_id=value.mattermost_user_id, user_id=request.state.user,
                                         generation=str(uuid.uuid4()), created_at=time.time())
                    db.add(old)
                    record_event(db, request.state.user, "mattermost.link", "account", request.state.user)
                value.consumed_by, value.generation = request.state.user, old.generation
                return {"linked": True}
        return await run_in_threadpool(confirm)

    @app.delete("/api/mattermost/link")
    def disconnect(request: Request):
        config = configured()
        with transaction(engine) as db:
            mapping_lock(db)
            personal_account(db, request.state.user)
            row = db.scalar(select(MattermostLink).where(MattermostLink.instance_id == instance(config), MattermostLink.user_id == request.state.user))
            if row:
                unlink(db, row)
        return {"linked": False}

    @app.post(COMMAND_PATH)
    async def command(request: Request):
        config = configured()
        body = await bounded_input(request, form=True)
        supplied = body.get("token", "")
        auth = request.headers.get("authorization", "")
        if auth:
            scheme, _, header_token = auth.partition(" ")
            if scheme.lower() not in {"token", "bearer"} or not secrets.compare_digest(header_token.encode(), config.command_token.encode()):
                raise HTTPException(401, "봇 요청 인증에 실패했습니다.")
            if supplied and not secrets.compare_digest(supplied.encode(), config.command_token.encode()):
                raise HTTPException(401, "봇 요청 인증에 실패했습니다.")
        elif not secrets.compare_digest(supplied.encode(), config.command_token.encode()):
            raise HTTPException(401, "봇 요청 인증에 실패했습니다.")
        if (body.get("team_id") != config.team_id or body.get("command") != "/hwp"
                or not valid_id(body.get("user_id")) or not valid_id(body.get("channel_id"))):
            raise HTTPException(403, "허용된 매터모스트 요청이 아닙니다.")
        limiter.admit((instance(config), body["user_id"]))
        content = body.get("text", "").strip()
        if len(content) > 6200 or any(ord(c) < 32 and c not in "\n\r\t" for c in content):
            return ephemeral("입력 내용을 확인하세요. 최대 6,000자까지 작성할 수 있습니다.")
        verb, _, argument = content.partition(" ")
        verb = {"search": "검색", "draft": "초안", "link": "연결", "unlink": "연결해제", "status": "내요청", "cancel": "취소"}.get(verb, verb)
        try:
            if not content or verb in {"도움말", "help"}:
                return ephemeral(HELP)
            if verb == "연결":
                token = await run_in_threadpool(challenge, engine, config, body, "link")
                return ephemeral(f"[웹에서 개인 계정 연결하기]({config.public_app_url}#mattermost-link={token})\n5분 안에 로그인한 계정을 확인하고 연결 버튼을 누르세요.")
            with Session(engine) as db:
                link = linked_account(db, config, body["user_id"])
                generation = link.generation
            if verb == "연결해제":
                def remove():
                    with transaction(engine) as db:
                        mapping_lock(db)
                        unlink(db, linked_account(db, config, body["user_id"]))
                await run_in_threadpool(remove)
                return ephemeral("계정 연결을 해제했습니다. 기존 초안은 웹의 내 작업에 보존됩니다.")
            if verb == "내요청":
                return await run_in_threadpool(recent_requests, engine, config, body["user_id"])
            if verb == "취소":
                return await run_in_threadpool(cancel_request, engine, config, body["user_id"], argument.strip())
            trigger = body.get("trigger_id", "")
            if not 10 <= len(trigger) <= 2048:
                return ephemeral("요청 식별자를 확인하지 못했습니다. 새 명령을 입력하세요.")
            if verb == "초안" and not argument.strip():
                state = await run_in_threadpool(challenge, engine, config, body, "dialog", generation)
                elements = [
                    {"display_name": "작성 내용", "name": "prompt", "type": "textarea", "min_length": 5, "max_length": 3000},
                    {"display_name": "문서 양식", "name": "preset", "type": "select", "default": "appendix-1", "options": [
                        {"text": "별첨 1 · 기본 보고서", "value": "appendix-1"}, {"text": "별첨 2", "value": "appendix-2"}, {"text": "별첨 3", "value": "appendix-3"}]},
                    {"display_name": "공개자료 조회", "name": "research", "type": "select", "default": "off", "options": [
                        {"text": "입력한 자료로 작성", "value": "off"}, {"text": "공개자료 조회 포함", "value": "on"}]},
                ]
                await BotClient(config).open_dialog(trigger, config.public_app_url + DIALOG_PATH.lstrip("/"), state, elements)
                return ephemeral("초안 작성 입력창을 열었습니다. 작성 완료 후 웹에서 내용을 검토하세요.")
            external = fingerprint("\0".join((instance(config), config.team_id, body["user_id"], trigger)))
            if verb == "검색":
                query = argument.strip()
                if not 2 <= len(query) <= 200:
                    return ephemeral("찾고 싶은 내용을 2~200자로 입력하세요.")
                value, kind = {"query": query}, "search"
            elif verb == "초안":
                task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, external))
                value = await run_in_threadpool(frozen_report, task_id, argument.strip())
                kind = "draft"
            else:
                return ephemeral(HELP)
            request_id = await run_in_threadpool(enqueue, engine, config, body["user_id"], external, kind, value, generation)
            return accepted(request_id)
        except HTTPException as exc:
            return ephemeral(str(exc.detail))
        except Exception:
            # Tokens, prompts, remote errors and full URLs must never be echoed.
            return ephemeral("봇 요청을 처리하지 못했습니다. 잠시 후 `/hwp 내요청`을 확인하세요.")

    @app.post(DIALOG_PATH)
    async def dialog(request: Request):
        config = configured()
        body = await bounded_input(request)
        token = body.get("state", "")
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise HTTPException(401, "입력창 요청 인증에 실패했습니다.")
        with Session(engine) as db:
            value = db.get(MattermostChallenge, fingerprint(token))
            if (not value or value.kind != "dialog" or value.expires_at < time.time()
                    or value.instance_id != instance(config) or value.team_id != config.team_id
                    or body.get("user_id") != value.mattermost_user_id or body.get("channel_id") != value.channel_id
                    or body.get("team_id") != value.team_id or body.get("callback_id") != "hwp-report"):
                raise HTTPException(401, "입력창 요청이 만료됐거나 일치하지 않습니다.")
            link = linked_account(db, config, value.mattermost_user_id)
            if value.generation != link.generation:
                raise HTTPException(409, "계정 연결이 변경됐습니다. 새 입력창을 여세요.")
            mm_user = value.mattermost_user_id
            generation = value.generation
        if body.get("cancelled") is True:
            return {}
        submission = body.get("submission")
        if (not isinstance(submission, dict) or not isinstance(submission.get("prompt"), str)
                or not isinstance(submission.get("preset"), str) or not isinstance(submission.get("research"), str)
                or submission.get("preset") not in {"appendix-1", "appendix-2", "appendix-3"}
                or submission.get("research") not in {"on", "off"}):
            return {"errors": {"prompt": "입력 내용을 확인하세요."}}
        if not 5 <= len(submission["prompt"].strip()) <= 3000:
            return {"errors": {"prompt": "입력창에는 5~3,000자로 작성하세요. 더 긴 내용은 `/hwp 초안 내용`으로 요청할 수 있습니다."}}
        external = fingerprint("dialog\0" + token)
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, external))
        try:
            report = await run_in_threadpool(frozen_report, task_id, submission["prompt"].strip(), submission["preset"], submission["research"] == "on")
            await run_in_threadpool(enqueue, engine, config, mm_user, external, "draft", report, generation)
            return {}
        except HTTPException as exc:
            return {"errors": {"prompt": str(exc.detail)}}
