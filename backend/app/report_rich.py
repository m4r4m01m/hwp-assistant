"""Deterministic presentation of fenced report text; no new factual content."""
import copy
import io
import math
import re
import zipfile
from xml.etree import ElementTree as E
from .document_standard import HC, set_margins, layout_metrics, paragraph_line_factor
from .report_hwpx import HP, HH, paragraphs
from .report_layout import lines, width

BLUE = "#245F9E"
POLICY = "tables-flow-emphasis-v1"


def emphasis_ranges(text, emphasis=()):
    flags = [0] * len(text)
    label = re.match(r"^\s*(?:[□ㅇ○-]\s*)?(\([^()]{1,24}\))", text)
    if label:
        for i in range(*label.span(1)): flags[i] |= 1
    for item in emphasis:
        bits = {"bold": 1, "blue": 2, "bold_blue": 3}[item["style"]]
        for match in re.finditer(re.escape(item["text"]), text):
            for i in range(*match.span()): flags[i] |= bits
    return flags


class RichLayout:
    def __init__(self, header, sample, *, visual_settings=None, page_width=481.9, page_budget=625, body_spacing=False):
        self.header = header
        self.chars = header.find(f".//{{{HH}}}charProperties")
        self.paras = header.find(f".//{{{HH}}}paraProperties")
        self.borders = header.find(f".//{{{HH}}}borderFills")
        self.char_map = {e.get("id"): e for e in self.chars}
        self.para_map = {e.get("id"): e for e in self.paras}
        self.variants = {}
        self.styles = {}
        self.serial = 900000
        self.visual_settings = visual_settings
        from .reference_report import is_reference
        self.reference = is_reference(visual_settings)
        self.body_spacing = self.reference and body_spacing
        self.body_gaps = {}
        if self.body_spacing:
            from .reference_report import body_spacing_profile
            self.spacing_profile = body_spacing_profile()
        self.page_width = page_width
        self.page_budget = page_budget
        self.table_size = round(visual_settings.get("table_size_pt", 12) * 100) if visual_settings is not None else 1200
        self.cell_padding = round(visual_settings["table_cell_padding_mm"] * 7200 / 25.4) if visual_settings is not None and "table_cell_padding_mm" in visual_settings else None
        self.cell_border_variants = {}
        base_char = self.char_map[sample.find(f"{{{HP}}}run").get("charPrIDRef")]
        base_para = self.para_map[sample.get("paraPrIDRef")]
        for name, size, align, after in (("cell", 1200, "LEFT", 0), ("card", 1200, "CENTER", 0),
                                        ("caption", 1200, "LEFT", 600), ("host", 100, "CENTER", 1000)):
            if visual_settings is not None and name in ("cell", "card", "caption"):
                size = self.table_size
            if visual_settings is not None and name == "cell":
                align = visual_settings.get("table_horizontal_align", align)
            char, para = copy.deepcopy(base_char), copy.deepcopy(base_para)
            char.attrib.update(height=str(size), textColor="#000000")
            for b in char.findall(f"{{{HH}}}bold"): char.remove(b)
            for tag, value in (("ratio", "100"), ("relSz", "100"), ("spacing", "0"), ("offset", "0")):
                for e in char.findall(f"{{{HH}}}{tag}"):
                    for key in e.attrib: e.set(key, value)
            para.find(f"{{{HH}}}align").set("horizontal", align)
            set_margins(para, after=after)
            line = visual_settings.get("table_line_percent", 125) if visual_settings is not None else 125
            for e in para.iter(f"{{{HH}}}lineSpacing"): e.set("value", str(line))
            para.find(f"{{{HH}}}border").set("borderFillIDRef", "1")
            if self.reference and name != "host":
                from .reference_source import apply_character, apply_paragraph
                reference_role = "caption" if name == "caption" else "table"
                apply_character(header, char, reference_role, visual_settings)
                from .reference_report import native_character
                native_character(char)
                apply_paragraph(para, reference_role, visual_settings)
            self.styles[name] = (self.append(self.chars, char), self.append(self.paras, para))
        self.fills = {}
        for name, shade, line in (("header", "#E8EFF8", True), ("cell", None, True),
                                  ("card", "#E8EFF8", True), ("arrow", None, False)):
            if shade and visual_settings is not None:
                shade = visual_settings.get("table_header_fill", shade)
            fill = copy.deepcopy(self.borders[0])
            for child in list(fill):
                local = child.tag.split("}")[-1]
                if local.endswith("Border"):
                    color = visual_settings.get("table_border_color", "#7595B7") if visual_settings is not None else "#7595B7"
                    child.attrib.update(type="SOLID" if line else "NONE", width="0.1 mm", color=color)
                elif local == "fillBrush": fill.remove(child)
            if shade:
                brush = E.SubElement(fill, f"{{{HC}}}fillBrush")
                E.SubElement(brush, f"{{{HC}}}winBrush", dict(faceColor=shade, hatchColor="#000000", alpha="0"))
            self.fills[name] = self.append(self.borders, fill)

    def append(self, collection, element):
        key = str(max(int(e.get("id")) for e in collection) + 1)
        element.set("id", key); collection.append(element); collection.set("itemCnt", str(len(collection)))
        if collection is self.chars: self.char_map[key] = element
        if collection is self.paras: self.para_map[key] = element
        return key

    def variant(self, cid, bits):
        if not bits: return cid
        key = (cid, bits)
        if key not in self.variants:
            char = copy.deepcopy(self.char_map[cid])
            if bits & 1 and char.find(f"{{{HH}}}bold") is None: E.SubElement(char, f"{{{HH}}}bold")
            if bits & 2: char.set("textColor", self.visual_settings.get("emphasis_color", "#0000FF") if self.reference else BLUE)
            if bits & 4 and char.find(f"{{{HH}}}supscript") is None:
                E.SubElement(char, f"{{{HH}}}supscript")
            if self.reference:
                from .reference_report import native_character
                native_character(char)
            self.variants[key] = self.append(self.chars, char)
        return self.variants[key]

    def space_body(self, p, item, previous):
        if not self.body_spacing:
            return 0
        from .reference_report import body_gap, report_role
        gap = body_gap(report_role(previous) if previous else None, report_role(item), self.visual_settings, self.spacing_profile)
        if not gap:
            return 0
        from .reference_source import apply_paragraph
        old_pid = p.get("paraPrIDRef")
        para = copy.deepcopy(self.para_map[old_pid])
        apply_paragraph(para, "body", {"body_before_pt": gap})
        p.set("paraPrIDRef", self.append(self.paras, para))
        self.body_gaps[p] = (old_pid, gap)
        return gap

    def clear_body_gap(self, p):
        """A new page already separates blocks; do not add an empty top line."""
        if p not in self.body_gaps:
            return 0
        pid, gap = self.body_gaps.pop(p)
        p.set("paraPrIDRef", pid)
        return gap

    def text(self, p, item, *, style=None, limit=None, strong=False):
        cid = p.find(f"{{{HP}}}run").get("charPrIDRef")
        if style:
            cid, pid = self.styles[style]; p.set("paraPrIDRef", pid)
        size = int(self.char_map[cid].get("height")) / 100
        pr = self.para_map[p.get("paraPrIDRef")]
        margins = pr.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")
        margin = {e.tag.split("}")[-1]: int(e.get("value")) / 100 for e in margins}
        if self.reference:
            ratio = float(self.char_map[cid].find(f"{{{HH}}}ratio").get("hangul", "100")) / 100
            spacing = float(self.char_map[cid].find(f"{{{HH}}}spacing").get("hangul", "0")) / 100
            if limit is None:
                limit = (self.page_width - margin["left"] - margin["right"] - max(0, -margin["intent"]) - 10) / size
            limit /= max(.1, ratio + spacing)
        else:
            limit = limit or (self.page_width - margin["left"] - max(0, -margin["intent"]) - 10) / size
        if limit <= 0:
            raise ValueError("Paragraph margins leave no room for report text")
        parts = lines(item["text"], limit, item["kind"] == "title")
        flags = emphasis_ranges(item["text"], item.get("emphasis", []))
        if self.body_spacing:
            from .reference_report import report_role
            if report_role(item) in {"title", "heading", "cover_title", "subheading", "minor_heading"}:
                # A body emphasis phrase can also occur in a heading. The
                # heading's selected role formatting owns its color/weight.
                flags = [0] * len(item["text"])
        if item.get("style_kind") in ("cover_date", "cover_department"):
            flags = [0] * len(item["text"])
        if self.reference:
            for start, end in item.get("citation_ranges", []):
                if not 0 <= start < end <= len(flags):
                    raise ValueError("Invalid frozen citation range")
                for position in range(start, end):
                    flags[position] |= 4
        for child in list(p): p.remove(child)
        offset = 0
        for n, part in enumerate(parts):
            start = 0
            while start < len(part):
                bits = flags[offset + start] | (1 if strong else 0)
                end = start + 1
                while end < len(part) and (flags[offset + end] | (1 if strong else 0)) == bits: end += 1
                run = E.SubElement(p, f"{{{HP}}}run", {"charPrIDRef": self.variant(cid, bits)})
                t = E.SubElement(run, f"{{{HP}}}t"); t.text = part[start:end]
                start = end
            if n < len(parts) - 1: E.SubElement(t, f"{{{HP}}}lineBreak")
            offset += len(part)
        p.set("pageBreak", "0")
        count = sum(max(1, math.ceil(width(part.rstrip()) / limit)) for part in parts)
        factor = paragraph_line_factor(pr) if self.visual_settings is not None else 1.25 if style else 1.3 if item["kind"] == "source" else 1.6
        return count * size * factor + margin["next"] + (margin["prev"] if self.reference or self.visual_settings is not None and "title_vertical_offset_pt" in self.visual_settings else 0)

    def table_fill(self, family, x, y, columns, rows):
        settings = self.visual_settings or {}
        if "table_border_scope" not in settings:
            return self.fills[family]
        scope = settings["table_border_scope"]
        edges = {"leftBorder": scope == "ALL" or scope == "OUTER" and x == 0 or scope == "NO_OUTER_SIDES" and x > 0,
                 "rightBorder": scope == "ALL" or scope == "OUTER" and x == columns - 1 or scope == "NO_OUTER_SIDES" and x < columns - 1,
                 "topBorder": scope in ("ALL", "HORIZONTAL", "NO_OUTER_SIDES") or scope == "OUTER" and y == 0,
                 "bottomBorder": scope in ("ALL", "HORIZONTAL", "NO_OUTER_SIDES") or scope == "OUTER" and y == rows - 1}
        key = (family, tuple(edges.values()))
        if key not in self.cell_border_variants:
            fill = copy.deepcopy(next(item for item in self.borders if item.get("id") == self.fills[family]))
            for child in fill:
                name = child.tag.split("}")[-1]
                if name.endswith("Border"):
                    child.attrib.update(type=settings["table_border_style"] if edges.get(name, False) else "NONE",
                                        width=f"{settings['table_border_width_mm']:g} mm", color=settings["table_border_color"])
            self.cell_border_variants[key] = self.append(self.borders, fill)
        return self.cell_border_variants[key]

    def table(self, entries, original):
        diagram = entries[0]["visual_kind"] == "diagram"
        columns = entries[0]["visual_columns"]
        rows = [entries[i:i + columns] for i in range(0, len(entries), columns)]
        if any(p["visual_row"] != y or p["visual_column"] != x or p["visual_columns"] != columns
               for y, row in enumerate(rows) for x, p in enumerate(row)) or any(len(row) != columns for row in rows):
            raise ValueError("Invalid frozen table geometry")
        table_width = round(self.page_width * 100)
        if self.reference and not diagram:
            table_width = round(table_width * self.visual_settings["table_width_percent"] / 100)
        widths = ([round((table_width - (columns // 2) * 2400) / ((columns + 1) // 2)) if x % 2 == 0 else 2400 for x in range(columns)]
                  if diagram else [table_width // columns] * columns)
        widths[-1] = table_width - sum(widths[:-1])
        self.serial += 1
        cid, pid = self.styles["host"]
        host = E.Element(f"{{{HP}}}p", dict(id=str(self.serial), paraPrIDRef=pid, styleIDRef="0", pageBreak="0", columnBreak="0", merged="0"))
        run = E.SubElement(host, f"{{{HP}}}run", dict(charPrIDRef=cid))
        self.serial += 1
        tbl = E.SubElement(run, f"{{{HP}}}tbl", dict(id=str(self.serial), zOrder="0", numberingType="TABLE", textWrap="TOP_AND_BOTTOM", textFlow="BOTH_SIDES", lock="0", dropcapstyle="None", pageBreak="CELL", repeatHeader="0" if diagram else "1", rowCnt=str(len(rows)), colCnt=str(columns), cellSpacing="0", borderFillIDRef="1", noAdjust="0"))
        sz = E.SubElement(tbl, f"{{{HP}}}sz", dict(width=str(table_width), height="0", widthRelTo="ABSOLUTE", heightRelTo="ABSOLUTE", protect="0"))
        E.SubElement(tbl, f"{{{HP}}}pos", dict(treatAsChar="1", affectLSpacing="0", flowWithText="1", allowOverlap="0", holdAnchorAndSO="0", vertRelTo="PARA", horzRelTo="PARA", vertAlign="TOP", horzAlign="LEFT", vertOffset="0", horzOffset="0"))
        E.SubElement(tbl, f"{{{HP}}}outMargin", dict(left="0", right="0", top="0", bottom="700"))
        padding = {"left": "510", "right": "510", "top": "300", "bottom": "300"}
        if not diagram and self.cell_padding is not None:
            padding = {key: str(self.cell_padding) for key in padding}
        if self.reference and not diagram:
            padding = {side: str(round(self.visual_settings[f"table_padding_{side}_mm"] * 7200 / 25.4)) for side in padding}
            tbl.set("repeatHeader", "1" if self.visual_settings["table_repeat_header"] else "0")
        E.SubElement(tbl, f"{{{HP}}}inMargin", padding)
        total = 0
        for y, row in enumerate(rows):
            tr = E.SubElement(tbl, f"{{{HP}}}tr")
            prepared = []
            for x, item in enumerate(row):
                p = original[item["paragraph"]]
                arrow = diagram and x % 2 == 1
                available = widths[x] - int(padding["left"]) - int(padding["right"])
                if not diagram and (self.reference or self.cell_padding is not None) and available < self.table_size:
                    raise ValueError("Cell padding leaves no room for report text")
                cost = self.text(p, item, style="card" if diagram else "cell", limit=available / self.table_size,
                                 strong=diagram or y == 0 and not self.reference)
                prepared.append((p, cost, "arrow" if arrow else "card" if diagram else "header" if y == 0 else "cell"))
            vertical = int(padding["top"]) + int(padding["bottom"])
            height = max(3000, math.ceil(max(cost for _, cost, _ in prepared) * 100) + (vertical + 300 if not diagram and self.cell_padding is not None else 900))
            if self.reference and not diagram:
                height = max(round(self.visual_settings["table_min_row_height_mm"] * 7200 / 25.4),
                             math.ceil(max(cost for _, cost, _ in prepared) * 100) + vertical)
            total += height
            for x, (p, _, fill) in enumerate(prepared):
                fill_id = self.fills[fill] if diagram else self.table_fill(fill, x, y, columns, len(rows))
                tc = E.SubElement(tr, f"{{{HP}}}tc", dict(name="", header="1" if y == 0 and not diagram else "0", hasMargin="1", protect="0", editable="0", dirty="0", borderFillIDRef=fill_id))
                vertical_align = (self.visual_settings or {}).get("table_vertical_align", "CENTER") if not diagram else "CENTER"
                sl = E.SubElement(tc, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign=vertical_align, linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0"))
                sl.append(p)
                E.SubElement(tc, f"{{{HP}}}cellAddr", dict(colAddr=str(x), rowAddr=str(y)))
                E.SubElement(tc, f"{{{HP}}}cellSpan", dict(colSpan="1", rowSpan="1"))
                E.SubElement(tc, f"{{{HP}}}cellSz", dict(width=str(widths[x]), height=str(height)))
                E.SubElement(tc, f"{{{HP}}}cellMargin", dict(padding))
        sz.set("height", str(total))
        if total > round((self.page_budget - 35) * 100): raise ValueError("Table exceeds a report page")
        return host, total / 100 + 17


def layout_rich_report(data, plan, *, visual_settings=None):
    section, before = paragraphs(data)
    if before != [""] + [p["text"] for p in plan]: raise ValueError("Report text changed before layout")
    page_width, page_budget = layout_metrics(section, visual_settings)
    original = list(section)
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        header = E.fromstring(source.read("Contents/header.xml"))
        from .reference_report import has_body_spacing
        layout = RichLayout(header, original[3], visual_settings=visual_settings, page_width=page_width, page_budget=page_budget,
                            body_spacing=has_body_spacing(plan))
        prepared, index = [], 0
        while index < len(plan):
            item = plan[index]
            if item.get("visual_id"):
                end = index + 1
                while end < len(plan) and plan[end].get("visual_id") == item["visual_id"]: end += 1
                p, cost = layout.table(plan[index:end], original)
                index = end
            else:
                p = original[item["paragraph"]]
                previous = prepared[-1][1] if prepared else None
                if not item.get("visual_caption"):
                    layout.space_body(p, item, previous)
                cost = layout.text(p, item, style="caption" if item.get("visual_caption") else None,
                                   strong=bool(item.get("visual_caption")) and not layout.reference)
                if item.get("visual_caption"):
                    cost += layout.space_body(p, item, previous)
                if layout.reference and item["kind"] == "title":
                    from .reference_report import has_title_layout, has_title_overflow, title_layout_cost
                    if has_title_layout(plan): cost = title_layout_cost(visual_settings, cost, overflow_padding=has_title_overflow(plan))
                index += 1
            prepared.append((p, item, cost))
        for p in list(section)[1:]: section.remove(p)
        used = 0
        from .reference_report import has_cover_layout
        cover_layout = layout.reference and has_cover_layout(plan)
        body_started = False
        for i, (p, item, cost) in enumerate(prepared):
            following = prepared[i + 1][2] if (item["kind"] == "heading" or item.get("visual_caption")) and i + 1 < len(prepared) else 0
            if layout.body_spacing:
                from .reference_report import report_role
                heading_roles = {"heading", "subheading", "minor_heading"}
                if report_role(item) in heading_roles:
                    # Keep an entire consecutive heading chain with its first
                    # prose block when it fits; a middle heading alone is not
                    # useful on the preceding page.
                    following = 0
                    for _, next_item, next_cost in prepared[i + 1:]:
                        following += next_cost
                        if report_role(next_item) not in heading_roles:
                            break
            needed = cost + following if cost + following <= page_budget else cost
            is_cover = cover_layout and (item["kind"] == "title" or item.get("style_kind") in ("cover_date", "cover_department"))
            first_body = cover_layout and not is_cover and not body_started
            if first_body:
                used = 0
                body_started = True
            new_page = first_body or not is_cover and used > 0 and used + needed > page_budget
            if new_page:
                cost -= layout.clear_body_gap(p)
            p.set("pageBreak", "1" if new_page else "0")
            used = cost if new_page else used + cost
            section.append(p)
        # Every verified paragraph remains exactly once, in its original reading order,
        # including table cells and diagram labels. Empty object hosts carry no claims.
        running = {p for control in section.iter() if control.tag in (f"{{{HP}}}header", f"{{{HP}}}footer") for p in control.iter(f"{{{HP}}}p")}
        actual = ["".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
                  for p in section.iter(f"{{{HP}}}p") if p not in running]
        if [s for s in actual if s] != [s for s in before if s]: raise ValueError("Rich layout changed report text")
        if layout.reference:
            from .reference_report import arrange_report
            arrange_report(header, section, visual_settings, plan=plan)
            # Cover layout may add only empty spacers and object hosts; every
            # date/department/title remains the text applied by exact commands.
            after_cover = ["".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
                           for p in section.iter(f"{{{HP}}}p") if p not in running]
            if [s for s in after_cover if s] != [s for s in before if s]:
                raise ValueError("Cover layout changed report text")
        out = io.BytesIO()
        replacements = {"Contents/section0.xml": E.tostring(section, encoding="utf-8", xml_declaration=True),
                        "Contents/header.xml": E.tostring(header, encoding="utf-8", xml_declaration=True)}
        with zipfile.ZipFile(out, "w") as target:
            for entry in source.infolist(): target.writestr(copy.copy(entry), replacements.get(entry.filename, source.read(entry)))
        return out.getvalue()
