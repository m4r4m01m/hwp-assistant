"""Source-derived controls for new standards, separate from frozen legacy styles.

The bundled HWPX is read through its hash check on every extraction. Paragraph
ordinals and XML paths identify examples: paragraph IDs in this source are not
unique. Explicit writing rules take precedence over incidental example styling;
both values and the rule anchors remain available as provenance.
"""
import copy
import io
import math
import zipfile
from xml.etree import ElementTree as ET

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
LANGUAGES = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
PRESET_IDS = ("appendix-1", "appendix-2", "appendix-3")

# Zero-based document-order paragraph ordinals, including paragraphs in cells.
ROLE_ANCHORS = {
    "title": 391, "heading": 393, "subheading": 395, "minor_heading": 397,
    "body": 399, "square": 399, "circle": 401, "dash": 403, "dot": 399,
    "source": 421, "note": 421, "table_source": 421,
    "caption": 407, "unit": 408, "table": 410,
    "cover_title": 432, "cover_date": 434, "cover_department": 444,
    "toc_title": 451, "toc_major": 456, "toc_minor": 464,
    "attachment": 476,
    "numbered_4": 399, "numbered_5": 399, "numbered_6": 399,
}
ROLE_FIELDS = (
    "font", "size_pt", "ratio_percent", "spacing_percent", "color", "bold",
    "italic", "underline", "horizontal_align", "line_percent", "before_pt",
    "after_pt", "left_indent_pt", "right_indent_pt", "first_line_indent_pt",
)


def _source():
    # Lazy import permits document_standard to call these helpers for new V3
    # artifacts without creating an import cycle or changing its legacy path.
    from .document_standard import load_standard
    profile, data = load_standard()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        header = ET.fromstring(archive.read("Contents/header.xml"))
        section = ET.fromstring(archive.read("Contents/section0.xml"))
    return profile, header, section


def _local(element):
    return element.tag.split("}")[-1]


def _paths(root):
    result = {root: "/" + _local(root)}
    def visit(parent):
        counts = {}
        for child in parent:
            name = _local(child)
            counts[name] = counts.get(name, 0) + 1
            result[child] = f"{result[parent]}/{name}[{counts[name]}]"
            visit(child)
    visit(root)
    return result


def _text(element):
    return "".join("".join(t.itertext()) for t in element.findall(f"{{{HP}}}t"))


def _branch(para):
    case = para.find(f"{{{HP}}}switch/{{{HP}}}case")
    return case if case is not None else para


