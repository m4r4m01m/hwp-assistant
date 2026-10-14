"""Stateless block previews/checks; publication remains the ordinary version save."""
import base64
import json
from fastapi import Header, HTTPException, Request
from pydantic import ValidationError
from .auth import require_access, require_lease_owner
from .block_contract import MAX_JSON, CheckBlock, PreviewBlock, VerifyBlock, catalog, decoded, frozen_hash, selected_block
from .block_hwpx import compose, verify_engine_output
from .db import transaction
from .hwpx import digest


async def bounded_input(request, model):
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_JSON:
        raise HTTPException(413, "블록 요청 JSON은 30MB 이하여야 합니다.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_JSON: raise HTTPException(413, "블록 요청 JSON은 30MB 이하여야 합니다.")
    try:
        value = json.loads(bytes(data))
        return model.model_validate(value)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise HTTPException(422, "유효한 블록 JSON 입력이 필요합니다.") from exc
    except ValidationError as exc:
        raise HTTPException(422, [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                                  for error in exc.errors()]) from exc


def install_document_blocks(app, check_lease):
    engine = app.state.engine

    def check_access(doc_id, base_version, user, token):
        with transaction(engine) as db:
            doc = require_access(db, doc_id, user, "editor", lock=True, fresh=True)
            require_lease_owner(db, doc, user, fresh=True)
            check_lease(doc, token)
            if doc.current_version != base_version:
                raise HTTPException(409, "문서의 저장 기준이 변경되었습니다. 현재 문서를 다시 확인하세요.")

    @app.get("/api/document-blocks")
    def block_catalog():
        return catalog()

    @app.post("/api/documents/{doc_id}/block-preview")
    async def preview(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, PreviewBlock)
        check_access(doc_id, value.base_version, request.state.user, x_edit_session)
        spec = selected_block(value)
        source = decoded(value.source_base64)
        output, plan = compose(source, value, spec)
        check_access(doc_id, value.base_version, request.state.user, x_edit_session)
        standard = catalog()
        return {"request_id": str(value.request_id), "input_sha256": frozen_hash(value),
                "source_sha256": value.source_sha256, "output_base64": base64.b64encode(output).decode(),
                "output_sha256": digest(output), "plan": plan, "target": value.target.model_dump(),
                "block": {key: spec[key] for key in ("id", "revision", "sha256")},
                "standard_reference_sha256": standard["standard_reference_sha256"],
                "standard_profile_sha256": standard["standard_profile_sha256"]}

    @app.post("/api/documents/{doc_id}/block-verify")
    async def verify(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, VerifyBlock)
        check_access(doc_id, value.base_version, request.state.user, x_edit_session)
        spec = selected_block(value)
        source = decoded(value.source_base64)
        expected, _ = compose(source, value, spec)
        output = decoded(value.engine_output_base64)
        verify_engine_output(source, expected, output)
        check_access(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"request_id": str(value.request_id), "input_sha256": frozen_hash(value),
                "source_sha256": value.source_sha256, "output_sha256": digest(output), "verified": True}

    @app.post("/api/documents/{doc_id}/block-check")
    async def check(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, CheckBlock)
        check_access(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"ok": True, "base_version": value.base_version}
