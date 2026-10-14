"""Deterministic presentation of fenced report text; no new factual content."""
import copy
import io
import math
import re
import zipfile
from xml.etree import ElementTree as E
from .document_standard import HC, set_margins
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
    def __init__(self, header, sample):
        self.header = header
        self.chars = header.find(f".//{{{HH}}}charProperties")
        self.paras = header.find(f".//{{{HH}}}paraProperties")
        self.borders = header.find(f".//{{{HH}}}borderFills")
        self.char_map = {e.get("id"): e for e in self.chars}
        self.para_map = {e.get("id"): e for e in self.paras}
        self.variants = {}
        self.styles = {}
        self.serial = 900000
        base_char = self.char_map[sample.find(f"{{{HP}}}run").get("charPrIDRef")]
        base_para = self.para_map[sample.get("paraPrIDRef")]
        for name, size, align, after in (("cell", 1200, "LEFT", 0), ("card", 1200, "CENTER", 0),
                                        ("caption", 1200, "LEFT", 600), ("host", 100, "CENTER", 1000)):
            char, para = copy.deepcopy(base_char), copy.deepcopy(base_para)
            char.attrib.update(height=str(size), textColor="#000000")
            for b in char.findall(f"{{{HH}}}bold"): char.remove(b)
            for tag, value in (("ratio", "100"), ("relSz", "100"), ("spacing", "0"), ("offset", "0")):
                for e in char.findall(f"{{{HH}}}{tag}"):
                    for key in e.attrib: e.set(key, value)
            para.find(f"{{{HH}}}align").set("horizontal", align)
            set_margins(para, after=after)
            for e in para.iter(f"{{{HH}}}lineSpacing"): e.set("value", "125")
            para.find(f"{{{HH}}}border").set("borderFillIDRef", "1")
            self.styles[name] = (self.append(self.chars, char), self.append(self.paras, para))
        self.fills = {}
        for name, shade, line in (("header", "#E8EFF8", True), ("cell", None, True),
                                  ("card", "#E8EFF8", True), ("arrow", None, False)):
            fill = copy.deepcopy(self.borders[0])
            for child in list(fill):
                local = child.tag.split("}")[-1]
                if local.endswith("Border"):
                    child.attrib.update(type="SOLID" if line else "NONE", width="0.1 mm", color="#7595B7")
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
            if bits & 2: char.set("textColor", BLUE)
            self.variants[key] = self.append(self.chars, char)
        return self.variants[key]

    def text(self, p, item, *, style=None, limit=None, strong=False):
        cid = p.find(f"{{{HP}}}run").get("charPrIDRef")
        if style:
            cid, pid = self.styles[style]; p.set("paraPrIDRef", pid)
        size = int(self.char_map[cid].get("height")) / 100
        pr = self.para_map[p.get("paraPrIDRef")]
        margins = pr.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")
        margin = {e.tag.split("}")[-1]: int(e.get("value")) / 100 for e in margins}
        limit = limit or (481.9 - margin["left"] - max(0, -margin["intent"]) - 10) / size
        parts = lines(item["text"], limit, item["kind"] == "title")
        flags = emphasis_ranges(item["text"], item.get("emphasis", []))
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
        return count * size * (1.25 if style else 1.3 if item["kind"] == "source" else 1.6) + margin["next"]

    def table(self, entries, original):
        diagram = entries[0]["visual_kind"] == "diagram"
        columns = entries[0]["visual_columns"]
        rows = [entries[i:i + columns] for i in range(0, len(entries), columns)]
        if any(p["visual_row"] != y or p["visual_column"] != x or p["visual_columns"] != columns
               for y, row in enumerate(rows) for x, p in enumerate(row)) or any(len(row) != columns for row in rows):
            raise ValueError("Invalid frozen table geometry")
        widths = ([round((48190 - (columns // 2) * 2400) / ((columns + 1) // 2)) if x % 2 == 0 else 2400 for x in range(columns)]
                  if diagram else [48190 // columns] * columns)
        widths[-1] = 48190 - sum(widths[:-1])
        self.serial += 1
        cid, pid = self.styles["host"]
        host = E.Element(f"{{{HP}}}p", dict(id=str(self.serial), paraPrIDRef=pid, styleIDRef="0", pageBreak="0", columnBreak="0", merged="0"))
        run = E.SubElement(host, f"{{{HP}}}run", dict(charPrIDRef=cid))
        self.serial += 1
        tbl = E.SubElement(run, f"{{{HP}}}tbl", dict(id=str(self.serial), zOrder="0", numberingType="TABLE", textWrap="TOP_AND_BOTTOM", textFlow="BOTH_SIDES", lock="0", dropcapstyle="None", pageBreak="CELL", repeatHeader="0" if diagram else "1", rowCnt=str(len(rows)), colCnt=str(columns), cellSpacing="0", borderFillIDRef="1", noAdjust="0"))
        sz = E.SubElement(tbl, f"{{{HP}}}sz", dict(width="48190", height="0", widthRelTo="ABSOLUTE", heightRelTo="ABSOLUTE", protect="0"))
        E.SubElement(tbl, f"{{{HP}}}pos", dict(treatAsChar="1", affectLSpacing="0", flowWithText="1", allowOverlap="0", holdAnchorAndSO="0", vertRelTo="PARA", horzRelTo="PARA", vertAlign="TOP", horzAlign="LEFT", vertOffset="0", horzOffset="0"))
        E.SubElement(tbl, f"{{{HP}}}outMargin", dict(left="0", right="0", top="0", bottom="700"))
        E.SubElement(tbl, f"{{{HP}}}inMargin", dict(left="510", right="510", top="300", bottom="300"))
        total = 0
        for y, row in enumerate(rows):
            tr = E.SubElement(tbl, f"{{{HP}}}tr")
            prepared = []
            for x, item in enumerate(row):
                p = original[item["paragraph"]]
                arrow = diagram and x % 2 == 1
                cost = self.text(p, item, style="card" if diagram else "cell", limit=(widths[x] - 1020) / 1200,
                                 strong=diagram or y == 0)
                prepared.append((p, cost, "arrow" if arrow else "card" if diagram else "header" if y == 0 else "cell"))
            height = max(3000, math.ceil(max(cost for _, cost, _ in prepared) * 100) + 900)
            total += height
            for x, (p, _, fill) in enumerate(prepared):
                tc = E.SubElement(tr, f"{{{HP}}}tc", dict(name="", header="1" if y == 0 and not diagram else "0", hasMargin="1", protect="0", editable="0", dirty="0", borderFillIDRef=self.fills[fill]))
                sl = E.SubElement(tc, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign="CENTER", linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0"))
                sl.append(p)
                E.SubElement(tc, f"{{{HP}}}cellAddr", dict(colAddr=str(x), rowAddr=str(y)))
                E.SubElement(tc, f"{{{HP}}}cellSpan", dict(colSpan="1", rowSpan="1"))
                E.SubElement(tc, f"{{{HP}}}cellSz", dict(width=str(widths[x]), height=str(height)))
                E.SubElement(tc, f"{{{HP}}}cellMargin", dict(left="510", right="510", top="300", bottom="300"))
        sz.set("height", str(total))
        if total > 59000: raise ValueError("Table exceeds a report page")
        return host, total / 100 + 17


def layout_rich_report(data, plan):
    section, before = paragraphs(data)
    if before != [""] + [p["text"] for p in plan]: raise ValueError("Report text changed before layout")
    original = list(section)
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        header = E.fromstring(source.read("Contents/header.xml"))
        layout = RichLayout(header, original[3])
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
                cost = layout.text(p, item, style="caption" if item.get("visual_caption") else None,
                                   strong=bool(item.get("visual_caption")))
                index += 1
            prepared.append((p, item, cost))
        for p in list(section)[1:]: section.remove(p)
        used = 0
        for i, (p, item, cost) in enumerate(prepared):
            following = prepared[i + 1][2] if (item["kind"] == "heading" or item.get("visual_caption")) and i + 1 < len(prepared) else 0
            needed = cost + following if cost + following <= 625 else cost
            new_page = used > 0 and used + needed > 625
            p.set("pageBreak", "1" if new_page else "0")
            used = cost if new_page else used + cost
            section.append(p)
        # Every verified paragraph remains exactly once, in its original reading order,
        # including table cells and diagram labels. Empty object hosts carry no claims.
        actual = ["".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
                  for p in section.iter(f"{{{HP}}}p")]
        if [s for s in actual if s] != [s for s in before if s]: raise ValueError("Rich layout changed report text")
        out = io.BytesIO()
        replacements = {"Contents/section0.xml": E.tostring(section, encoding="utf-8", xml_declaration=True),
                        "Contents/header.xml": E.tostring(header, encoding="utf-8", xml_declaration=True)}
        with zipfile.ZipFile(out, "w") as target:
            for entry in source.infolist(): target.writestr(copy.copy(entry), replacements.get(entry.filename, source.read(entry)))
        return out.getvalue()
