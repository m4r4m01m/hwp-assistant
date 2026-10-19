"""Editable revision-3 standards derived from the immutable original rules/examples.

This module never updates the original, registered historical bytes or frozen input.
"""
import copy
import io
import re
import uuid
import zipfile
from functools import lru_cache
from typing import Literal
from xml.etree import ElementTree as E

from fastapi import HTTPException
from pydantic import Field, create_model, model_validator

from .document_standard import HP, HH, HC, load_standard, normalize_no_fill
from .hwpx import digest, validate
from .report_contract import ReportValue, citation_marker
from . import reference_source as source

PRESETS = {
    "appendix-1": ("별첨 1 · 표지·목차 없는 보고서", "문서표지 및 목차가 불필요한 경우의 원문 구성", 81, 106),
    "appendix-2": ("별첨 2 · 표지와 별도 목차", "문서표지와 목차가 필요한 경우의 원문 구성", 111, 131),
    "appendix-3": ("별첨 3 · 목차를 포함한 표지", "문서표지에 목차를 함께 둔 원문 구성", 131, 141),
}
MAX_PAGES = 32
WIDTHS = (.1, .12, .15, .2, .25, .3, .4, .5, .6, .7, 1., 1.5, 2., 3., 4., 5.)
ROLE_LABELS = {
    "title": "본문 문서 제목", "heading": "대제목 · 첫째 항목", "subheading": "중제목 · 둘째 항목",
    "minor_heading": "소제목 · 셋째 항목", "body": "일반 본문", "source": "참고·출처", "note": "본문 주석",
    "caption": "표·그림 제목", "unit": "표·그림 단위", "table": "표 내용",
    "table_source": "표·그림 아래 주석", "cover_title": "표지 제목", "cover_date": "표지 일자",
    "cover_department": "표지 부서명", "toc_title": "목차 제목", "toc_major": "목차 첫째 항목",
    "toc_minor": "목차 둘째 항목", "attachment": "붙임·별첨 제목", "square": "□ 기호 항목",
    "circle": "ㅇ 기호 항목", "dash": "- 기호 항목", "dot": "․ 기호 항목",
    "numbered_4": "넷째 번호 항목", "numbered_5": "다섯째 번호 항목", "numbered_6": "여섯째 번호 항목",
}
SUFFIXES = {
    "font": ("글꼴", "text", None, None, None, ""),
    "size_pt": ("글자 크기", "number", 5, 72, .5, "pt"),
    "ratio_percent": ("장평", "number", 50, 200, 1, "%"),
    "spacing_percent": ("자간", "number", -20, 50, 1, "%"),
    "color": ("글자 색", "color", None, None, None, ""),
    "bold": ("진하게", "boolean", None, None, None, ""),
    "italic": ("기울임", "boolean", None, None, None, ""),
    "underline": ("밑줄", "select", None, None, None, ""),
    "horizontal_align": ("정렬", "select", None, None, None, ""),
    "line_percent": ("줄 간격", "number", 50, 300, 1, "%"),
    "before_pt": ("문단 앞 간격", "number", 0, 120, 1, "pt"),
    "after_pt": ("문단 뒤 간격", "number", 0, 120, 1, "pt"),
    "left_indent_pt": ("왼쪽 여백", "number", 0, 120, .5, "pt"),
    "right_indent_pt": ("오른쪽 여백", "number", 0, 120, .5, "pt"),
    "first_line_indent_pt": ("첫 줄 들여쓰기 · 음수는 내어쓰기", "number", -120, 120, .5, "pt"),
}


def _text(paragraph):
    return "".join(t.text or "" for run in paragraph.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))


@lru_cache(maxsize=1)
def _source_rules():
    profile, raw = load_standard()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        section = E.fromstring(archive.read("Contents/section0.xml"))
    paragraphs = list(section.iter(f"{{{HP}}}p"))
    anchors = {}
    def walk(node, path, top_index=None):
        anchors[node] = dict(path=path, top_level_index=top_index)
        counts = {}
        for index, child in enumerate(node):
            local = child.tag.split("}")[-1]
            counts[local] = counts.get(local, 0) + 1
            walk(child, path + f"/{local}[{counts[local]}]", index if node is section else top_index)
    walk(section, "/sec")
    selectors = [
        ("scope", "적용 대상", [4]), ("principles", "기본 원칙", [8, 10, 12]),
        ("paper", "용지·여백", [21, 34, 38, 43, 46, 48, 53]),
        ("fonts", "글꼴·크기", [58, 59, 60, 61, *range(66, 109)]),
        ("character", "장평·자간·정렬·색상·밑줄", [111, 113, 115, 117]),
        ("spacing", "문단·행 간격", [120, 123]),
        ("hierarchy", "번호·기호·들여쓰기", [132, *range(135, 148), 152]),
        ("table", "표", [160, 161, 162, 163, 169, 170, 171, 173, 174, 175, 176, 177]),
        ("page_number", "면 표시", [187]), ("cover", "본문 제목·표지·목차", [191, 204, 205, 206, 207, 208]),
        ("number", "숫자", [219]), ("date", "날짜", [226, 227]), ("time", "시간", [229]),
        ("citation", "주석", [231]), ("punctuation", "문장 부호", [*range(238, 251)]),
    ]
    return [dict(id=identifier, title=title, text="\n".join(_text(paragraphs[i]) for i in indices),
                 anchors=[dict(part="Contents/section0.xml", ordinal=i, paragraph_id=paragraphs[i].get("id"), **anchors[paragraphs[i]]) for i in indices])
            for identifier, title, indices in selectors]


