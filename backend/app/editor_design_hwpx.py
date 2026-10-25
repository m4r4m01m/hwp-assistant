"""Bounded formatting of existing HWPX, with immutable content and old styles.

This is deliberately not report generation: it never adds/deletes body nodes,
rewrites markers, creates a cover/TOC, or replaces an archive resource.
"""
import copy
import io
import re
import zipfile
from xml.etree import ElementTree as E

from fastapi import HTTPException

from .block_contract import MAX_SOURCE
from .block_hwpx import HH, HP, local, parsed, plain_text, serialize_block_xml
from .document_standard import source_parts
from .hwpx import validate
from .reference_report import native_character
from .reference_source import apply_character, apply_paragraph, apply_page, hwp_to_mm, mm_to_hwp
from .reference_standard import apply_table, defaults


_DIRECT_CONTROLS = {"tbl", "pic", "rect", "ellipse", "line", "connectLine", "arc", "polygon", "curve",
                    "container", "compose", "dutmal", "equation", "btn", "checkBtn", "radioBtn", "comboBox", "edit"}
_WRAPPED_CONTROLS = {"colPr", "header", "footer", "footNote", "endNote", "autoNum", "hiddenComment", "indexmark",
                     "fieldBegin", "pageHiding", "pageNumCtrl", "pageNum", "bookmark", "newNum"}


def paragraph_controls(paragraph):
    """Native all-controls indices, never an index among only the tables.

    The pinned parser visits run children in document order. Ambiguous fallback
    controls are rejected rather than targeting a different object by accident.
    """
    controls = []
    for run in paragraph.findall(f"{{{HP}}}run"):
        for node in run:
            name = local(node)
            if name == "t":
                continue
            if name == "secPr":
                controls.append(node)
                controls.extend(node.iter(f"{{{HP}}}colPr"))
            elif name in _DIRECT_CONTROLS:
                controls.append(node)
            elif name == "ctrl":
                for child in node:
                    kind = local(child)
                    if kind in _WRAPPED_CONTROLS:
                        if kind != "hiddenComment" or len(child):
                            controls.append(child)
                    elif kind != "fieldEnd":
                        raise HTTPException(422, "이 문단의 개체 위치를 안전하게 확인할 수 없습니다.")
            else:
                raise HTTPException(422, "이 문단에는 아직 지원하지 않는 개체가 있습니다.")
    return controls


def _collection(header, name):
    result = header.find(f".//{{{HH}}}{name}")
    if result is None:
        raise ValueError("Missing document style collection")
    return result


def _definition(header, collection, identifier):
    result = next((node for node in _collection(header, collection) if node.get("id") == identifier), None)
    if result is None:
        raise ValueError("Missing document style")
    return result


def _append(collection, node):
    node.set("id", str(max((int(item.get("id", "0")) for item in collection), default=-1) + 1))
    collection.append(node)
    collection.set("itemCnt", str(len(collection)))
    return node.get("id")


def _rule_frame(fill):
    if fill is None:
        return False
    edges = {local(node): node for node in fill if local(node).endswith("Border")}
    blue = {"#437fc1", "#107fb5", "#3f7fc1"}
    return all(name in edges and edges[name].get("type") == "SOLID" and edges[name].get("color", "").lower() in blue
               for name in ("topBorder", "bottomBorder")) and all(
                   name not in edges or edges[name].get("type", "NONE") == "NONE" for name in ("leftBorder", "rightBorder"))


def _paragraph_title(header, paragraph):
    para = _definition(header, "paraProperties", paragraph.get("paraPrIDRef"))
    border = para.find(f"{{{HH}}}border")
    known = _definition(header, "styles", paragraph.get("styleIDRef", "0")).get("name")
    return known == "HWP Assistant editor title" or bool(border is not None and _rule_frame(_definition(header, "borderFills", border.get("borderFillIDRef"))))


def _title_table(header, table):
    rows = table.findall(f"{{{HP}}}tr")
    if len(rows) != 1 or len(rows[0]) != 1 or table.get("rowCnt") != "1" or table.get("colCnt") != "1":
        return False
    cell = rows[0][0]
    known = {"HWP Assistant editor title", "HWP Assistant editor cover_title"}
    marked = any(_definition(header, "styles", p.get("styleIDRef", "0")).get("name") in known
                 for p in cell.findall(f"{{{HP}}}subList/{{{HP}}}p"))
    return marked or _rule_frame(_definition(header, "borderFills", cell.get("borderFillIDRef")))


