"""Shared verification for fenced report text, before layout or manual edits."""
from fastapi import HTTPException
from .hwpx import digest
from .report_hwpx import paragraphs
from .document_standard import format_signature


def verify_output(data, value, approval, scaffold_data):
    plan = approval["plan"]
    if len(plan) != len(value.receipts):
        raise HTTPException(422, "모든 승인 문단의 편집기 영수증이 필요합니다.")
    previous = None
    for item, receipt in zip(plan, value.receipts):
        target = {"kind": "body_paragraph", "section": 0, "paragraph": item["paragraph"], "charOffset": 0, "length": len(item["before"])}
        if (receipt.get("schemaVersion") != 1 or receipt.get("operation") != "apply"
            or receipt.get("commandId") != item["command_id"] or receipt.get("target") != target
            or receipt.get("beforeTextSha256") != digest(item["before"].encode())
            or receipt.get("afterTextSha256") != digest(item["text"].encode())
            or not receipt.get("pageCountBefore") or receipt.get("pageCountBefore") != receipt.get("pageCountAfter")
            or (previous and (previous.get("afterDocumentSha256") != receipt.get("beforeDocumentSha256")
                or previous.get("afterChangeSeq") != receipt.get("beforeChangeSeq")
                or previous.get("documentEpoch") != receipt.get("documentEpoch")))):
            raise HTTPException(422, "승인 내용과 편집기 적용 영수증이 일치하지 않습니다.")
        previous = receipt
    if previous.get("afterDocumentSha256") != value.document_sha256:
        raise HTTPException(422, "최종 편집 상태가 영수증과 다릅니다.")
    _, actual = paragraphs(data)
    if actual != [""] + [p["text"] for p in plan]:
        raise HTTPException(422, "HWPX 출력 문단이 승인한 보고서와 다릅니다.")
    if approval.get("document_standard"):
        try:
            same_format = format_signature(data, plan) == format_signature(scaffold_data, plan)
        except (ValueError, KeyError, StopIteration, AttributeError, IndexError):
            same_format = False
        if not same_format:
            raise HTTPException(422, "승인 시 고정한 기본 문서 서식이 출력과 다릅니다.")