def rules():
    return copy.deepcopy(_source_rules())


def source_metadata():
    profile, _ = load_standard()
    return dict(name=profile["name"], reference_sha256=profile["reference_sha256"],
                profile_sha256=profile["profile_sha256"], raw_endpoint_internal=True,
                default_precedence="규정표에 명시된 값 우선, 미명시 값은 원문 예시 XML에서 추출",
                preview_presentation="별첨의 본문 문구는 보존하며 번호·기호·주석 표식은 선택한 표시 설정에 연결합니다. 원문의 혼합 주석 표식과 달라질 수 있습니다.",
                provenance=source.source_profile()["roles"],
                structural_provenance={
                    "table_width_percent": dict(part="Contents/section0.xml", top_level_index=99, table_id="1872749261", source_width=29907,
                        basis="폭의 수치 규정이 없어 원문 표 폭29907과 본문 폭48190에서 추출"),
                    "citation_links": [dict(id="S1", inline_ordinal=407, description_ordinal=421, original="*"),
                        dict(id="S2", inline_ordinal=411, description_ordinal=419, original="주1)"),
                        dict(id="S3", inline_ordinal=412, description_ordinal=420, original="주2)")],
                    "toc_border": dict(part="Contents/section0.xml", top_level_index=129, table_id="1872749252", color="#000000", width_mm=.1),
                    "toc_padding": dict(ordinal=206, basis="좌우20pt는 문단 여백에 적용하며 셀 좌우 여백을 중복하지 않음"),
                    "page_number_font": dict(basis="원문에는 실제 쪽번호 필드·고유 글꼴 정의가 없어 참고·주석 역할의 글꼴과 12pt 기본값을 상속"),
                })


def _numeric(key, label, group, value, minimum, maximum, step=1, unit="", **extra):
    return dict(key=key, label=label, group=group, type="number", min=minimum, max=maximum,
                step=step, unit=unit, default=value, **extra)


def _choice(key, label, group, value, options, **extra):
    return dict(key=key, label=label, group=group, type="select", unit="", default=value,
                options=[dict(value=item, label=name) for item, name in options], **extra)