def _title_table_role(header, table):
    cell = table.find(f"{{{HP}}}tr/{{{HP}}}tc")
    fill = _definition(header, "borderFills", cell.get("borderFillIDRef"))
    top = fill.find(f"{{{HH}}}topBorder")
    marked = any(_definition(header, "styles", p.get("styleIDRef", "0")).get("name") == "HWP Assistant editor cover_title"
                 for p in cell.findall(f"{{{HP}}}subList/{{{HP}}}p"))
    return "cover_title" if marked or top is not None and top.get("color", "").lower() == "#107fb5" else "title"


def _simple_table(table):
    rows = table.findall(f"{{{HP}}}tr")
    columns = int(table.get("colCnt", "0"))
    if not rows or len(rows) != int(table.get("rowCnt", "0")) or not 1 <= columns <= 50 or len(rows) > 200:
        raise HTTPException(422, "이 표의 행·열 구조는 서식 적용을 지원하지 않습니다.")
    if len(list(table.iter(f"{{{HP}}}tbl"))) != 1:
        raise HTTPException(422, "표 안의 표에는 서식을 일괄 적용할 수 없습니다.")
    if int(table.find(f"{{{HP}}}sz").get("width", "0")) <= 0:
        raise HTTPException(422, "표 너비를 확인할 수 없습니다.")
    paragraphs = []
    for y, row in enumerate(rows):
        if len(row) != columns or any(local(cell) != "tc" for cell in row):
            raise HTTPException(422, "병합되거나 불규칙한 표는 기존 서식을 유지합니다.")
        for x, cell in enumerate(row):
            span, address = cell.find(f"{{{HP}}}cellSpan"), cell.find(f"{{{HP}}}cellAddr")
            if span is None or address is None or span.get("colSpan") != "1" or span.get("rowSpan") != "1" or address.get("colAddr") != str(x) or address.get("rowAddr") != str(y):
                raise HTTPException(422, "병합된 표는 현재 서식 적용을 지원하지 않습니다.")
            size, margin, sub = cell.find(f"{{{HP}}}cellSz"), cell.find(f"{{{HP}}}cellMargin"), cell.find(f"{{{HP}}}subList")
            if size is None or margin is None or sub is None or int(size.get("width", "0")) <= 0:
                raise HTTPException(422, "표 셀의 크기·여백 정보를 확인할 수 없습니다.")
            for paragraph in sub:
                if local(paragraph) != "p" or paragraph_controls(paragraph):
                    raise HTTPException(422, "개체가 들어 있는 표 셀은 기존 서식을 유지합니다.")
                paragraphs.append((paragraph, y == 0))
    return paragraphs


