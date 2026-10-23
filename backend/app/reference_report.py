"""Reference-standard formatting for newly requested reports (revision 3 only)."""
import copy
import json
import math
import re
from xml.etree import ElementTree as E

from .document_standard import HH, HP, source_parts

STANDARD_STYLE = "HWP Assistant Reference Standard:v3"
TOC_ANCHOR_STYLE = "HWP Assistant Reference TOC anchor:v3"
COVER_LAYOUT_POLICY = "reference-cover-v1"
TITLE_LAYOUT_POLICY = "reference-title-v1"
TITLE_OVERFLOW_POLICY = "reference-title-overflow-v1"
BODY_SPACING_POLICY = "reference-body-spacing-v1"
TOC_LAYOUT_LEGACY_POLICY = "reference-toc-frame-v1"
TOC_LAYOUT_POLICY = "reference-toc-frame-v2"
TOC_LAYOUT_POLICIES = (TOC_LAYOUT_LEGACY_POLICY, TOC_LAYOUT_POLICY)
COVER_SPACER_STYLE = "HWP Assistant Reference Cover spacer:v1"


def has_cover_layout(plan):
    return bool(plan and plan[0].get("cover_layout") == COVER_LAYOUT_POLICY)


def has_title_layout(plan):
    return bool(plan and plan[0].get("title_layout") == TITLE_LAYOUT_POLICY)


def has_title_overflow(plan):
    return bool(plan and plan[0].get("title_overflow") == TITLE_OVERFLOW_POLICY)


def has_body_spacing(plan):
    return bool(plan and plan[0].get("body_spacing") == BODY_SPACING_POLICY)


def has_toc_layout(plan):
    return bool(plan and plan[0].get("toc_layout") in TOC_LAYOUT_POLICIES)


def report_role(item):
    if item.get("visual_id"):
        return "table"
    if item.get("visual_caption"):
        return "caption"
    return item.get("style_kind", item["kind"])


def body_spacing_profile():
    """Source blank-line heights, separate from editable paragraph margins.

    Appendix 1 uses native empty paragraphs, not before/after margins: top-level
    84 (16pt) after a heading, 86/88 (14pt) around subordinate headings, 90
    (6pt) before supporting prose and 103 (20pt) before the next main heading.
    The written spacing rule (document-order paragraph 120) requires a 14pt
    blank line generally and 6-20pt by element. Its minimum wins over the
    incidental 3pt spacer at top-level 92. Never alter the source projection.
    """
    from .document_standard import paragraph_line_factor
    _, header, section = source_parts()
    chars = {c.get("id"): c for c in header.iter(f"{{{HH}}}charPr")}
    paras = {p.get("id"): p for p in header.iter(f"{{{HH}}}paraPr")}
    result = {}
    for name, index in (("heading", 103), ("after_heading", 84), ("body", 86), ("support", 90)):
        paragraph = section[index]
        char = chars[paragraph.find(f"{{{HP}}}run").get("charPrIDRef")]
        result[name] = round(int(char.get("height")) / 100 * paragraph_line_factor(paras[paragraph.get("paraPrIDRef")]), 2)
    return result


def body_gap(previous_role, role, settings, profile):
    """Restore the source's separation once, respecting explicit user margins."""
    excluded = {"title", "cover_title", "cover_date", "cover_department"}
    if previous_role is None or previous_role in excluded or role in excluded or role == "table" or role.endswith("_continuation"):
        return 0
    # Continuations inherit the original item's native paragraph margins.
    # Look up that role so its selected after-spacing still replaces our gap.
    previous_role = previous_role.removesuffix("_continuation")
    if settings.get(previous_role + "_after_pt", 0) or settings.get(role + "_before_pt", 0):
        return 0
    if role == "heading":
        return profile["heading"]
    if previous_role == "heading" or role == "subheading":
        return profile["after_heading"]
    if role in {"source", "note", "table_source", "unit"}:
        return 0
    if previous_role in {"subheading", "minor_heading"} or role == "minor_heading":
        return profile["body"]
    if role in {"circle", "dash", "dot"}:
        return profile["support"]
    return profile["body"]


