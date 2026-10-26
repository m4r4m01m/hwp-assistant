"""Synthetic report with bold leads, ordinary body, a table and a printed note."""
import copy
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from app.block_hwpx import Semantic, HP, HH, serialize_block_xml

REPLACEMENTS = {
    "□ (점검현황) 운영팀은 30개 시스템의 취약 버전을 확인하는 점검을 진행할 계획임.": "□ (점검현황) 운영팀은 30개 시스템의 취약 버전을 점검할 계획임.",
    "ㅇ (조치일정) 보안팀은 10월 30일까지 패치를 적용하는 조치를 진행할 계획임.": "ㅇ (조치일정) 보안팀은 10월 30일까지 패치를 적용할 계획임.",
}


def document():
    semantic = Semantic((Path(__file__).parents[2] / "samples" / "paragraph-and-table.hwpx").read_bytes())
    chars = semantic.header.find(f".//{{{HH}}}charProperties")
    regular = copy.deepcopy(chars[0]); regular.set("id", str(len(chars))); regular.set("height", "1400")
    for child in list(regular):
        if child.tag == f"{{{HH}}}bold": regular.remove(child)
    chars.append(regular)
    bold = copy.deepcopy(regular); bold.set("id", str(len(chars))); E.SubElement(bold, f"{{{HH}}}bold"); chars.append(bold)
    chars.set("itemCnt", str(len(chars)))
    template, table = copy.deepcopy(semantic.section[1]), copy.deepcopy(semantic.section[2])
    def paragraph(text, serial, lead=False):
        p = copy.deepcopy(template); p.set("id", str(serial))
        for child in list(p): p.remove(child)
        if lead:
            marker, rest = text.split(" ", 1); label, rest = rest.split(")", 1)
            parts = [(marker + " ", regular), (label + ")", bold), (rest, regular)]
        else: parts = [(text, regular)]
        for value, style in parts:
            r = E.SubElement(p, f"{{{HP}}}run", {"charPrIDRef": style.get("id")})
            E.SubElement(r, f"{{{HP}}}t").text = value
        return p
    for node in list(semantic.section)[1:]: semantic.section.remove(node)
    semantic.section.extend([paragraph("합성 취약점 대응 보고", 1), paragraph("1. 현황과 조치", 2),
        *[paragraph(text, i + 3, True) for i, text in enumerate(REPLACEMENTS)], table,
        paragraph("주1) 본문 수치와 담당 부서는 합성 검수 자료임.", 8)])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in semantic.files.items():
            archive.writestr(name, serialize_block_xml(semantic.header) if name == "Contents/header.xml" else
                serialize_block_xml(semantic.section) if name == "Contents/section0.xml" else raw)
    return output.getvalue()
