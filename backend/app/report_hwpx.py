"""Blank report scaffolds. AI text enters them only through approved exact commands."""
import copy
import io
import zipfile
from xml.etree import ElementTree as ET
from fastapi import HTTPException
from .hwpx import validate
from .document_standard import report_styles, role

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"


def paragraphs(data):
    _, roots = validate(data)
    sections = sorted(k for k in roots if k.startswith("Contents/section"))
    if sections != ["Contents/section0.xml"]:
        raise HTTPException(422, "보고서는 하나의 본문 구역을 사용해야 합니다.")
    root = roots[sections[0]]
    return root, ["".join(t.text or "" for t in p.iter(f"{{{HP}}}t")) for p in root]


def scaffold(seed, blocks, *, visual_settings=None):
    root, texts = paragraphs(seed)
    if len(texts) != 1 or any(texts) or len(list(root)) != 1:
        raise HTTPException(422, "새 빈 문서만 보고서 틀로 사용할 수 있습니다.")
    forbidden = {"tbl", "pic", "ole", "equation", "chart", "video", "shape", "fieldBegin"}
    if any(e.tag.split("}")[-1] in forbidden for e in root.iter()):
        raise HTTPException(422, "빈 문서에 개체가 포함되어 있습니다.")
    original = list(root)[0]
    run = original.find(f"{{{HP}}}run")
    if run is None:
        raise HTTPException(422, "빈 문서의 문단 서식이 없습니다.")
    # The caller's seed only proves this is a new blank document. Formatting
    # always comes from the verified bundled standard, never an arbitrary seed.
    _, header, properties, formats = report_styles(visual_settings=visual_settings)
    original.attrib.update(id="0", paraPrIDRef=formats["empty"][1], styleIDRef="0", pageBreak="0", columnBreak="0", merged="0")
    for child in list(original): original.remove(child)
    run = ET.SubElement(original, f"{{{HP}}}run", {"charPrIDRef": formats["empty"][0]})
    run.append(properties)
    ctrl = ET.SubElement(run, f"{{{HP}}}ctrl")
    ET.SubElement(ctrl, f"{{{HP}}}colPr", {"id": "0", "type": "NEWSPAPER", "layout": "LEFT", "colCount": "1", "sameSz": "1", "sameGap": "0"})
    ctrl = ET.SubElement(run, f"{{{HP}}}ctrl")
    style = visual_settings.get("page_number_style", "DASH") if visual_settings else "DASH"
    position = visual_settings.get("page_number_position", "BOTTOM_CENTER") if visual_settings else "BOTTOM_CENTER"
    ET.SubElement(ctrl, f"{{{HP}}}pageNum", {"pos": position if style in ("DASH", "ROMAN") else "NONE",
        "formatType": "ROMAN_CAPITAL" if style == "ROMAN" else "DIGIT", "sideChar": "-"})
    if style == "PLAIN":
        # The pinned pageNum sideChar parser/exporter substitutes '-' for an
        # empty decoration. Native footer PAGE auto-number fields round-trip
        # plain numbers without that fallback or literal page strings. Roman
        # uses pageNum because footer PAGE substitution renders decimal only.
        ctrl = ET.SubElement(run, f"{{{HP}}}ctrl")
        footer = ET.SubElement(ctrl, f"{{{HP}}}footer", dict(id="910001", applyPageType="BOTH"))
        sub = ET.SubElement(footer, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign="TOP",
            linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0"))
        cid, pid = formats["page_number"]
        fp = ET.SubElement(sub, f"{{{HP}}}p", dict(id="910002", paraPrIDRef=pid, styleIDRef="0", pageBreak="0", columnBreak="0", merged="0"))
        fr = ET.SubElement(fp, f"{{{HP}}}run", dict(charPrIDRef=cid))
        fc = ET.SubElement(fr, f"{{{HP}}}ctrl")
        number = ET.SubElement(fc, f"{{{HP}}}autoNum", dict(num="1", numType="PAGE"))
        ET.SubElement(number, f"{{{HP}}}autoNumFormat", dict(type="DIGIT",
            userChar="", prefixChar="", suffixChar="", supscript="0"))
        ET.SubElement(fr, f"{{{HP}}}t")
    ET.SubElement(run, f"{{{HP}}}t")
    plan = []
    for index, block in enumerate(blocks, 1):
        placeholder = f"REPORT_SLOT_{index:02d}"
        p = ET.SubElement(root, f"{{{HP}}}p", {**original.attrib, "id": str(index), "pageBreak": "1" if block["page_break"] else "0"})
        r = ET.SubElement(p, f"{{{HP}}}run", dict(run.attrib))
        kind = block.get("style_kind", role(block["text"], block["kind"]))
        if kind in formats:
            cid, pid = formats[kind]
            r.set("charPrIDRef", cid); p.set("paraPrIDRef", pid)
        ET.SubElement(r, f"{{{HP}}}t").text = placeholder
        plan.append({"paragraph": index, "before": placeholder, **block})
    with zipfile.ZipFile(io.BytesIO(seed)) as source:
        for name in ("Contents/section0.xml", "Contents/header.xml"):
            for _, ns in ET.iterparse(io.BytesIO(source.read(name)), events=["start-ns"]):
                try:
                    ET.register_namespace(*ns)
                except ValueError:
                    pass
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for item in source.infolist():
                replacement = {"Contents/section0.xml": root, "Contents/header.xml": header}.get(item.filename)
                target.writestr(copy.copy(item), ET.tostring(replacement, encoding="utf-8", xml_declaration=True)
                                if replacement is not None else source.read(item))
    return output.getvalue(), plan
