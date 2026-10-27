"""Bounded table geometry proposals; no document publication or content edits."""
import copy
import io
import re
import zipfile

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .block_hwpx import HH, HP, Semantic, serialize_block_xml
from .editor_design_hwpx import _append, _collection, _definition, _simple_table, _title_table, paragraph_controls
from .hwpx import digest
from .reference_source import apply_paragraph, mm_to_hwp
from .standard_policy import table_geometry


class TableTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    paragraph: int = Field(ge=0, le=100000)
    control: int = Field(ge=0, le=100000)
    parent_paragraph: int = Field(ge=0, le=100000)
    caption_paragraph: int | None = Field(default=None, ge=0, le=100000)
    caption: str = Field(default="", max_length=400)
    left_mm: float = Field(ge=0, le=1000)
    width_mm: float = Field(gt=0, le=1000)
    parent_half_mm: float = Field(ge=0, le=1000)
    caption_gap_mm: float | None = Field(default=None, ge=0, le=1000)


class TableLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parent_half: bool
    caption_gap_mm: float | None = Field(ge=0, le=10)

    @model_validator(mode="after")
    def useful(self):
        if not self.parent_half and self.caption_gap_mm is None:
            raise ValueError("표 배치 변경을 지정하세요.")
        return self


def margins(header, paragraph):
    prop = _definition(header, "paraProperties", paragraph.get("paraPrIDRef"))
    margin = prop.find(f".//{{{HH}}}margin")
    if margin is None:
        raise ValueError("Missing paragraph margins")
    return {node.tag.rsplit("}", 1)[-1]: int(node.get("value")) for node in margin}


def text(paragraph):
    return "".join(t.text or "" for run in paragraph.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))


def targets(source):
    semantic = Semantic(source)
    paragraphs, result, parent = list(semantic.section), [], None
    for index, paragraph in enumerate(paragraphs):
        try:
            controls = paragraph_controls(paragraph)
        except HTTPException:
            parent = None
            continue
        value = text(paragraph)
        if not controls and re.match(r"^\s*[□ㅇ○\-․]\s+", value):
            parent = index
        elif not controls and value.strip() and re.match(r"^\s*(?:\d+[.)]|[가-하][.)]|[①-⑳])\s", value):
            parent = None
        for control, table in enumerate(controls):
            if len(controls) != 1 or table.tag != f"{{{HP}}}tbl" or parent is None or value.strip():
                continue
            style = _definition(semantic.header, "styles", paragraph.get("styleIDRef", "0")).get("name", "")
            if "TOC" in style or _title_table(semantic.header, table):
                continue
            pos = table.find(f"{{{HP}}}pos")
            if pos is None or pos.get("treatAsChar") != "1" or pos.get("horzOffset", "0") != "0":
                continue
            try:
                _simple_table(table)
            except HTTPException:
                continue
            cells = table.findall(f"{{{HP}}}tr/{{{HP}}}tc")
            if table.get("rowCnt") == "1" and any("".join(t.text or "" for t in cell.iter(f"{{{HP}}}t")) == "→" for cell in cells):
                continue
            pm, tm = margins(semantic.header, paragraphs[parent]), margins(semantic.header, paragraph)
            left = max(0, tm.get("left", 0) + min(0, tm.get("intent", 0)))
            half, _ = table_geometry({"table_anchor_role": "parent", "table_anchor_percent": 50,
                "parent_left_indent_pt": pm.get("left", 0) / 100,
                "parent_first_line_indent_pt": pm.get("intent", 0) / 100, "table_width_percent": 100}, 1000)
            caption, caption_text, gap = None, "", None
            previous = paragraphs[index - 1] if index else None
            if previous is not None and index - 1 > parent and not paragraph_controls(previous):
                prop = _definition(semantic.header, "paraProperties", previous.get("paraPrIDRef"))
                align = prop.find(f"{{{HH}}}align")
                if align is not None and align.get("horizontal") == "CENTER" and 0 < len(text(previous)) <= 400:
                    caption, caption_text = index - 1, text(previous)
                    gap = round(margins(semantic.header, previous).get("next", 0) / 7200 * 25.4, 3)
            result.append(dict(paragraph=index, control=control, parent_paragraph=parent, caption_paragraph=caption,
                caption=caption_text, left_mm=round(left / 7200 * 25.4, 3),
                width_mm=round(int(table.find(f"{{{HP}}}sz").get("width")) / 7200 * 25.4, 3),
                parent_half_mm=round(half / 72 * 25.4, 3), caption_gap_mm=gap))
    if len(result) > 20:
        return []
    return result