def is_reference(settings):
    return bool(settings and settings.get("preset_id") in ("appendix-1", "appendix-2", "appendix-3"))


def numbered_marker(pattern, index):
    """Expand the first reference marker, retaining user-selected punctuation."""
    for token, replacement in (("1", str(index)), ("가", "가나다라마바사아자차카타파하"[(index - 1) % 14]),
                               ("①", chr(ord("①") + index - 1)), ("㉮", chr(ord("㉮") + (index - 1) % 14))):
        if token in pattern:
            return pattern.replace(token, replacement, 1)
    return pattern


def body_item(text, settings, counters):
    """Resolve structural tokens before freezing text; never change claim prose."""
    match = re.match(r"^([□ㅇ○\-․])\s+", text)
    if not match:
        for pattern, role in ((r"^[가-하]\.\s", "subheading"), (r"^(?:\(\d+\)|\d+\))\s", "minor_heading"),
                              (r"^(?:\([가-하]\)|[가-하]\))\s", "numbered_4"), (r"^[①-⑳]\s", "numbered_5"),
                              (r"^[㉮-㉻]\s", "numbered_6"), (r"^(?:\*+|주\d+\))\s", "note")):
            if re.match(pattern, text):
                return text, role
        return text, "body"
    level = {"□": 0, "ㅇ": 1, "○": 1, "-": 2, "․": 3}[match[1]]
    counters[level] += 1
    counters[level + 1:] = [0] * (3 - level)
    if settings["numbering_style"] == "NUMBERED":
        marker = numbered_marker(settings[f"numbered_{level + 2}_marker"], counters[level])
        role = ("subheading", "minor_heading", "numbered_4", "numbered_5")[level]
    else:
        role = ("square", "circle", "dash", "dot")[level]
        marker = settings[f"bullet_{role}_marker"]
    return marker + " " + text[match.end():], role


def append_style(collection, node):
    node.set("id", str(max((int(e.get("id")) for e in collection), default=-1) + 1))
    collection.append(node)
    collection.set("itemCnt", str(len(collection)))
    return node.get("id")


def native_character(char):
    """Emit new V3 char definitions in the pinned native writer's schema order."""
    defaults = {"underline": dict(type="NONE", shape="SOLID", color="#000000"),
                "strikeout": dict(shape="NONE", color="#000000"), "outline": dict(type="NONE"),
                "shadow": dict(type="NONE", color="#000000", offsetX="0", offsetY="0")}
    for name, attrs in defaults.items():
        if char.find(f"{{{HH}}}{name}") is None:
            E.SubElement(char, f"{{{HH}}}{name}", attrs)
    order = ("fontRef", "ratio", "spacing", "relSz", "offset", "italic", "bold", "underline",
             "strikeout", "outline", "shadow", "emboss", "engrave", "supscript", "subscript")
    char[:] = sorted(char, key=lambda node: order.index(node.tag.split("}")[-1]) if node.tag.split("}")[-1] in order else len(order))


def apply_title_frame(header, para, role, settings):
    """The source's two horizontal rules enclose at least the configured height."""
    if role not in ("title", "cover_title"):
        return
    cover = role == "cover_title"
    fills = header.find(f".//{{{HH}}}borderFills")
    source_id = "25" if cover else "7"
    fill = copy.deepcopy(next(e for e in fills if e.get("id") == source_id))
    prefix = "cover" if cover else "title"
    rule_width = float(settings[prefix + "_rule_width_mm"])
    rule_width_text = f"{rule_width:.1f}" if rule_width.is_integer() else f"{rule_width:g}"
    for child in list(fill):
        local = child.tag.split("}")[-1]
        if local.endswith("Border"):
            child.attrib.update(type="SOLID" if local in ("topBorder", "bottomBorder") else "NONE",
                                width=rule_width_text + " mm",
                                color=settings[prefix + "_rule_color"])
        elif local == "fillBrush":
            fill.remove(child)
    border = para.find(f"{{{HH}}}border")
    height = settings[prefix + "_frame_height_mm"] * 7200 / 25.4
    line_height = settings[role + "_size_pt"] * settings[role + "_line_percent"]
    offset = max(0, round((height - line_height) / 2))
    border.attrib.update(borderFillIDRef=append_style(fills, fill), offsetTop=str(offset), offsetBottom=str(offset),
                         offsetLeft="0", offsetRight="0", connect="0", ignoreMargin="1")