class Styler:
    def __init__(self, header, settings):
        self.header, self.settings = header, settings
        self.characters, self.paragraphs = {}, {}
        self.changed = set()
        self.tables = 0

    def paragraph(self, paragraph, role, *, frame=False, preserve_emphasis=True):
        old = paragraph.get("paraPrIDRef")
        key = (old, role, frame)
        if key not in self.paragraphs:
            para = copy.deepcopy(_definition(self.header, "paraProperties", old))
            apply_paragraph(para, role, self.settings)
            if frame:
                self.title_frame(para)
            self.paragraphs[key] = _append(_collection(self.header, "paraProperties"), para)
        paragraph.set("paraPrIDRef", self.paragraphs[key])
        first_char = None
        for run in paragraph.findall(f"{{{HP}}}run"):
            original = run.get("charPrIDRef")
            char_key = (original, role, preserve_emphasis)
            if char_key not in self.characters:
                char = copy.deepcopy(_definition(self.header, "charProperties", original))
                settings = self.settings
                if preserve_emphasis:
                    # Existing local emphasis and superscript text are content
                    # cues, not a request to flatten every run to one style.
                    settings = {key: value for key, value in settings.items()
                                if key not in {role + "_bold", role + "_italic", role + "_underline"}}
                apply_character(self.header, char, role, settings)
                native_character(char)
                self.characters[char_key] = _append(_collection(self.header, "charProperties"), char)
            run.set("charPrIDRef", self.characters[char_key])
            first_char = first_char or self.characters[char_key]
        if first_char is not None:
            styles = _collection(self.header, "styles")
            style = copy.deepcopy(_definition(self.header, "styles", paragraph.get("styleIDRef", "0")))
            style.attrib.update(name="HWP Assistant editor " + role, engName="HWP Assistant editor " + role,
                                charPrIDRef=first_char, paraPrIDRef=paragraph.get("paraPrIDRef"))
            identifier = _append(styles, style)
            style.set("nextStyleIDRef", identifier)
            paragraph.set("styleIDRef", identifier)
        self.changed.add(id(paragraph))

    def title_fill(self, prefix="title"):
        _, reference, _ = source_parts()
        fill = copy.deepcopy(_definition(reference, "borderFills", "25" if prefix == "cover" else "7"))
        rule_width = float(self.settings[prefix + "_rule_width_mm"])
        rule_text = f"{rule_width:.1f}" if rule_width.is_integer() else f"{rule_width:g}"
        for child in list(fill):
            name = local(child)
            if name.endswith("Border"):
                child.attrib.update(type="SOLID" if name in ("topBorder", "bottomBorder") else "NONE",
                                    width=rule_text + " mm",
                                    color=self.settings[prefix + "_rule_color"])
            elif name == "fillBrush":
                fill.remove(child)
        return _append(_collection(self.header, "borderFills"), fill)

    def title_frame(self, para):
        border = para.find(f"{{{HH}}}border")
        if border is None:
            border = E.SubElement(para, f"{{{HH}}}border")
        height = mm_to_hwp(self.settings["title_frame_height_mm"])
        line_height = self.settings["title_size_pt"] * self.settings["title_line_percent"]
        offset = max(0, round((height - line_height) / 2))
        border.attrib.update(borderFillIDRef=self.title_fill(), offsetTop=str(offset), offsetBottom=str(offset),
                             offsetLeft="0", offsetRight="0", connect="0", ignoreMargin="1")
        # Paragraph borders extend outside the line box; the renderer does not
        # reserve that extra height in the following paragraph/table's flow.
        # Keep the user's configured margins and add the exterior frame extent
        # (including half the stroke), plus one stroke width of visible clearance
        # so the rule does not visually join a following table's own border.
        # Recompute from settings, not the previous clone, to avoid cumulative
        # spacing growth when the same design is applied again.
        stroke = mm_to_hwp(self.settings["title_rule_width_mm"])
        reserve = offset + (stroke + 1) // 2 + stroke
        apply_paragraph(para, "title", {
            "title_before_pt": self.settings["title_before_pt"] + reserve / 100,
            "title_after_pt": self.settings["title_after_pt"] + reserve / 100,
        })

    def table(self, table):
        paragraphs = _simple_table(table)
        fills = _collection(self.header, "borderFills")
        first_new = len(fills)
        apply_table(table, self.header, self.settings)
        # The native writer spells integral millimetre widths as 1.0, 2.0,
        # etc. Canonicalize only newly cloned definitions, never old styles.
        for fill in list(fills)[first_new:]:
            for edge in fill:
                if local(edge).endswith("Border"):
                    amount = float(edge.get("width").removesuffix(" mm"))
                    edge.set("width", (f"{amount:.1f}" if amount.is_integer() else f"{amount:g}") + " mm")
        for paragraph, _ in paragraphs:
            self.paragraph(paragraph, "table")
        self.tables += 1

    def title_table(self, table, role="title"):
        paragraphs = _simple_table(table)
        if not _title_table(self.header, table):
            raise HTTPException(422, "제목 디자인은 파란 상하선의 한 칸 제목 상자에만 적용할 수 있습니다.")
        cell = table.find(f"{{{HP}}}tr/{{{HP}}}tc")
        prefix = "cover" if role == "cover_title" else "title"
        cell.set("borderFillIDRef", self.title_fill(prefix))
        # The existing object width, positioning and padding remain intact.
        # A larger box is safe; shrinking may clip manually authored content.
        height = max(int(table.find(f"{{{HP}}}sz").get("height", "0")), mm_to_hwp(self.settings[prefix + "_frame_height_mm"]))
        table.find(f"{{{HP}}}sz").set("height", str(height))
        cell.find(f"{{{HP}}}cellSz").set("height", str(height))
        for paragraph, _ in paragraphs:
            self.paragraph(paragraph, role, preserve_emphasis=False)
        self.tables += 1