@lru_cache(maxsize=1)
def _catalog():
    role_values = source.role_defaults()
    fields = [_choice("preset_id", "별첨 구성", "문서 구성", "appendix-1",
                      [(key, value[0]) for key, value in PRESETS.items()])]
    for role, label in ROLE_LABELS.items():
        for suffix, (name, kind, minimum, maximum, step, unit) in SUFFIXES.items():
            key = role + "_" + suffix
            if key == "square_left_indent_pt":
                step = .25  # The original's measured prefix is exactly 24.75pt.
            if key not in role_values:
                raise ValueError("Original role extraction is missing " + key)
            item = dict(key=key, label=name, group=label, type=kind, min=minimum, max=maximum,
                        step=step, unit=unit, default=role_values[key])
            if suffix == "font":
                item.update(min_length=1, max_length=80)
            elif suffix == "underline":
                item["options"] = [dict(value=value, label=text) for value, text in (("NONE", "없음"), ("BOTTOM", "아래 밑줄"))]
            elif suffix == "horizontal_align":
                item["options"] = [dict(value=value, label=text) for value, text in (
                    ("LEFT", "왼쪽"), ("CENTER", "가운데"), ("RIGHT", "오른쪽"), ("JUSTIFY", "양쪽"))]
            fields.append(item)
    fields.extend([
        _numeric("page_width_mm", "용지 너비", "용지·여백", 210, 100, 420, .1, "mm"),
        _numeric("page_height_mm", "용지 높이", "용지·여백", 297, 100, 594, .1, "mm"),
        *[_numeric("margin_" + side + "_mm", label, "용지·여백", value, 0, 80, .1, "mm")
          for side, label, value in (("top", "위쪽", 20), ("bottom", "아래쪽", 10), ("left", "왼쪽", 20),
                                     ("right", "오른쪽", 20), ("header", "머리말", 10), ("footer", "꼬리말", 10))],
        dict(key="table_header_fill", label="첫 행 음영 · 검정 90% 밝게", group="표 구조", type="color", unit="", default="#E5E5E5"),
        dict(key="table_border_color", label="선 색", group="표 구조", type="color", unit="", default="#000000"),
        _choice("table_border_style", "선 종류", "표 구조", "SOLID", [("SOLID", "실선"), ("DASH", "파선"), ("DOT", "점선"), ("NONE", "없음")]),
        _choice("table_border_width_mm", "선 두께", "표 구조", .1, [(value, f"{value:g} mm") for value in WIDTHS]),
        _choice("table_border_scope", "선 적용", "표 구조", "NO_OUTER_SIDES", [("NO_OUTER_SIDES", "양쪽 바깥선 없음"), ("ALL", "모든 선"), ("OUTER", "바깥선"), ("HORIZONTAL", "가로선"), ("NONE", "없음")]),
        _choice("table_vertical_align", "셀 세로 정렬", "표 구조", "CENTER", [("TOP", "위"), ("CENTER", "가운데"), ("BOTTOM", "아래")]),
        *[_numeric("table_padding_" + side + "_mm", label, "표 구조", value, 0, 20, .1, "mm")
          for side, label, value in (("left", "셀 왼쪽 여백", 1.8), ("right", "셀 오른쪽 여백", 1.8), ("top", "셀 위 여백", .5), ("bottom", "셀 아래 여백", .5))],
        _numeric("table_min_row_height_mm", "최소 셀 높이", "표 구조", 7, 3, 80, .1, "mm"),
        _numeric("table_width_percent", "본문 폭 대비 표 너비", "표 구조", round(29907 / 48190 * 100, 6), 20, 100, 1, "%"),
        dict(key="table_repeat_header", label="다음 쪽에 첫 행 반복", group="표 구조", type="boolean", unit="", default=True),
        _numeric("title_frame_height_mm", "본문 제목 상자 높이", "제목 상자", 15, 5, 60, .1, "mm"),
        _choice("title_rule_width_mm", "본문 제목 상하선 두께", "제목 상자", 1., [(value, f"{value:g} mm") for value in WIDTHS]),
        dict(key="title_rule_color", label="본문 제목 상하선 색", group="제목 상자", type="color", unit="", default="#437FC1"),
        _numeric("cover_frame_height_mm", "표지 제목 상자 높이", "제목 상자", 23, 5, 80, .1, "mm"),
        _choice("cover_rule_width_mm", "표지 제목 상하선 두께", "제목 상자", 1.5, [(value, f"{value:g} mm") for value in WIDTHS]),
        dict(key="cover_rule_color", label="표지 제목 상하선 색", group="제목 상자", type="color", unit="", default="#107FB5"),
        dict(key="toc_border_color", label="목차 상자 선 색", group="목차 상자", type="color", unit="", default="#000000"),
        _choice("toc_border_width_mm", "목차 상자 선 두께", "목차 상자", .1, [(value, f"{value:g} mm") for value in WIDTHS]),
        _choice("toc_border_style", "목차 상자 선 종류", "목차 상자", "SOLID", [("SOLID", "실선"), ("DASH", "파선"), ("DOT", "점선"), ("NONE", "없음")]),
        *[_numeric("toc_padding_" + side + "_mm", label, "목차 상자", value, 0, 20, .1, "mm")
          for side, label, value in (("left", "셀 왼쪽 여백", 0), ("right", "셀 오른쪽 여백", 0), ("top", "셀 위 여백", source.hwp_to_mm(140)), ("bottom", "셀 아래 여백", source.hwp_to_mm(140)))],
        _choice("emphasis_color", "선택 강조 색", "글자 강조", "#0000FF", [("#0000FF", "파랑"), ("#FF0000", "빨강")], help="원문에서 허용한 빨강 또는 파랑 중 한 가지를 선택합니다."),
        _choice("page_number_style", "면 표시 형식", "면 표시·주석", "DASH", [("DASH", "- 1 -"), ("PAGE_TOTAL", "일련번호 / 전체면수"), ("PLAIN", "1"), ("NONE", "없음")]),
        _choice("page_number_position", "면 표시 위치", "면 표시·주석", "BOTTOM_CENTER", [("BOTTOM_LEFT", "아래 왼쪽"), ("BOTTOM_CENTER", "아래 가운데"), ("BOTTOM_RIGHT", "아래 오른쪽")]),
        _choice("citation_style", "주석 표식", "면 표시·주석", "STARS", [("STARS", "*, **, ***"), ("NOTE", "주1), 주2)")]),
        dict(key="citation_superscript", label="본문 주석 표식 윗첨자", group="면 표시·주석", type="boolean", unit="", default=True),
        dict(key="body_parenthetical_labels", label="항목 요약 말머리", group="문서 구성", type="boolean", unit="", default=False,
             help="원문 별첨 예시에 없는 추가 말머리를 새 보고서에 표시할지 선택합니다."),
        dict(key="show_core_summary", label="별도 핵심 요약 절", group="문서 구성", type="boolean", unit="", default=False,
             help="원문 별첨 예시에 없는 추가 요약 절을 새 보고서에 표시할지 선택합니다."),
        _choice("numbering_style", "본문 항목 구성", "항목 번호·기호", "BULLETS", [("BULLETS", "기호 항목"), ("NUMBERED", "번호 항목")]),
    ])
    for index, value in enumerate(("1.", "가.", "(1)", "(가)", "①", "㉮"), 1):
        fields.append(dict(key=f"numbered_{index}_marker", label=f"{index}째 번호 표식", group="항목 번호·기호", type="text", unit="", default=value, min_length=1, max_length=16))
    for key, value in (("square", "□"), ("circle", "ㅇ"), ("dash", "-"), ("dot", "․")):
        fields.append(dict(key=f"bullet_{key}_marker", label=value + " 항목 표식", group="항목 번호·기호", type="text", unit="", default=value, min_length=1, max_length=16))
    rules_by_id = {rule["id"]: rule for rule in rules()}
    for key, rule_id in (("scope_rule", "scope"), ("principles_rule", "principles"), ("number_rule", "number"),
                         ("date_rule", "date"), ("time_rule", "time"), ("punctuation_rule", "punctuation"),
                         ("paragraph_spacing_rule", "spacing")):
        rule = rules_by_id[rule_id]
        fields.append(dict(key=key, label=rule["title"], group="작성 규정", type="textarea", unit="",
                           default=rule["text"], min_length=0, max_length=4000,
                           help="새 초안 작성 지시입니다. 기존 사실·날짜·인용을 사후 치환하지 않습니다."))
    return fields


