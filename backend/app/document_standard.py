"""Pinned, offline writing standard shared by report generation and artifact tools."""
import copy
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from fastapi import HTTPException
from .hwpx import digest

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
ASSETS = Path(__file__).parent / "assets" / "document-standard"


def load_standard():
    try:
        raw = (ASSETS / "profile.json").read_bytes()
        profile = json.loads(raw)
        reference = (ASSETS / "reference.hwpx").read_bytes()
        if digest(reference) != profile["reference_sha256"]:
            raise ValueError("reference hash mismatch")
        profile["profile_sha256"] = digest(raw)
        return profile, reference
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "기본 작성 기준 파일을 확인할 수 없습니다. 다른 서식으로 대체하지 않았습니다.") from exc


def metadata():
    profile, _ = load_standard()
    return {**profile, "reference_url": "/api/document-standard/reference", "automatic": True}


def set_margins(para, *, left=0, right=0, intent=0, before=0, after=0):
    # Source uses HwpUnitChar case and an older doubled-unit fallback.
    for branch in para.iter():
        local = branch.tag.split("}")[-1]
        if local not in ("case", "default"):
            continue
        factor = 2 if local == "default" else 1
        for node in branch.iter():
            key = node.tag.split("}")[-1]
            values = {"left": left, "right": right, "intent": intent, "prev": before, "next": after}
            if key in values and "value" in node.attrib:
                node.set("value", str(values[key] * factor))


def source_parts():
    profile, reference = load_standard()
    with zipfile.ZipFile(io.BytesIO(reference)) as z:
        return profile, ET.fromstring(z.read("Contents/header.xml")), ET.fromstring(z.read("Contents/section0.xml"))