def _role(header, paragraph):
    text = plain_text(paragraph).strip()
    if not text:
        return None
    if _paragraph_title(header, paragraph):
        return "title"
    for pattern, role in ((r"^□\s+", "square"), (r"^[ㅇ○]\s+", "circle"), (r"^-\s+", "dash"), (r"^․\s+", "dot"),
                          (r"^\d+\.\s+", "heading"), (r"^[가-하]\.\s+", "subheading"), (r"^\(\d+\)\s+", "minor_heading"),
                          (r"^\([가-하]\)\s+", "numbered_4"), (r"^[①-⑳]\s+", "numbered_5"), (r"^[㉮-㉻]\s+", "numbered_6")):
        if re.match(pattern, text):
            # Parenthetical weekdays are prose, never heading evidence.
            if role == "numbered_4" and text[:3] in ("(월)", "(화)", "(수)", "(목)", "(금)", "(토)", "(일)"):
                return None
            return role
    return None


def _content(node, parent=None):
    """Exact structural/content signature with only declared style slots removed."""
    name = local(node)
    if name in ("linesegarray", "lineseg"):
        return None
    allowed = {"p": {"paraPrIDRef", "styleIDRef"}, "run": {"charPrIDRef"}, "tbl": {"repeatHeader"},
               "tc": {"borderFillIDRef", "hasMargin"}, "subList": {"vertAlign"},
               "cellMargin": {"left", "right", "top", "bottom"}, "cellSz": {"width", "height"},
               "pagePr": {"width", "height"}}
    ignored = allowed.get(name, set())
    if name == "sz" and parent == "tbl":
        ignored = {"width", "height"}
    if name == "margin" and parent == "pagePr":
        ignored = {"top", "bottom", "left", "right", "header", "footer"}
    children = tuple(item for child in node if (item := _content(child, name)) is not None)
    return node.tag, tuple(sorted((key, value) for key, value in node.attrib.items() if key not in ignored)), node.text, node.tail, children