def report_styles(settings):
    from .reference_source import role_samples, apply_character, apply_paragraph, apply_page
    profile, header, section = source_parts()
    samples = role_samples()
    chars = header.find(f".//{{{HH}}}charProperties")
    paras = header.find(f".//{{{HH}}}paraProperties")
    formats = {}
    for role, sample in samples.items():
        char, para = copy.deepcopy(sample["char"]), copy.deepcopy(sample["para"])
        apply_character(header, char, role, settings)
        native_character(char)
        apply_paragraph(para, role, settings)
        apply_title_frame(header, para, role, settings)
        formats[role] = (append_style(chars, char), append_style(paras, para))
    for role in ("square", "circle", "dash", "dot"):
        cid, pid = formats[role]
        para = copy.deepcopy(next(e for e in paras if e.get("id") == pid))
        # Wrapped continuation text retains the first text position of its item.
        continuation = dict(settings)
        continuation[role + "_left_indent_pt"] += max(0, -settings[role + "_first_line_indent_pt"])
        continuation[role + "_first_line_indent_pt"] = 0
        apply_paragraph(para, role, continuation)
        formats[role + "_continuation"] = (cid, append_style(paras, para))
    char, para = copy.deepcopy(samples["body"]["char"]), copy.deepcopy(samples["body"]["para"])
    from .document_standard import set_margins, normalize_no_fill
    char.attrib.update(height="100", shadeColor="#FFFFFFFF", borderFillIDRef="1")
    set_margins(para)
    para.find(f"{{{HH}}}border").set("borderFillIDRef", "1")
    for spacing in para.iter(f"{{{HH}}}lineSpacing"):
        spacing.set("value", "100")
    formats["empty"] = (append_style(chars, char), append_style(paras, para))
    page_char = copy.deepcopy(next(e for e in chars if e.get("id") == formats["source"][0]))
    page_para = copy.deepcopy(next(e for e in paras if e.get("id") == formats["source"][1]))
    page_para.find(f"{{{HH}}}align").set("horizontal", settings["page_number_position"].removeprefix("BOTTOM_"))
    set_margins(page_para)
    formats["page_number"] = (append_style(chars, page_char), append_style(paras, page_para))
    properties = copy.deepcopy(section.find(f".//{{{HP}}}secPr"))
    for child in list(properties):
        if child.tag.split("}")[-1] in ("parameterset", "presentation"):
            properties.remove(child)
    apply_page(properties, settings)
    normalize_no_fill(header)
    # Persist only the formatting consumed by ordinary editor TOC refresh.
    toc_settings = {key: value for key, value in settings.items() if key.startswith("toc_") or key in ("preset_id",)}
    styles = header.find(f".//{{{HH}}}styles")
    marker = copy.deepcopy(styles[0])
    marker.attrib.update(name=STANDARD_STYLE, engName=json.dumps(toc_settings, ensure_ascii=False, sort_keys=True),
                         charPrIDRef=formats["empty"][0], paraPrIDRef=formats["empty"][1])
    append_style(styles, marker)
    return profile, header, properties, formats