def field_catalog():
    return copy.deepcopy(_catalog())


def defaults(preset_id="appendix-1"):
    if preset_id not in PRESETS:
        raise ValueError("Unknown original appendix preset")
    return {**{field["key"]: field["default"] for field in _catalog()}, "preset_id": preset_id}


class _SettingsBase(ReportValue):
    @model_validator(mode="before")
    @classmethod
    def strict_options_and_text(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for field in _catalog():
            item = value.get(field["key"])
            if field["type"] == "select" and isinstance(item, bool):
                raise ValueError("선택 값에 참/거짓을 사용할 수 없습니다.")
            if isinstance(item, str):
                if field["type"] in ("text", "select") and any(ord(c) < 32 or ord(c) == 127 for c in item):
                    raise ValueError("한 줄 설정에 제어 문자를 사용할 수 없습니다.")
                if field["type"] == "textarea" and any(ord(c) < 32 and c not in "\r\n\t" for c in item):
                    raise ValueError("작성 규정에 제어 문자를 사용할 수 없습니다.")
                if field["type"] == "color":
                    value[field["key"]] = item.upper()
        return value

    @model_validator(mode="after")
    def physical_page(self):
        value = self.model_dump(mode="json")
        if value["margin_left_mm"] + value["margin_right_mm"] + 30 > value["page_width_mm"]:
            raise ValueError("용지 너비보다 좌우 여백이 큽니다.")
        if value["margin_top_mm"] + value["margin_bottom_mm"] + 30 > value["page_height_mm"]:
            raise ValueError("용지 높이보다 위아래 여백이 큽니다.")
        return self


def _model_fields():
    fields = {}
    for field in _catalog():
        kind = field["type"]
        kwargs = dict(strict=True, description=field.get("help", field["label"]))
        if kind == "number":
            integer = field["key"].endswith(("_line_percent", "_ratio_percent", "_spacing_percent"))
            cls = int if integer else float
            kwargs.update(ge=field["min"], le=field["max"])
            if not integer:
                kwargs["allow_inf_nan"] = False
        elif kind == "boolean":
            cls = bool
        elif kind == "select":
            values = tuple(option["value"] for option in field["options"])
            cls = Literal[values]
            kwargs.pop("strict")
        elif kind == "color":
            cls = str
            kwargs.update(pattern=r"^#[0-9A-Fa-f]{6}$")
        else:
            cls = str
            kwargs.update(min_length=field.get("min_length", 0), max_length=field.get("max_length", 80))
        fields[field["key"]] = (cls, Field(**kwargs))
    return fields


VisualSettingsV3 = create_model("VisualSettingsV3", __base__=_SettingsBase, **_model_fields())


def _register_namespaces(archive):
    for name in ("Contents/header.xml", "Contents/section0.xml"):
        for _, namespace in E.iterparse(io.BytesIO(archive.read(name)), events=["start-ns"]):
            try:
                E.register_namespace(*namespace)
            except ValueError:
                pass


def original_projection():
    """Read-only original content projection, never a report or source replacement."""
    profile, raw = load_standard()
    _, roots = validate(raw)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive, zipfile.ZipFile(output, "w") as target:
        _register_namespaces(archive)
        for entry in archive.infolist():
            data = archive.read(entry)
            root = roots.get(entry.filename)
            changed = normalize_no_fill(root) if root is not None else False
            if root is not None:
                for props in root.iter(f"{{{HP}}}secPr"):
                    for child in list(props):
                        if child.tag.split("}")[-1] in ("presentation", "parameterset"):
                            props.remove(child)
                            changed = True
            if changed:
                data = E.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(copy.copy(entry), data)
    result = output.getvalue()
    validate(result)
    return result


def preset_definitions():
    return [dict(preset_id=key, name=value[0], description=value[1], source_range=dict(part="Contents/section0.xml", start=value[2], end_exclusive=value[3]))
            for key, value in PRESETS.items()]


def _validate_seed(seed):
    from .report_hwpx import paragraphs
    root, values = paragraphs(seed)
    if values != [""] or len(root) != 1 or any(node.tag.split("}")[-1] in ("tbl", "pic", "ole", "equation", "fieldBegin") for node in root.iter()):
        raise HTTPException(422, "새 빈 문서만 표준 예시의 바탕으로 사용할 수 있습니다.")


def render_preview(seed, settings):
    _validate_seed(seed)
    return render_preset(settings)


def render_preset(settings):
    """Keep original appendix content/structure, applying declared standard settings."""
    from .reference_report import apply_title_frame
    preset_id = settings["preset_id"]
    _, _, start, stop = PRESETS[preset_id]
    profile, raw = load_standard()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        _register_namespaces(archive)
        header = E.fromstring(archive.read("Contents/header.xml"))
        original = E.fromstring(archive.read("Contents/section0.xml"))
        section = E.Element(original.tag, dict(original.attrib))
        first = copy.deepcopy(original[0])
        for run in first.findall(f"{{{HP}}}run"):
            for text in run.findall(f"{{{HP}}}t"):
                text.text = ""
                for child in list(text):
                    text.remove(child)
        section.append(first)
        for paragraph in list(original)[start:stop]:
            section.append(copy.deepcopy(paragraph))
        props = section.find(f".//{{{HP}}}secPr")
        for child in list(props):
            if child.tag.split("}")[-1] in ("presentation", "parameterset"):
                props.remove(child)
        source.apply_page(props, settings)
        role_positions, citations = _sample_presentation(_preset_roles(preset_id, section), settings)
        chars = header.find(f".//{{{HH}}}charProperties")
        paras = header.find(f".//{{{HH}}}paraProperties")
        originals_c = {node.get("id"): node for node in chars}
        originals_p = {node.get("id"): node for node in paras}
        clones_c, clones_p = {}, {}
        def clone(collection, mapping, key, original):
            if key not in mapping:
                node = copy.deepcopy(original)
                node.set("id", str(max(int(item.get("id")) for item in collection) + 1))
                collection.append(node)
                collection.set("itemCnt", str(len(collection)))
                mapping[key] = node
            return mapping[key]
        for paragraph, role in role_positions:
            old = paragraph.get("paraPrIDRef")
            para = clone(paras, clones_p, (old, role), originals_p[old])
            source.apply_paragraph(para, role, settings)
            if role in ("title", "cover_title"):
                apply_title_frame(header, para, role, settings)
            paragraph.set("paraPrIDRef", para.get("id"))
            for run in paragraph.findall(f"{{{HP}}}run"):
                old = run.get("charPrIDRef")
                marker = citations.get(paragraph)
                inline = bool(marker and "".join(t.text or "" for t in run.findall(f"{{{HP}}}t")) == marker)
                char = clone(chars, clones_c, (old, role, inline), originals_c[old])
                source.apply_character(header, char, role, settings)
                if inline:
                    for child in char.findall(f"{{{HH}}}supscript"):
                        char.remove(child)
                    if settings["citation_superscript"]:
                        E.SubElement(char, f"{{{HH}}}supscript")
                run.set("charPrIDRef", char.get("id"))
        _apply_sample_tables(header, section, settings)
        # Current page numbers are native fields, never literal example totals.
        # The source states a page-number rule but has no actual field. The
        # editable projection uses fields evaluated by the actual renderer.
        _page_number(header, section, settings)
        # The copied original line cache encodes positions within the entire
        # 12-page source. It is stale after extracting an appendix or changing
        # formatting, and must never determine the new projection's pages.
        for paragraph in section.iter(f"{{{HP}}}p"):
            for cached in paragraph.findall(f"{{{HP}}}linesegarray"):
                paragraph.remove(cached)
        # Keep section/column/page controls on the original first paragraph.
        # An extra empty source-title host would insert an unrelated 22pt line.
        target_run = section[1].find(f"{{{HP}}}run")
        control_run = section[0].find(f"{{{HP}}}run")
        for position, child in enumerate([node for node in list(control_run) if node.tag.split("}")[-1] in ("secPr", "ctrl")]):
            control_run.remove(child)
            target_run.insert(position, child)
        section.remove(section[0])
        normalize_no_fill(header)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for entry in archive.infolist():
                node = {"Contents/header.xml": header, "Contents/section0.xml": section}.get(entry.filename)
                data = E.tostring(node, encoding="utf-8", xml_declaration=True) if node is not None else archive.read(entry)
                if entry.filename == "Contents/content.hpf":
                    # New artifact metadata must not impersonate the original author.
                    package = E.fromstring(data)
                    for meta in package.iter():
                        if meta.tag.split("}")[-1] == "title":
                            meta.text = PRESETS[preset_id][0]
                        elif meta.tag.split("}")[-1] == "meta":
                            meta.text = ""
                    data = E.tostring(package, encoding="utf-8", xml_declaration=True)
                elif entry.filename == "Preview/PrvText.txt":
                    data = "\r\n".join(_text(p) for p in section.iter(f"{{{HP}}}p")).encode("utf-8")
                target.writestr(copy.copy(entry), data)
    validate(output.getvalue())
    return output.getvalue()


def _preset_roles(preset_id, section):
    result = []
    top_start = PRESETS[preset_id][2]
    for index, host in enumerate(list(section)[1:], top_start):
        for paragraph in host.iter(f"{{{HP}}}p"):
            text = _text(paragraph).strip()
            if not text:
                continue
            role = "body"
            if index in (81,): role = "title"
            elif index in (112, 132): role = "cover_title"
            elif index in (114, 134): role = "cover_date"
            elif index in (124, 138): role = "cover_department"
            elif text.startswith("< 목"): role = "toc_title"
            elif index in (129, 136):
                role = "attachment" if text.startswith("별첨") else "toc_minor" if re.match(r"[가-하]\.", text) else "toc_major"
            elif index in (83, 104): role = "heading"
            elif index in (85, 95): role = "subheading"
            elif index == 87: role = "minor_heading"
            elif index == 89: role = "square"
            elif index == 91: role = "circle"
            elif index == 93: role = "dash"
            elif index == 97: role = "caption"
            elif index == 98: role = "unit"
            elif index == 99: role = "table"
            elif index in (100, 101, 102): role = "table_source"
            result.append((paragraph, role))
    return result


def _splice(paragraph, start, stop, replacement):
    """Replace a known structural span while retaining all other runs/text."""
    nodes = [t for run in paragraph.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t")]
    offset = 0
    inserted = False
    for node in nodes:
        text = node.text or ""
        end = offset + len(text)
        if offset < stop and end > start:
            left, right = max(0, start - offset), min(len(text), stop - offset)
            node.text = text[:left] + (replacement if not inserted else "") + text[right:]
            inserted = True
        offset = end


def _sample_presentation(positions, settings):
    """Known source marker links only; prose, dates and quantities stay intact."""
    from .reference_report import numbered_marker
    result, citations = [], {}
    counters = {role: 0 for role in ("heading", "subheading", "minor_heading", "square", "circle", "dash")}
    patterns = {"heading": r"^(\s*)(\d+\.)(\s)", "subheading": r"^(\s*)([가-하]\.)(\s)",
                "minor_heading": r"^(\s*)((?:\d+\)|\(\d+\)))(\s)",
                "square": r"^(\s*)(□)(\s)", "circle": r"^(\s*)(ㅇ)(\s)", "dash": r"^(\s*)(-)(\s)"}
    for paragraph, role in positions:
        text = _text(paragraph)
        match = re.match(patterns[role], text) if role in patterns else None
        if match:
            counters[role] += 1
            if role in ("heading", "subheading", "minor_heading"):
                level = ("heading", "subheading", "minor_heading").index(role) + 1
                marker = numbered_marker(settings[f"numbered_{level}_marker"], counters[role])
            elif settings["numbering_style"] == "NUMBERED":
                level = ("square", "circle", "dash").index(role) + 4
                marker = numbered_marker(settings[f"numbered_{level}_marker"], counters[role])
                role = "numbered_" + str(level)
            else:
                marker = settings["bullet_" + role + "_marker"]
            # These bounded marker prefixes are the original's indentation
            # mechanism. Paragraph roles now carry their native indentation;
            # retain the marker and the one separating space, not both indents.
            _splice(paragraph, 0, match.end(2), marker)
            text = _text(paragraph)
        link = None
        inline = False
        if role == "caption" and text.rstrip().endswith("현황*"):
            link, inline = (text.rfind("*"), text.rfind("*") + 1, "S1"), True
        elif role == "table" and text.endswith("기업주1)"):
            link, inline = (len(text) - 3, len(text), "S2"), True
        elif role == "table" and text.endswith("실적주2)"):
            link, inline = (len(text) - 3, len(text), "S3"), True
        elif role == "table_source":
            note = re.match(r"^\s*(주1\)|주2\)|\*)(?=\s)", text)
            if note:
                link = (note.start(1), note.end(1), {"*": "S1", "주1)": "S2", "주2)": "S3"}[note[1]])
        if link:
            start, stop, identifier = link
            marker = citation_marker(identifier, settings["citation_style"])
            _splice(paragraph, start, stop, marker)
            if inline:
                citations[paragraph] = marker
        result.append((paragraph, role))
    return result, citations


def _apply_sample_tables(header, section, settings):
    paras = {node.get("id"): node for node in header.iter(f"{{{HH}}}paraPr")}
    fills = header.find(f".//{{{HH}}}borderFills")
    for table in section.iter(f"{{{HP}}}tbl"):
        identifier = table.get("id")
        if identifier in ("1872749260", "1872749251", "1872749253"):
            cover = identifier != "1872749260"
            height = source.mm_to_hwp(settings["cover_frame_height_mm" if cover else "title_frame_height_mm"])
            table.find(f"{{{HP}}}sz").set("height", str(height))
            for cell in table.iter(f"{{{HP}}}tc"):
                cell.find(f"{{{HP}}}cellSz").set("height", str(height))
                for paragraph in cell.iter(f"{{{HP}}}p"):
                    border = paras[paragraph.get("paraPrIDRef")].find(f"{{{HH}}}border")
                    # Original examples use a one-cell title table. Reuse the
                    # common frame fill on that cell, avoiding a second frame
                    # around its paragraph.
                    cell.set("borderFillIDRef", border.get("borderFillIDRef"))
                    border.attrib.update(borderFillIDRef="1", offsetTop="0", offsetBottom="0", offsetLeft="0", offsetRight="0")
            continue
        if identifier in ("1872749252", "1872749254"):
            originals = {node.get("id"): node for node in fills}
            for cell in table.iter(f"{{{HP}}}tc"):
                fill = copy.deepcopy(originals[cell.get("borderFillIDRef")])
                fill.set("id", str(max(int(item.get("id")) for item in fills) + 1))
                for line in fill:
                    if line.tag.split("}")[-1] in ("topBorder", "bottomBorder", "leftBorder", "rightBorder") and line.get("type") != "NONE":
                        line.attrib.update(type=settings["toc_border_style"], color=settings["toc_border_color"], width=f"{settings['toc_border_width_mm']:g} mm")
                fills.append(fill)
                fills.set("itemCnt", str(len(fills)))
                cell.set("borderFillIDRef", fill.get("id"))
                cell.set("hasMargin", "1")
                for side in ("left", "right", "top", "bottom"):
                    cell.find(f"{{{HP}}}cellMargin").set(side, str(source.mm_to_hwp(settings[f"toc_padding_{side}_mm"])))
            continue
        if identifier != "1872749261":
            continue
        apply_table(table, header, settings)


def apply_table(table, header, settings):
    """Data table geometry/fills shared by pristine examples and report layout."""
    fills = header.find(f".//{{{HH}}}borderFills")
    existing = {fill.get("id"): fill for fill in fills}
    rows = table.findall(f"{{{HP}}}tr")
    scope = settings["table_border_scope"]
    min_height = source.mm_to_hwp(settings["table_min_row_height_mm"])
    available = source.mm_to_hwp(settings["page_width_mm"]) - source.mm_to_hwp(settings["margin_left_mm"]) - source.mm_to_hwp(settings["margin_right_mm"])
    size = table.find(f"{{{HP}}}sz")
    old_width = int(size.get("width"))
    width = round(available * settings["table_width_percent"] / 100)
    size.set("width", str(width))
    table.set("repeatHeader", "1" if settings["table_repeat_header"] else "0")
    total = 0
    for y, row in enumerate(rows):
        height = max([int(cell.find(f"{{{HP}}}cellSz").get("height")) for cell in row] + [min_height])
        total += height
        for x, cell in enumerate(row):
            fill = copy.deepcopy(existing[cell.get("borderFillIDRef")])
            new_id = str(max(int(node.get("id")) for node in fills) + 1)
            fill.set("id", new_id)
            for child in list(fill):
                local = child.tag.split("}")[-1]
                if local == "fillBrush":
                    fill.remove(child)
                elif local.endswith("Border"):
                    visible = scope == "ALL" or scope == "OUTER" and (
                        local == "leftBorder" and x == 0 or local == "rightBorder" and x == len(row) - 1 or
                        local == "topBorder" and y == 0 or local == "bottomBorder" and y == len(rows) - 1) or (
                        scope == "NO_OUTER_SIDES" and not (local == "leftBorder" and x == 0 or local == "rightBorder" and x == len(row) - 1)) or scope == "HORIZONTAL" and local in ("topBorder", "bottomBorder")
                    if local == "diagonal": visible = False
                    child.attrib.update(type=settings["table_border_style"] if visible else "NONE",
                        width=f"{settings['table_border_width_mm']:g} mm", color=settings["table_border_color"])
            if y == 0:
                brush = E.SubElement(fill, f"{{{HC}}}fillBrush")
                E.SubElement(brush, f"{{{HC}}}winBrush", dict(faceColor=settings["table_header_fill"], hatchColor="#000000", alpha="0"))
            fills.append(fill)
            fills.set("itemCnt", str(len(fills)))
            cell.set("borderFillIDRef", new_id)
            cell.set("hasMargin", "1")
            cell.find(f"{{{HP}}}subList").set("vertAlign", settings["table_vertical_align"])
            cell.find(f"{{{HP}}}cellSz").set("height", str(height))
            geometry = cell.find(f"{{{HP}}}cellSz")
            geometry.set("width", str(round(int(geometry.get("width")) * width / old_width)))
            margin = cell.find(f"{{{HP}}}cellMargin")
            for side in ("left", "right", "top", "bottom"):
                margin.set(side, str(source.mm_to_hwp(settings[f"table_padding_{side}_mm"])))
    table.find(f"{{{HP}}}sz").set("height", str(total))


def builtin_identity(preset_id):
    profile, _ = load_standard()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hwp-assistant:reference-standard-v3:" + profile["reference_sha256"] + ":" + preset_id))


def _page_number(header, section, settings):
    """Use native current/total page fields, evaluated by the actual renderer."""
    host = section[0]
    ctrl = host.find(f"{{{HP}}}run/{{{HP}}}ctrl")
    if ctrl is None:
        ctrl = E.SubElement(host.find(f"{{{HP}}}run"), f"{{{HP}}}ctrl")
    for control in list(ctrl):
        if control.tag.split("}")[-1] in ("pageNum", "footer"):
            ctrl.remove(control)
    style = settings["page_number_style"]
    E.SubElement(ctrl, f"{{{HP}}}pageNum", dict(pos=settings["page_number_position"] if style == "DASH" else "NONE", formatType="DIGIT", sideChar="-"))
    if style not in ("PLAIN", "PAGE_TOTAL"):
        return
    sample = source.role_samples()["source"]
    char, para = copy.deepcopy(sample["char"]), copy.deepcopy(sample["para"])
    source.apply_character(header, char, "source", settings)
    source.apply_paragraph(para, "source", settings)
    para.find(f"{{{HH}}}align").set("horizontal", settings["page_number_position"].removeprefix("BOTTOM_"))
    for collection_name, element in (("charProperties", char), ("paraProperties", para)):
        collection = header.find(f".//{{{HH}}}{collection_name}")
        element.set("id", str(max(int(item.get("id")) for item in collection) + 1))
        collection.append(element)
        collection.set("itemCnt", str(len(collection)))
    if style == "PAGE_TOTAL":
        from .reference_page_number import page_total_footer
        page_total_footer(header, host, char.get("id"), para.get("id"), settings["page_number_position"])
        return
    footer = E.SubElement(ctrl, f"{{{HP}}}footer", dict(id="910001", applyPageType="BOTH"))
    sub = E.SubElement(footer, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign="TOP", linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0"))
    paragraph = E.SubElement(sub, f"{{{HP}}}p", dict(id="910002", paraPrIDRef=para.get("id"), styleIDRef="0", pageBreak="0", columnBreak="0", merged="0"))
    for kind in ("PAGE",):
        run = E.SubElement(paragraph, f"{{{HP}}}run", dict(charPrIDRef=char.get("id")))
        if kind != "/":
            number = E.SubElement(E.SubElement(run, f"{{{HP}}}ctrl"), f"{{{HP}}}autoNum", dict(num="1", numType=kind))
            E.SubElement(number, f"{{{HP}}}autoNumFormat", dict(type="DIGIT", userChar="", prefixChar="", suffixChar="", supscript="0"))
        E.SubElement(run, f"{{{HP}}}t").text = "/" if kind == "/" else None