def report_styles(*, visual_settings=None):
    profile, header, section = source_parts()
    chars = header.find(f".//{{{HH}}}charProperties")
    paras = header.find(f".//{{{HH}}}paraProperties")
    formats = {}
    # Title is a compact 15 mm frame because this automatic report has no cover/TOC.
    definitions = {
        "title": (2200, True, "CENTER", 160, 0, 2000),
        "heading": (1600, True, "LEFT", 160, 0, 1400),
        "subheading": (1500, True, "LEFT", 160, profile["subheading_indent_hwpunit"], 1400),
        "minor_heading": (1400, False, "LEFT", 160, profile["minor_heading_indent_hwpunit"], 1400),
        "body": (1400, False, "JUSTIFY", 160, profile["body_indent_hwpunit"], 1400),
        "source": (1200, False, "LEFT", 130, profile["note_indent_hwpunit"], 700),
        "empty": (100, False, "LEFT", 100, 0, 0),
    }
    for kind, bullet in profile["bullets"].items():
        definitions[kind] = (1500, False, "LEFT", 160,
                             bullet["marker_left"], 1000)
        definitions[kind + "_continuation"] = (1500, False, "LEFT", 160,
                                                bullet["marker_left"] + bullet["hanging"], 1000)
    if visual_settings is not None and visual_settings.get("page_number_style") == "PLAIN":
        align = visual_settings.get("page_number_position", "BOTTOM_CENTER").removeprefix("BOTTOM_")
        definitions["page_number"] = (1000, False, align, 100, 0, 0)
    if visual_settings is not None:
        for kind, definition in list(definitions.items()):
            group = "body" if kind == "body" or kind in profile["bullets"] or kind.endswith("_continuation") else kind
            if group not in ("title", "heading", "body", "source"):
                continue
            height, headline, align, line, left, after = definition
            height = round(visual_settings.get(group + "_size_pt", height / 100) * 100)
            line = visual_settings.get(group + "_line_percent", line)
            after = round(visual_settings.get(group + "_after_pt", after / 100) * 100)
            bullet_kind = kind.removesuffix("_continuation")
            if bullet_kind in profile["bullets"]:
                bullet = profile["bullets"][bullet_kind]
                left = round(visual_settings.get(bullet_kind + "_indent_pt", bullet["marker_left"] / 100) * 100)
                if kind.endswith("_continuation"):
                    left += bullet["hanging"]
            definitions[kind] = (height, headline, align, line, left, after)
    for kind, (height, headline, align, line, left, after) in definitions.items():
        original = profile["headline_char_id" if headline else "body_char_id"]
        char = copy.deepcopy(next(c for c in chars if c.get("id") == original))
        cid = str(max(int(c.get("id")) for c in chars) + 1)
        char.attrib.update(id=cid, height=str(height), textColor="#000000", shadeColor="#FFFFFFFF", borderFillIDRef="1")
        para = copy.deepcopy(next(p for p in paras if p.get("id") == profile["paragraph_id"]))
        pid = str(max(int(p.get("id")) for p in paras) + 1)
        para.set("id", pid)
        para.find(f"{{{HH}}}align").set("horizontal", align)
        for element in para.iter(f"{{{HH}}}lineSpacing"):
            element.set("value", str(line))
        para.find(f"{{{HH}}}breakSetting").set("breakLatinWord", "BREAK_WORD")
        bullet = profile["bullets"].get(kind)
        set_margins(para, left=left, intent=-bullet["hanging"] if bullet else 0, after=after)
        if kind in profile["bullets"] or kind.endswith("_continuation"):
            para.find(f"{{{HH}}}breakSetting").set("breakNonLatinWord", "KEEP_WORD")
        if kind == "title":
            border = para.find(f"{{{HH}}}border")
            border.attrib.update(borderFillIDRef=profile["compact_title_border_id"], offsetTop="365", offsetBottom="365", ignoreMargin="1")
            if visual_settings is not None and "title_vertical_offset_pt" in visual_settings:
                offset = round(visual_settings["title_vertical_offset_pt"] * 100)
                # Extra space is INSIDE the two blue rules. Pair the paragraph
                # margin with the rule offset so surrounding prose cannot overlap.
                top, bottom = max(0, offset), max(0, -offset)
                set_margins(para, left=left, before=top, after=after + bottom)
                # rhwp's frame starts BEFORE paragraph prev spacing. Keeping
                # offsetTop fixed makes the top rule stay while text moves down.
                border.attrib.update(offsetTop="365", offsetBottom=str(365 + bottom))
        chars.append(char); paras.append(para); formats[kind] = (cid, pid)
    chars.set("itemCnt", str(len(chars))); paras.set("itemCnt", str(len(paras)))
    for element in header.iter():
        if element.get("shadeColor") == "none": element.set("shadeColor", "#FFFFFFFF")
    properties = copy.deepcopy(section.find(f".//{{{HP}}}secPr"))
    for child in list(properties):
        if child.tag.split("}")[-1] in ("parameterset", "presentation"):
            properties.remove(child)
    if visual_settings is not None:
        margin = properties.find(f"{{{HP}}}pagePr/{{{HP}}}margin")
        for side in ("top", "bottom", "left", "right"):
            key = "margin_" + side + "_mm"
            if key in visual_settings:
                margin.set(side, str(int(visual_settings[key] * 7200 / 25.4)))
    return profile, header, properties, formats


def layout_metrics(section, visual_settings=None):
    """Keep the historical conservative page budget for unchanged previews."""
    if visual_settings is None:
        return 481.9, 625
    page = section.find(f".//{{{HP}}}pagePr")
    margin = page.find(f"{{{HP}}}margin")
    available = (int(page.get("width")) - int(margin.get("left")) - int(margin.get("right"))) / 100
    # Preserve the existing safety allowance while respecting custom top/bottom
    # margins. The bundled reference uses 5669 + 2834 HWP units here.
    budget = 625 + (8503 - int(margin.get("top")) - int(margin.get("bottom"))) / 100
    return available, budget


def paragraph_line_factor(para):
    branch = para.find(f"{{{HP}}}switch/{{{HP}}}case")
    branch = branch if branch is not None else para
    return int(branch.find(f"{{{HH}}}lineSpacing").get("value")) / 100


def role(text, kind):
    if kind == "body":
        for marker, name in (("□ ", "square"), ("ㅇ ", "circle"), ("○ ", "circle"), ("- ", "dash")):
            if text.startswith(marker): return name
        if re.match(r"^[가-하]\.\s", text): return "subheading"
        if re.match(r"^\(?\d+\)\s|^\([가-하]\)\s", text): return "minor_heading"
    return kind