def toc_settings(header):
    marker = next((node for node in header.iter(f"{{{HH}}}style") if node.get("name") == STANDARD_STYLE), None)
    if marker is None:
        return None
    from .reference_standard import VisualSettingsV3, defaults
    raw = json.loads(marker.get("engName", ""))
    if not isinstance(raw, dict) or any(not key.startswith("toc_") and key not in ("preset_id", "body_spacing") for key in raw):
        raise ValueError("Invalid reference TOC formatting")
    policy = raw.pop("body_spacing", None)
    if policy is not None and policy != BODY_SPACING_POLICY:
        raise ValueError("Invalid reference spacing policy")
    layout = raw.pop("toc_layout", None)
    if layout is not None and layout not in TOC_LAYOUT_POLICIES:
        raise ValueError("Invalid reference TOC layout policy")
    validated = VisualSettingsV3.model_validate({**defaults(), **raw}).model_dump(mode="json")
    if policy is not None:
        validated["body_spacing"] = policy
    if layout is not None:
        validated["toc_layout"] = layout
    return validated


def arrange_report(header, section, settings, *, plan=None):
    """Blank anchors and page boundaries add no claims or source example text."""
    if not is_reference(settings):
        return
    if has_body_spacing(plan) or has_toc_layout(plan):
        # Ordinary editor TOC updates must keep the same layout generation.
        # Older frozen plans never acquire this marker on replay.
        marker = next(node for node in header.iter(f"{{{HH}}}style") if node.get("name") == STANDARD_STYLE)
        raw = json.loads(marker.get("engName"))
        if has_body_spacing(plan):
            raw["body_spacing"] = BODY_SPACING_POLICY
        if has_toc_layout(plan):
            raw["toc_layout"] = plan[0]["toc_layout"]
        marker.set("engName", json.dumps(raw, ensure_ascii=False, sort_keys=True))
    if settings["preset_id"] == "appendix-1":
        if has_title_layout(plan):
            arrange_source_title(header, section, settings, overflow_padding=has_title_overflow(plan))
        return
    if has_cover_layout(plan):
        arrange_source_cover(header, section, settings, plan)
        return
    # The report title remains the first verified text paragraph. Only its role
    # changes to cover_title when building the scaffold.
    first_body = next((i for i, p in enumerate(section) if i > 1 and any((t.text or "").strip() for t in p.iter(f"{{{HP}}}t"))), None)
    if first_body is None:
        return
    section[first_body].set("pageBreak", "1")
    if settings["preset_id"] == "appendix-2":
        anchor = copy.deepcopy(section[0])
        for child in list(anchor):
            anchor.remove(child)
        E.SubElement(E.SubElement(anchor, f"{{{HP}}}run", dict(charPrIDRef=section[0].find(f"{{{HP}}}run").get("charPrIDRef"))), f"{{{HP}}}t")
        styles = header.find(f".//{{{HH}}}styles")
        marker = copy.deepcopy(styles[0])
        marker.attrib.update(name=TOC_ANCHOR_STYLE, engName=TOC_ANCHOR_STYLE,
                             charPrIDRef=anchor.find(f"{{{HP}}}run").get("charPrIDRef"), paraPrIDRef=anchor.get("paraPrIDRef"))
        anchor.attrib.update(id="980000", pageBreak="1", styleIDRef=append_style(styles, marker))
        section.insert(first_body, anchor)


def source_empty_paragraph(header, source_header, source, settings=None, role=None):
    """Clone only the source's empty paragraph formatting, with new local IDs."""
    from .reference_source import apply_character, apply_paragraph
    from .document_standard import normalize_no_fill
    source_char = source.find(f"{{{HP}}}run").get("charPrIDRef")
    char = copy.deepcopy(next(c for c in source_header.iter(f"{{{HH}}}charPr") if c.get("id") == source_char))
    para = copy.deepcopy(next(p for p in source_header.iter(f"{{{HH}}}paraPr") if p.get("id") == source.get("paraPrIDRef")))
    if role:
        apply_character(header, char, role, settings)
        apply_paragraph(para, role, settings)
    native_character(char)
    normalize_no_fill(char)
    node = E.Element(f"{{{HP}}}p", {**source.attrib, "pageBreak": "0", "columnBreak": "0", "styleIDRef": "0"})
    node.set("paraPrIDRef", append_style(header.find(f".//{{{HH}}}paraProperties"), para))
    E.SubElement(E.SubElement(node, f"{{{HP}}}run", dict(charPrIDRef=append_style(header.find(f".//{{{HH}}}charProperties"), char))), f"{{{HP}}}t")
    return node


