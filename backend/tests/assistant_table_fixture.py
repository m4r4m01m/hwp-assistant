"""Synthetic table and lengthy prose for actual-engine editing tests."""
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from app.block_hwpx import HP, serialize_block_xml
from app.reference_standard import defaults
from app.report_contract import report_blocks
from app.report_hwpx import scaffold, paragraphs
from app.report_rich import layout_rich_report

REPLACEMENTS = {
    "□ 운영팀은 30개 시스템을 대상으로 취약한 버전이 사용되고 있는지를 확인하기 위한 점검을 진행할 계획임.": "□ 운영팀은 30개 시스템의 취약 버전 점검 예정.",
    "ㅇ 보안팀은 10월 30일까지 패치가 적용되지 않은 시스템에 패치를 적용하는 작업을 진행할 계획임.": "ㅇ 보안팀은 10월 30일까지 미패치 시스템에 패치 적용 예정.",
    "□ 보안팀은 점검 결과에 대한 검토를 실시한 뒤 그 결과를 경영진에게 공유하는 절차를 진행할 계획임.": "□ 보안팀은 점검 결과 검토 후 경영진 공유 예정.",
}


def document():
    draft = dict(title="표 배치와 문장 축약 — 합성 검수", notes=[], sources=[], limitations=[], summary="□ 합성 검수 자료")
    draft["sections"] = [
        {"heading": "점검 현황", "paragraphs": list(REPLACEMENTS)[:2], "tables": [{"caption": "시스템별 조치 계획", "columns": ["대상", "담당", "기한"], "rows": [["합성 시스템", "보안팀", "10월 30일"]]}]},
        {"heading": "후속 조치", "paragraphs": [list(REPLACEMENTS)[2]]},
        {"heading": "완료 기준", "paragraphs": ["□ 점검 결과와 패치 적용 기록 보관."]},
    ]
    settings = defaults()
    blocks = report_blocks(draft, rich=True, visual_settings=settings)
    seed = (Path(__file__).parents[2] / "samples/paragraph-and-table.hwpx").read_bytes()
    section, _ = paragraphs(seed)
    for node in list(section): section.remove(node)
    paragraph = E.SubElement(section, f"{{{HP}}}p", {"id": "0", "paraPrIDRef": "0", "styleIDRef": "0"})
    E.SubElement(E.SubElement(paragraph, f"{{{HP}}}run", {"charPrIDRef": "0"}), f"{{{HP}}}t")
    blank = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(seed)) as original, zipfile.ZipFile(blank, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in original.namelist():
            archive.writestr(name, serialize_block_xml(section) if name == "Contents/section0.xml" else original.read(name))
    seed = blank.getvalue()
    raw, plan = scaffold(seed, blocks, visual_settings=settings)
    section, _ = paragraphs(raw)
    for item in plan:
        section[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in original.namelist():
            archive.writestr(name, serialize_block_xml(section) if name == "Contents/section0.xml" else original.read(name))
    return layout_rich_report(stream.getvalue(), plan, visual_settings=settings)