def transform(data, target, choice):
    try:
        header, section, _, _ = parsed(data)
        before = _content(section)
        all_settings = {**defaults(), **choice["settings"]}
        whole = target["kind"] == "document"
        if (choice["kind"] == "standard") != whole:
            raise HTTPException(422, "문서 표준은 문서 전체에, 표·제목 디자인은 선택한 부분에 적용하세요.")
        if not whole:
            # A fragment cannot alter page settings or size itself against an
            # unrelated standard's page width.
            page = section.find(f".//{{{HP}}}pagePr")
            margin = page.find(f"{{{HP}}}margin")
            all_settings.update({"page_width_mm": hwp_to_mm(page.get("width")),
                                 "margin_left_mm": hwp_to_mm(margin.get("left")), "margin_right_mm": hwp_to_mm(margin.get("right"))})
        styler = Styler(header, all_settings)
        skipped_paragraphs = skipped_tables = 0
        warnings = []
        if whole:
            for properties in section.iter(f"{{{HP}}}secPr"):
                apply_page(properties, all_settings)
            for paragraph in section:
                controls = paragraph_controls(paragraph)
                tables = [node for node in controls if local(node) == "tbl"]
                for table in tables:
                    try:
                        if _title_table(header, table):
                            # Preserve cover/title object composition; transform
                            # the text style only when its role is unambiguous.
                            styler.title_table(table, _title_table_role(header, table))
                        elif any(plain_text(p).strip() in {"→", "↓", "⇒", "⟶"} for p in table.iter(f"{{{HP}}}p")):
                            # Report flow diagrams are editable table controls,
                            # but their arrow/card geometry is not a data table.
                            skipped_tables += 1
                        else:
                            styler.table(table)
                    except HTTPException:
                        skipped_tables += 1
                nontrivial = [node for node in controls if local(node) not in ("secPr", "colPr", "pageNum", "pageNumCtrl", "newNum", "pageHiding")]
                if nontrivial:
                    if plain_text(paragraph).strip():
                        skipped_paragraphs += 1
                    continue
                role = _role(header, paragraph)
                if role:
                    styler.paragraph(paragraph, role, frame=role == "title", preserve_emphasis=role != "title")
                elif plain_text(paragraph).strip():
                    skipped_paragraphs += 1
            warnings.append("용지·여백과 확인 가능한 문단·표 서식만 적용합니다. 내용·기호·주석 표식과 표지·목차 구성은 유지합니다. 기존 목차는 쪽수를 다시 갱신하세요.")
            if skipped_paragraphs:
                warnings.append(f"역할을 확정할 수 없거나 개체를 포함한 본문 문단 {skipped_paragraphs}개는 기존 글자·문단 서식을 유지했습니다.")
            if skipped_tables:
                warnings.append(f"병합·중첩·개체 셀 등 지원하지 않는 표 {skipped_tables}개는 기존 서식을 유지했습니다.")
        else:
            index = target.get("paragraph")
            if not isinstance(index, int) or not 0 <= index < len(section):
                raise HTTPException(409, "서식을 적용할 문단이 없습니다. 현재 위치를 다시 선택하세요.")
            paragraph = section[index]
            controls = paragraph_controls(paragraph)
            if target["kind"] == "paragraph":
                if choice["kind"] != "title" or any(local(node) not in ("secPr", "colPr", "pageNum", "pageNumCtrl", "newNum", "pageHiding") for node in controls):
                    raise HTTPException(422, "제목 디자인은 개체가 없는 본문 문단에 적용하세요.")
                if not plain_text(paragraph).strip():
                    raise HTTPException(422, "제목으로 표시할 내용이 있는 문단을 선택하세요.")
                styler.paragraph(paragraph, "title", frame=True, preserve_emphasis=False)
            else:
                control = target.get("control")
                if not isinstance(control, int) or not 0 <= control < len(controls) or local(controls[control]) != "tbl":
                    raise HTTPException(409, "선택한 표의 위치가 일치하지 않습니다. 표를 다시 선택하세요.")
                if choice["kind"] == "title":
                    if plain_text(paragraph).strip():
                        raise HTTPException(422, "본문 내용과 함께 배치된 표는 제목 상자로 바꾸지 않습니다.")
                    styler.title_table(controls[control])
                    warnings.append("제목 상자의 기존 너비·위치와 내용을 유지합니다. 높이는 내용 잘림을 피하도록 기존 높이보다 줄이지 않습니다.")
                elif choice["kind"] == "table":
                    if _title_table(header, controls[control]):
                        raise HTTPException(422, "제목 상자에는 표 디자인 대신 제목 디자인을 선택하세요.")
                    styler.table(controls[control])
                else:
                    raise HTTPException(422, "표 또는 제목 디자인을 선택하세요.")
        if _content(section) != before:
            raise HTTPException(422, "서식 적용 중 내용·개체 구조가 달라져 적용하지 않았습니다.")
        # Native line caches are a previous rendering result, not document text.
        for paragraph in section.iter(f"{{{HP}}}p"):
            for cached in paragraph.findall(f"{{{HP}}}linesegarray"):
                paragraph.remove(cached)
        replacements = {"Contents/header.xml": serialize_block_xml(header), "Contents/section0.xml": serialize_block_xml(section)}
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as result:
            for entry in source.infolist():
                result.writestr(copy.copy(entry), replacements.get(entry.filename, source.read(entry)))
        output = out.getvalue()
        if len(output) > MAX_SOURCE:
            raise HTTPException(413, "서식 적용 결과가 10MB를 초과합니다.")
        validate(output)
        return output, dict(changed_paragraphs=len(styler.changed), changed_tables=styler.tables,
                            skipped_paragraphs=skipped_paragraphs, skipped_tables=skipped_tables,
                            page_settings=whole, content_preserved=True, scope="style-only"), warnings
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        raise HTTPException(422, "이 문서의 서식·개체 구조를 안전하게 변경할 수 없습니다. 기존 편집 내용은 그대로입니다.") from exc