def overflow_title_padding(settings, prefix, original):
    # A rule is centered on the cell edge. Leave 3pt of clear space inside
    # its painted half-width when text makes the source frame grow.
    safe = math.ceil(settings[prefix + "_rule_width_mm"] * 7200 / 25.4 / 2 + 300)
    return {side: max(int(original[side]), safe) for side in ("top", "bottom")}


def source_title_box(header, section, title, settings, host, source_index, prefix, *, overflow_padding=False):
    """Place one fenced title in the original native table, with chosen styling."""
    from .document_standard import layout_metrics
    _, source_header, source_section = source_parts()
    source = source_section[source_index]
    paras = header.find(f".//{{{HH}}}paraProperties")
    table = copy.deepcopy(source.find(f".//{{{HP}}}tbl"))
    for child in list(table):
        if child.tag == f"{{{HP}}}cellzoneList": table.remove(child)
    cell = table.find(f"{{{HP}}}tr/{{{HP}}}tc")
    sub = cell.find(f"{{{HP}}}subList")
    for child in list(sub): sub.remove(child)
    # The cell supplies the blue rules and vertical centering. The fenced
    # title paragraph retains the chosen font and spacing without doubling
    # its previous paragraph border.
    para = copy.deepcopy(next(p for p in paras if p.get("id") == title.get("paraPrIDRef")))
    para.find(f"{{{HH}}}border").attrib.update(borderFillIDRef="1", offsetTop="0", offsetBottom="0", offsetLeft="0", offsetRight="0")
    title.set("paraPrIDRef", append_style(paras, para))
    title.set("pageBreak", "0")
    sub.append(title)
    fills = header.find(f".//{{{HH}}}borderFills")
    fill = copy.deepcopy(next(p for p in source_header.iter(f"{{{HH}}}borderFill") if p.get("id") == cell.get("borderFillIDRef")))
    rule = float(settings[prefix + "_rule_width_mm"])
    rule_text = f"{rule:.1f}" if rule.is_integer() else f"{rule:g}"
    for child in list(fill):
        if child.tag.split("}")[-1].endswith("Border"):
            child.attrib.update(type="SOLID" if child.tag.split("}")[-1] in ("topBorder", "bottomBorder") else "NONE",
                                width=rule_text + " mm", color=settings[prefix + "_rule_color"])
        elif child.tag.split("}")[-1] == "fillBrush": fill.remove(child)
    cell.set("borderFillIDRef", append_style(fills, fill))
    page_width, _ = layout_metrics(section, settings)
    # Preserve the source's width inside the configured page's text area.
    source_page = source_section.find(f".//{{{HP}}}pagePr")
    source_margin = source_page.find(f"{{{HP}}}margin")
    source_width = int(source_page.get("width")) - int(source_margin.get("left")) - int(source_margin.get("right"))
    old_width = int(table.find(f"{{{HP}}}sz").get("width"))
    box_width = max(100, round(page_width * 100) - (source_width - old_width))
    box_height = round(settings[prefix + "_frame_height_mm"] * 7200 / 25.4)
    if overflow_padding:
        from .document_standard import paragraph_line_factor
        char = next(c for c in header.iter(f"{{{HH}}}charPr") if c.get("id") == title.find(f"{{{HP}}}run").get("charPrIDRef"))
        size = int(char.get("height"))
        line_count = 1 + len(list(title.iter(f"{{{HP}}}lineBreak")))
        margins = para.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")
        vertical = sum(int(node.get("value")) for node in margins if node.tag in (f"{{{HH}}}prev", f"{{{HH}}}next"))
        content_height = math.ceil(size + (line_count - 1) * size * paragraph_line_factor(para) + vertical)
        padding = cell.find(f"{{{HP}}}cellMargin")
        if content_height + int(padding.get("top")) + int(padding.get("bottom")) > box_height:
            safe_padding = overflow_title_padding(settings, prefix, padding.attrib)
            padding.attrib.update({side: str(value) for side, value in safe_padding.items()})
            box_height = content_height + sum(safe_padding.values())
    table.find(f"{{{HP}}}sz").attrib.update(width=str(box_width), height=str(box_height))
    cell.find(f"{{{HP}}}cellSz").attrib.update(width=str(box_width), height=str(box_height))
    host_run = host.find(f"{{{HP}}}run")
    # The native writer omits an empty t beside a table/control. Emit its
    # canonical host shape now so a TOC round trip changes no semantics.
    for text in host_run.findall(f"{{{HP}}}t"): host_run.remove(text)
    host_run.append(table)
    return host