def _number(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def _values(header, char, para):
    ref = char.find(f"{{{HH}}}fontRef")
    hangul = next(face for face in header.iter(f"{{{HH}}}fontface")
                  if face.get("lang", "").casefold() == "hangul")
    font = next(font.get("face") for font in hangul
                if font.get("id") == ref.get("hangul"))
    ratio = char.find(f"{{{HH}}}ratio")
    spacing = char.find(f"{{{HH}}}spacing")
    underline = char.find(f"{{{HH}}}underline")
    branch = _branch(para)
    margin = branch.find(f"{{{HH}}}margin")
    margins = {_local(node): int(node.get("value")) / 100 for node in margin} if margin is not None else {}
    line = branch.find(f"{{{HH}}}lineSpacing")
    return {
        "font": font, "size_pt": _number(int(char.get("height")) / 100),
        "ratio_percent": int(ratio.get("hangul", "100")) if ratio is not None else 100,
        "spacing_percent": int(spacing.get("hangul", "0")) if spacing is not None else 0,
        "color": char.get("textColor", "#000000").upper(),
        "bold": char.find(f"{{{HH}}}bold") is not None,
        "italic": char.find(f"{{{HH}}}italic") is not None,
        "underline": "BOTTOM" if underline is not None and underline.get("type") == "BOTTOM" else "NONE",
        "horizontal_align": para.find(f"{{{HH}}}align").get("horizontal"),
        "line_percent": int(line.get("value")) if line is not None else 160,
        "before_pt": _number(margins.get("prev", 0)),
        "after_pt": _number(margins.get("next", 0)),
        "left_indent_pt": _number(margins.get("left", 0)),
        "right_indent_pt": _number(margins.get("right", 0)),
        "first_line_indent_pt": _number(margins.get("intent", 0)),
    }


def _rule_overrides(role):
    """Only explicit source rules; the later 15pt supplement is not used here."""
    rules = {
        "ratio_percent": (100, 111), "spacing_percent": (0, 111),
        "color": ("#000000", 115), "line_percent": (160, 123),
    }
    if role in ("source", "note"):
        rules.update(size_pt=(12, 92), line_percent=(130, 123))
    if role in ("caption", "unit", "table", "table_source"):
        rules["line_percent"] = (110, 123)
    if role == "caption":
        rules.update(size_pt=(14, 86), horizontal_align=("CENTER", 177),
                     underline=("BOTTOM", 117))
    if role == "table":
        rules.update(size_pt=(12, 87), horizontal_align=("CENTER", 113))
    if role == "unit":
        rules["size_pt"] = (12, 88)
    if role == "attachment":
        rules.update(size_pt=(20, 89), underline=("BOTTOM", 117))
    # The examples express these hierarchy positions with leading half-width
    # spaces. New report text contains structural markers without those spaces,
    # so the source's one-space rule must live in native paragraph formatting.
    hierarchy_indents = {"subheading": 7.5, "minor_heading": 14,
                         "numbered_4": 21, "numbered_5": 28, "numbered_6": 35}
    if role in hierarchy_indents:
        rules.update(left_indent_pt=(hierarchy_indents[role], 132), first_line_indent_pt=(0, 132))
    return rules


def roles_source():
    """Return independent role char/para clones and source/rule provenance.

    ``char`` and ``para`` are the actual source definitions, before normative
    overrides. ``xml_defaults`` and ``defaults`` explicitly expose differences.
    The attachment anchor is a TOC entry, not a 20pt attachment-title example;
    its 20pt/underline defaults therefore cite the written rules separately.
    """
    profile, header, section = _source()
    paragraphs = list(section.iter(f"{{{HP}}}p"))
    paths = _paths(section)
    chars = {item.get("id"): item for item in header.iter(f"{{{HH}}}charPr")}
    paras = {item.get("id"): item for item in header.iter(f"{{{HH}}}paraPr")}
    result = {}
    for role, ordinal in ROLE_ANCHORS.items():
        paragraph = paragraphs[ordinal]
        runs = paragraph.findall(f"{{{HP}}}run")
        # Do not select leading annotation/spacing runs of another font.
        run = max(runs, key=lambda item: len("".join(_text(item).split())))
        char = chars[run.get("charPrIDRef")]
        para = paras[paragraph.get("paraPrIDRef")]
        xml_values = _values(header, char, para)
        defaults = dict(xml_values)
        overrides = {}
        for field, (value, rule_ordinal) in _rule_overrides(role).items():
            defaults[field] = value
            rule = paragraphs[rule_ordinal]
            overrides[field] = {"value": value, "xml_value": xml_values[field],
                "ordinal": rule_ordinal, "path": paths[rule],
                "text": "".join(_text(item) for item in rule.findall(f"{{{HP}}}run"))}
            if rule_ordinal == 132:
                overrides[field]["interpretation"] = (
                    "하위 항목 한 칸 들여쓰기와 예시의 선행 공백을 문단 서식으로 승계합니다. "
                    "둘째 항목은 15pt 글자의 반 칸 7.5pt, 셋째 이후는 14pt 글자의 반 칸 7pt씩 늘립니다. "
                    "선행 공백은 예시의 알려진 항목 표식 앞에서만 제거합니다.")
        provenance = {"source_sha256": profile["reference_sha256"],
            "part": "Contents/section0.xml", "ordinal": ordinal,
            "path": paths[paragraph], "char_id": char.get("id"),
            "para_id": para.get("id"),
            "text": "".join(_text(item) for item in runs), "rule_overrides": overrides}
        if role == "square":
            # The pristine example uses five leading spaces in a separate 10pt
            # run, not a paragraph margin. The pinned native engine places its
            # square 33 CSS px from the body origin: 24.75pt, not the nominal
            # five-times-half-font estimate of 25pt. Promote this measured
            # prefix once because the sample renderer removes the spaces.
            defaults["left_indent_pt"] = 24.75
            provenance["native_prefix_indent"] = {
                "value_pt": 24.75, "xml_margin_pt": xml_values["left_indent_pt"],
                "prefix": "     ", "prefix_char_id": "64", "prefix_size_pt": 10,
                "rendered_page": 9, "body_origin_x_px": 75.58666666666667,
                "marker_x_px": 108.58666666666667, "css_pixels_per_pt": 96 / 72,
                "engine_wasm_sha256": "e2a21d71f65045ed0d432e33859590e1ae229a654109e4a1f4ceb4aa2eb3b2e0",
                "evidence": "docs/evidence/reference-bullet-indent-2026-10-20.json",
                "interpretation": "원문 별첨1 본문 기호의 선행 공백 위치를 실제 엔진에서 측정해 문단 들여쓰기로 옮깁니다. 번호 제목의 한 칸 규정과 별개인 예시 배치값입니다.",
            }
        if role in ("dot", "numbered_4", "numbered_5", "numbered_6"):
            rule_ordinal = {"dot": 152, "numbered_4": 144, "numbered_5": 145, "numbered_6": 146}[role]
            rule = paragraphs[rule_ordinal]
            provenance["inherited_from"] = "body"
            provenance["marker_rule"] = {"ordinal": rule_ordinal, "path": paths[rule],
                "text": "".join(_text(item) for item in rule.findall(f"{{{HP}}}run"))}
            provenance["limitation"] = "기호·번호 순서는 원본 규칙이며 별도의 상세 글꼴·문단 서식 예시는 없어 본문 서식을 상속합니다."
        if role == "attachment":
            provenance["limitation"] = "이 원본 앵커는 목차의 별첨 항목입니다. 붙임 제목 20pt·밑줄은 원본의 명시 규칙을 우선합니다."
        result[role] = {"char": copy.deepcopy(char), "para": copy.deepcopy(para),
                        "xml_defaults": xml_values, "defaults": defaults,
                        "provenance": provenance}
    return result


def role_defaults():
    """Flat role-prefixed settings, with explicit source-rule precedence."""
    return {f"{role}_{field}": value for role, source in roles_source().items()
            for field, value in source["defaults"].items()}


role_samples = roles_source


def hwp_to_mm(value):
    return float(value) * 25.4 / 7200


def mm_to_hwp(value, original=None):
    """Round to one HWP unit; retain source truncation for equivalent mm values."""
    value = float(value)
    if original is not None and math.isclose(value, hwp_to_mm(original), abs_tol=.005):
        return int(original)
    return round(value * 7200 / 25.4)


mm_to_hwpunits = mm_to_hwp


def page_defaults():
    """Source paper and six margin controls in mm (not a new page preset)."""
    _, _, section = _source()
    page = section.find(f".//{{{HP}}}pagePr")
    margin = page.find(f"{{{HP}}}margin")
    # Restore an integer mm label only within the source unit's rounding error.
    # apply_page retains those exact original units when this label is selected.
    def millimeters(value):
        value = hwp_to_mm(value)
        return round(value) if math.isclose(value, round(value), abs_tol=.005) else round(value, 3)
    return {f"page_{dimension}_mm": millimeters(page.get(dimension))
            for dimension in ("width", "height")} | {
                f"margin_{side}_mm": millimeters(margin.get(side))
                for side in ("top", "bottom", "left", "right", "header", "footer")}


def source_profile():
    """Serializable source/default/provenance metadata for a standard catalog."""
    profile, _, _ = _source()
    roles = roles_source()
    return {"reference_sha256": profile["reference_sha256"],
            "profile_sha256": profile["profile_sha256"],
            "defaults": {**page_defaults(), **{f"{role}_{field}": value
                for role, source in roles.items() for field, value in source["defaults"].items()}},
            "roles": {role: {"xml_defaults": value["xml_defaults"],
                "defaults": value["defaults"], **value["provenance"]}
                for role, value in roles.items()}}


def is_reference_settings(settings):
    return bool(settings) and settings.get("preset_id") in PRESET_IDS


_CHAR_ORDER = {name: index for index, name in enumerate((
    "fontRef", "ratio", "spacing", "relSz", "offset", "bold", "italic",
    "underline", "strikeout", "outline", "shadow", "emboss", "engrave",
    "supscript", "subscript"))}


def _character_child(char, name):
    found = char.find(f"{{{HH}}}{name}")
    if found is not None:
        return found
    child = ET.Element(f"{{{HH}}}{name}")
    index = next((i for i, item in enumerate(char)
                  if _CHAR_ORDER.get(_local(item), 100) > _CHAR_ORDER[name]), len(char))
    char.insert(index, child)
    return child


def apply_character(header, char, role, settings):
    """Apply supplied role values to a cloned character definition in place."""
    def setting(field):
        return settings.get(f"{role}_{field}")
    font = setting("font")
    if font is not None:
        collection = header.find(f".//{{{HH}}}fontfaces")
        if collection is None:
            raise ValueError("Reference header has no font collection")
        refs = _character_child(char, "fontRef")
        for language in LANGUAGES:
            face = next((item for item in collection
                         if item.get("lang", "").casefold() == language), None)
            if face is None:
                face = ET.SubElement(collection, f"{{{HH}}}fontface", lang=language.upper(), fontCnt="0")
            match = next((item for item in face if item.get("face") == font), None)
            if match is None:
                ident = str(max((int(item.get("id")) for item in face), default=-1) + 1)
                match = ET.SubElement(face, f"{{{HH}}}font", id=ident, face=font, type="TTF", isEmbedded="0")
            face.set("fontCnt", str(len(face)))
            refs.set(language, match.get("id"))
        collection.set("itemCnt", str(len(collection)))
    size = setting("size_pt")
    if size is not None:
        char.set("height", str(round(size * 100)))
    color = setting("color")
    if color is not None:
        char.set("textColor", color.upper())
    for field, name in (("ratio_percent", "ratio"), ("spacing_percent", "spacing")):
        value = setting(field)
        if field == "spacing_percent" and role in ("body", "square", "circle", "dash", "dot") and settings.get("body_tracking_percent") is not None:
            value = min(50, max(-20, (value or 0) + settings["body_tracking_percent"]))
        if value is not None:
            child = _character_child(char, name)
            child.attrib.update({language: str(round(value)) for language in LANGUAGES})
    for name in ("bold", "italic"):
        value = setting(name)
        if value is not None:
            for child in char.findall(f"{{{HH}}}{name}"):
                char.remove(child)
            if value:
                _character_child(char, name)
    underline = setting("underline")
    if underline is not None:
        for child in char.findall(f"{{{HH}}}underline"):
            char.remove(child)
        if underline != "NONE":
            _character_child(char, "underline").attrib.update(
                type=underline, shape="SOLID", color=char.get("textColor", "#000000"))
    return char


def apply_paragraph(para, role, settings):
    """Apply role spacing and signed first-line indent in both unit branches."""
    from .standard_policy import LINE_POLICY_EXCLUDED_ROLES, SPACE_COMPRESSION_PERCENT
    # These optional controls never change a historical standard with absent keys.
    if role not in LINE_POLICY_EXCLUDED_ROLES:
        unit = settings.get("line_break_unit")
        if unit in ("WORD", "CHARACTER"):
            breaking = para.find(f"{{{HH}}}breakSetting")
            if breaking is not None:
                # Hancom's Korean lexical enum is inverse to the Latin enum;
                # the pinned renderer's composer/line_breaking.rs documents it.
                breaking.set("breakNonLatinWord", "BREAK_WORD" if unit == "WORD" else "KEEP_WORD")
                breaking.set("breakLatinWord", "KEEP_WORD" if unit == "WORD" else "BREAK_WORD")
        condense = SPACE_COMPRESSION_PERCENT.get(settings.get("line_space_policy"))
        if condense is not None:
            para.set("condense", str(condense))
    alignment = settings.get(f"{role}_horizontal_align")
    if alignment is not None:
        para.find(f"{{{HH}}}align").set("horizontal", alignment)
    line = settings.get(f"{role}_line_percent")
    if line is not None:
        for element in para.iter(f"{{{HH}}}lineSpacing"):
            element.attrib.update(type="PERCENT", value=str(round(line)))
    values = {native: round(settings[f"{role}_{field}"] * 100)
              for field, native in (("left_indent_pt", "left"), ("right_indent_pt", "right"),
                  ("first_line_indent_pt", "intent"), ("before_pt", "prev"), ("after_pt", "next"))
              if f"{role}_{field}" in settings}
    switch = para.find(f"{{{HP}}}switch")
    branches = list(switch) if switch is not None else [para]
    for branch in branches:
        factor = 2 if _local(branch) == "default" else 1
        margin = branch.find(f"{{{HH}}}margin")
        if margin is None:
            continue
        for element in margin:
            value = values.get(_local(element))
            if value is not None:
                element.set("value", str(value * factor))
    return para


def apply_page(sec_pr, settings):
    """Apply paper size and six margins, preserving equivalent source units."""
    page = sec_pr if _local(sec_pr) == "pagePr" else sec_pr.find(f"{{{HP}}}pagePr")
    if page is None:
        raise ValueError("Reference section has no page properties")
    for dimension in ("width", "height"):
        key = f"page_{dimension}_mm"
        if key in settings:
            page.set(dimension, str(mm_to_hwp(settings[key], page.get(dimension))))
    margin = page.find(f"{{{HP}}}margin")
    for side in ("top", "bottom", "left", "right", "header", "footer"):
        key = f"margin_{side}_mm"
        if key in settings:
            margin.set(side, str(mm_to_hwp(settings[key], margin.get(side))))
    return sec_pr