def validated_layout(payload, layout):
    if layout is None:
        return None
    value = TableLayout.model_validate(layout).model_dump()
    context = payload.get("rewrite_context") or {}
    tables = context.get("table_targets") or []
    if context.get("unavailable") or not tables:
        raise ValueError("수정 가능한 표 배치 정보가 없는 요청입니다.")
    if value["caption_gap_mm"] is not None and not any(t["caption_paragraph"] is not None for t in tables):
        raise ValueError("간격을 변경할 표 제목이 없습니다.")
    return value


def apply_layout(source, context, layout):
    if digest(source) != context["source_sha256"]:
        raise HTTPException(409, "질문을 보낸 원본 문서와 다릅니다.")
    if layout is None:
        return source
    value = validated_layout({"rewrite_context": context}, layout)
    actual = targets(source)
    if actual != context.get("table_targets"):
        raise HTTPException(409, "표 위치·기준 문단·배치가 요청 시점과 다릅니다.")
    semantic = Semantic(source)
    page = semantic.section.find(f".//{{{HP}}}pagePr")
    margin = page.find(f"{{{HP}}}margin")
    available = int(page.get("width")) - int(margin.get("left")) - int(margin.get("right"))

    def paragraph_style(paragraph, **changes):
        prop = copy.deepcopy(_definition(semantic.header, "paraProperties", paragraph.get("paraPrIDRef")))
        apply_paragraph(prop, "layout", {"layout_" + key: val for key, val in changes.items()})
        paragraph.set("paraPrIDRef", _append(_collection(semantic.header, "paraProperties"), prop))

    for target in actual:
        paragraph = semantic.section[target["paragraph"]]
        table = paragraph_controls(paragraph)[target["control"]]
        if value["parent_half"]:
            pm = margins(semantic.header, semantic.section[target["parent_paragraph"]])
            left, width = table_geometry({"table_anchor_role": "parent", "table_anchor_percent": 50,
                "parent_left_indent_pt": pm.get("left", 0) / 100,
                "parent_first_line_indent_pt": pm.get("intent", 0) / 100,
                "parent_right_indent_pt": pm.get("right", 0) / 100, "table_width_percent": 100}, available / 100)
            size = table.find(f"{{{HP}}}sz")
            old = int(size.get("width")); new = min(old, round(width * 100))
            if new <= 0:
                raise HTTPException(422, "표를 배치할 본문 폭이 부족합니다.")
            for row in table.findall(f"{{{HP}}}tr"):
                used = 0
                for index, cell in enumerate(row):
                    cell_size = cell.find(f"{{{HP}}}cellSz")
                    scaled = new - used if index == len(row) - 1 else round(int(cell_size.get("width")) * new / old)
                    if scaled <= 0:
                        raise HTTPException(422, "표 셀 폭이 부족합니다.")
                    cell_size.set("width", str(scaled)); used += scaled
            size.set("width", str(new))
            table.find(f"{{{HP}}}pos").attrib.update(horzAlign="LEFT", horzOffset="0")
            paragraph_style(paragraph, left_indent_pt=left, first_line_indent_pt=0, horizontal_align="LEFT")
        if value["caption_gap_mm"] is not None and target["caption_paragraph"] is not None:
            paragraph_style(semantic.section[target["caption_paragraph"]], after_pt=mm_to_hwp(value["caption_gap_mm"]) / 100)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in semantic.files.items():
            archive.writestr(name, serialize_block_xml(semantic.section) if name == "Contents/section0.xml" else
                serialize_block_xml(semantic.header) if name == "Contents/header.xml" else raw)
    return output.getvalue()