def title_layout_cost(settings, text_cost, *, overflow_padding=False):
    """Budget the original title table and following empty paragraph."""
    from .document_standard import paragraph_line_factor
    _, header, section = source_parts()
    chars = {c.get("id"): c for c in header.iter(f"{{{HH}}}charPr")}
    paras = {p.get("id"): p for p in header.iter(f"{{{HH}}}paraPr")}
    table = section[81].find(f".//{{{HP}}}tbl")
    outer = table.find(f"{{{HP}}}outMargin")
    cell = table.find(f"{{{HP}}}tr/{{{HP}}}tc/{{{HP}}}cellMargin")
    padding = {side: int(cell.get(side)) for side in ("top", "bottom")}
    if overflow_padding and text_cost + sum(padding.values()) / 100 > settings["title_frame_height_mm"] * 72 / 25.4:
        padding = overflow_title_padding(settings, "title", padding)
    height = max(settings["title_frame_height_mm"] * 72 / 25.4,
                 text_cost + sum(padding.values()) / 100)
    height += (int(outer.get("top")) + int(outer.get("bottom"))) / 100
    for index in (81, 82):
        p = section[index]
        size = int(chars[p.find(f"{{{HP}}}run").get("charPrIDRef")].get("height")) / 100
        factor = paragraph_line_factor(paras[p.get("paraPrIDRef")])
        height += size * (max(0, factor - 1) if index == 81 else factor)
    return height


def arrange_source_title(header, section, settings, *, overflow_padding=False):
    """Appendix 1's 15mm title box reserves space before its first heading."""
    _, source_header, source_section = source_parts()
    serial = max((int(e.get("id")) for e in section.iter() if (e.get("id") or "").isdigit()), default=980000) + 1
    host = source_empty_paragraph(header, source_header, source_section[81])
    source_title_box(header, section, section[1], settings, host, 81, "title", overflow_padding=overflow_padding)
    for child in host.iter():
        if child.tag in (f"{{{HP}}}p", f"{{{HP}}}tbl"):
            child.set("id", str(serial)); serial += 1
    spacer = source_empty_paragraph(header, source_header, source_section[82])
    spacer.set("id", str(serial))
    styles = header.find(f".//{{{HH}}}styles")
    marker = copy.deepcopy(styles[0])
    marker.attrib.update(name=COVER_SPACER_STYLE, engName=COVER_SPACER_STYLE,
                         charPrIDRef=spacer.find(f"{{{HP}}}run").get("charPrIDRef"), paraPrIDRef=spacer.get("paraPrIDRef"))
    spacer.set("styleIDRef", append_style(styles, marker))
    section[1] = host
    section.insert(2, spacer)