def format_signature(data, plan):
    """Semantic style values, independent of style IDs renumbered by the editor."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        header = ET.fromstring(z.read("Contents/header.xml")); section = ET.fromstring(z.read("Contents/section0.xml"))
    chars = {c.get("id"): c for c in header.iter(f"{{{HH}}}charPr")}
    paras = {p.get("id"): p for p in header.iter(f"{{{HH}}}paraPr")}
    borders = {b.get("id"): b for b in header.iter(f"{{{HH}}}borderFill")}
    result = []
    for item in plan:
        p = list(section)[item["paragraph"]]
        runs = p.findall(f"{{{HP}}}run")
        if len(runs) != 1: raise ValueError("unexpected mixed formatting")
        char = chars[runs[0].get("charPrIDRef")]; para = paras[p.get("paraPrIDRef")]
        font = char.find(f"{{{HH}}}fontRef")
        fonts = []
        for lang, idref in sorted(font.attrib.items()):
            face = next(e for e in header.iter(f"{{{HH}}}fontface") if e.get("lang", "").casefold() == lang.casefold())
            fonts.append((lang, next(e.get("face") for e in face if e.get("id") == idref)))
        case = para.find(f"{{{HP}}}switch/{{{HP}}}case")
        branch = case if case is not None else para
        margin = branch.find(f"{{{HH}}}margin")
        border = para.find(f"{{{HH}}}border")
        fill = borders[border.get("borderFillIDRef")]
        result.append({"fonts": fonts, "height": char.get("height"), "color": char.get("textColor"),
                       "alignment": para.find(f"{{{HH}}}align").get("horizontal"),
                       "margins": [(e.tag.split("}")[-1], e.get("value")) for e in margin],
                       "line": branch.find(f"{{{HH}}}lineSpacing").get("value"),
                       "border_offsets": {key: border.get(key, "0") for key in ("offsetLeft", "offsetRight", "offsetTop", "offsetBottom")},
                       "borders": [(e.tag.split("}")[-1], e.get("type"), e.get("width"), e.get("color")) for e in fill if e.tag.split("}")[-1].endswith("Border")],
                       "page_break": p.get("pageBreak", "0")})
    page = section.find(f".//{{{HP}}}pagePr")
    # Native page fields live outside the fenced text paragraphs. Their semantic
    # values must also survive application: style IDs may be renumbered by rhwp,
    # while deleting/changing a footer field is never a harmless normalization.
    page_numbers = [{key: number.get(key, default) for key, default in (
        ("pos", "BOTTOM_CENTER"), ("formatType", "DIGIT"), ("sideChar", "-"))}
        for number in section.iter(f"{{{HP}}}pageNum")]
    footer_numbers = []
    for footer in section.iter(f"{{{HP}}}footer"):
        entries = []
        for p in footer.findall(f"{{{HP}}}subList/{{{HP}}}p"):
            pr = paras[p.get("paraPrIDRef")]
            numbers = []
            for run in p.findall(f"{{{HP}}}run"):
                char = chars[run.get("charPrIDRef")]
                for number in run.iter(f"{{{HP}}}autoNum"):
                    fmt = number.find(f"{{{HP}}}autoNumFormat")
                    if fmt is None:
                        raise ValueError("Native page-number field has no frozen format")
                    numbers.append({"kind": number.get("numType", "PAGE"), "height": char.get("height"),
                        "color": char.get("textColor"), "format": {key: fmt.get(key, default) for key, default in (
                            ("type", "DIGIT"), ("userChar", ""), ("prefixChar", ""), ("suffixChar", ""), ("supscript", "0"))}})
            entries.append({"alignment": pr.find(f"{{{HH}}}align").get("horizontal"), "numbers": numbers,
                            "text": "".join(t.text or "" for t in p.iter(f"{{{HP}}}t"))})
        footer_numbers.append({"apply": footer.get("applyPageType", "BOTH"), "paragraphs": entries})
    return {"paragraphs": result, "page": dict(page.attrib), "page_margins": dict(page.find(f"{{{HP}}}margin").attrib),
            "page_numbers": page_numbers, "footer_numbers": footer_numbers}