def arrange_source_cover(header, section, settings, plan):
    """Use the original cover's empty paragraphs and title table, with fenced text.

    Only plans carrying COVER_LAYOUT_POLICY enter here. Native layout remains
    responsible for overflow; long titles and large TOCs are never compressed.
    """
    _, source_header, source_section = source_parts()
    styles = header.find(f".//{{{HH}}}styles")
    serial = max((int(e.get("id")) for e in section.iter() if (e.get("id") or "").isdigit()), default=980000) + 1

    def identity(node):
        nonlocal serial
        for child in node.iter():
            if child.tag in (f"{{{HP}}}p", f"{{{HP}}}tbl"):
                child.set("id", str(serial)); serial += 1
        return node

    def marker(node, name):
        style = copy.deepcopy(styles[0])
        style.attrib.update(name=name, engName=name, charPrIDRef=node.find(f"{{{HP}}}run").get("charPrIDRef"),
                            paraPrIDRef=node.get("paraPrIDRef"))
        node.set("styleIDRef", append_style(styles, style))
        return node

    def blank(index, role=None, *, name=COVER_SPACER_STYLE):
        node = source_empty_paragraph(header, source_header, source_section[index], settings, role)
        return identity(marker(node, name))

    def anchor():
        # A 1pt empty anchor is replaced by the existing measured TOC engine.
        node = copy.deepcopy(section[0])
        run = node.find(f"{{{HP}}}run")
        cid = run.get("charPrIDRef")
        for child in list(node): node.remove(child)
        E.SubElement(E.SubElement(node, f"{{{HP}}}run", dict(charPrIDRef=cid)), f"{{{HP}}}t")
        node.attrib.update(pageBreak="1" if settings["preset_id"] == "appendix-2" else "0", columnBreak="0")
        return identity(marker(node, TOC_ANCHOR_STYLE))

    def title_box(title):
        index = 112 if settings["preset_id"] == "appendix-2" else 132
        host = blank(index)
        host.set("styleIDRef", "0")
        return identity(source_title_box(header, section, title, settings, host, index, "cover", overflow_padding=has_title_overflow(plan)))

    original = list(section)
    title = original[1]
    position = 2
    cover_nodes = {}
    for item in plan[1:]:
        role = item.get("style_kind")
        if role not in ("cover_date", "cover_department"): break
        cover_nodes[role] = original[position]
        cover_nodes[role].set("pageBreak", "0")
        position += 1
    body = original[position:]
    if not body:
        raise ValueError("Reference cover requires report body")
    body[0].set("pageBreak", "1")
    date = cover_nodes.get("cover_date")
    department = cover_nodes.get("cover_department")
    if settings["preset_id"] == "appendix-2":
        cover = [blank(111), title_box(title), blank(113), date if date is not None else blank(114, "cover_date")]
        cover.extend(blank(i) for i in range(115, 124))
        cover.extend([department if department is not None else blank(124, "cover_department"), anchor()])
    else:
        cover = [blank(131), title_box(title), blank(133), date if date is not None else blank(134, "cover_date"),
                 blank(135), anchor(), blank(137), department if department is not None else blank(138, "cover_department")]
    # Reuse the leading source spacer as the section-control host, matching its
    # height exactly instead of adding the scaffold's extra 1pt empty line.
    leading_run = cover[0].find(f"{{{HP}}}run")
    controls = [child for run in original[0].findall(f"{{{HP}}}run") for child in run if child.tag != f"{{{HP}}}t"]
    for text in leading_run.findall(f"{{{HP}}}t"): leading_run.remove(text)
    for child in reversed(controls): leading_run.insert(0, child)
    section[:] = [*cover, *body]


def toc_anchor_index(header, section):
    ids = {s.get("id") for s in header.iter(f"{{{HH}}}style") if s.get("name") == TOC_ANCHOR_STYLE}
    return next((i for i, p in enumerate(section) if p.get("styleIDRef") in ids), 1)
