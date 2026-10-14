"""Append-only HWPX block composition, preserving the user's exported buffer."""
import copy
import io
import math
import re
import zipfile
from xml.etree import ElementTree as E
from xml.sax.saxutils import escape, quoteattr
from fastapi import HTTPException
from .block_contract import MAX_SOURCE, block_texts
from .document_standard import HH, HC, HP, report_styles, set_margins
from .hwpx import digest, validate
from .report_layout import lines, width
from .report_rich import emphasis_ranges

COLLECTIONS = {"borderFillIDRef": ("borderFills", "borderFill"), "charPrIDRef": ("charProperties", "charPr"),
               "paraPrIDRef": ("paraProperties", "paraPr"), "tabPrIDRef": ("tabProperties", "tabPr"),
               "styleIDRef": ("styles", "style"), "nextStyleIDRef": ("styles", "style")}


def local(node):
    return node.tag.split("}")[-1]


NAMESPACES = {
    HH: "hh", HP: "hp", HC: "hc", "http://www.hancom.co.kr/hwpml/2011/section": "hs",
    "http://www.hancom.co.kr/hwpml/2011/app": "ha", "http://www.hancom.co.kr/hwpml/2016/paragraph": "hp10",
    "http://www.hancom.co.kr/hwpml/2011/history": "hhs", "http://www.hancom.co.kr/hwpml/2011/master-page": "hm",
    "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar": "hwpunitchar",
    "http://purl.org/dc/elements/1.1/": "dc", "http://www.idpf.org/2007/opf/": "opf",
    "http://www.idpf.org/2007/ops": "epub", "http://www.hancom.co.kr/schema/2011/hpf": "hpf",
    "urn:oasis:names:tc:opendocument:xmlns:config:1.0": "config",
    "http://www.w3.org/XML/1998/namespace": "xml",
}


def serialize_block_xml(root):
    # The native serializer preserves supplemental XML as raw fragments and emits
    # only its fixed prefixes. Local encoding must not depend on ElementTree's
    # process-global namespace registrations made by unrelated report requests.
    used = {name[1:].split("}", 1)[0] for node in root.iter() for name in (node.tag, *node.attrib) if name.startswith("{")}
    prefixes = {uri: NAMESPACES.get(uri, "extra" + str(index)) for index, uri in enumerate(sorted(used))}
    def qualified(name):
        if not name.startswith("{"): return name
        uri, suffix = name[1:].split("}", 1)
        return prefixes[uri] + ":" + suffix
    def text(value):
        # XML normalizes literal CR to LF on parse. Numeric references preserve
        # the exact character from an existing text node or mixed-content tail.
        return escape(value or "").replace("\r", "&#13;")
    def render(node, first=False):
        attributes = "".join(" " + qualified(name) + "=" + quoteattr(value) for name, value in node.attrib.items())
        if first:
            attributes += "".join(" xmlns:" + prefix + "=" + quoteattr(uri) for uri, prefix in sorted(prefixes.items()) if prefix != "xml")
        tag = qualified(node.tag)
        if not len(node) and node.text is None: value = "<" + tag + attributes + "/>"
        else:
            value = "<" + tag + attributes + ">" + text(node.text)
            value += "".join(render(child) for child in node)
            value += "</" + tag + ">"
        return value + text(node.tail)
    return ("<?xml version='1.0' encoding='utf-8'?>\n" + render(root, True)).encode("utf-8")


def parsed(data):
    _, roots = validate(data)
    sections = [name for name in roots if re.fullmatch(r"Contents/section\d+\.xml", name)]
    if sections != ["Contents/section0.xml"]:
        raise HTTPException(422, "블록 삽입은 단일 본문 구역만 지원합니다.")
    header, section = roots["Contents/header.xml"], roots["Contents/section0.xml"]
    if any(local(node) in ("fieldBegin", "fieldEnd") for node in section.iter()):
        raise HTTPException(422, "필드가 포함된 문서는 아직 블록 삽입을 지원하지 않습니다.")
    if header.find(f"{{{HH}}}refList") is None or any(local(p) != "p" for p in section):
        raise HTTPException(422, "지원하는 본문·서식 구조가 아닙니다.")
    if any(node.get("colCount", "1") != "1" for node in section.iter(f"{{{HP}}}colPr")):
        raise HTTPException(422, "다단 문서에는 블록을 삽입할 수 없습니다.")
    page = section.find(f".//{{{HP}}}pagePr")
    margin = page.find(f"{{{HP}}}margin") if page is not None else None
    if page is None or margin is None:
        raise HTTPException(422, "본문의 용지·여백 정보를 확인할 수 없습니다.")
    available = int(page.get("width")) - int(margin.get("left")) - int(margin.get("right"))
    if available < 20000 or available > 100000:
        raise HTTPException(422, "지원하는 본문 폭 범위가 아닙니다.")
    return header, section, roots, available


def plain_text(paragraph):
    return "".join("".join(t.itertext()) for run in paragraph.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))


def node_value(node, *, ignore_id=False):
    return (node.tag, tuple(sorted((key, value) for key, value in node.attrib.items() if not (ignore_id and key == "id"))),
            node.text or "", tuple(node_value(child, ignore_id=ignore_id) for child in node))


class MergeStyles:
    def __init__(self, destination, reference):
        self.dest = destination.find(f"{{{HH}}}refList")
        self.ref = reference.find(f"{{{HH}}}refList")
        self.maps = {}

    def collection(self, root, name):
        node = root.find(f"{{{HH}}}{name}")
        if node is None:
            node = E.SubElement(root, f"{{{HH}}}{name}", {"itemCnt": "0"})
        return node

    def append(self, name, element):
        collection = self.collection(self.dest, name)
        item = copy.deepcopy(element)
        new_id = str(max([int(e.get("id", "0")) for e in collection] + [-1]) + 1)
        item.set("id", new_id); collection.append(item); collection.set("itemCnt", str(len(collection)))
        return new_id

    def font(self, language, old_id):
        key = ("font", language.casefold(), old_id)
        if key not in self.maps:
            source_faces = self.ref.find(f"{{{HH}}}fontfaces")
            source = next((face for face in source_faces if face.get("lang", "").casefold() == language.casefold()), None)
            font = next((f for f in source if f.get("id") == old_id), None) if source is not None else None
            if font is None: raise ValueError("Missing standard font")
            faces = self.collection(self.dest, "fontfaces")
            face = next((face for face in faces if face.get("lang", "").casefold() == language.casefold()), None)
            if face is None:
                face = E.SubElement(faces, source.tag, {**source.attrib, "fontCnt": "0"})
            existing = next((f for f in face if node_value(f, ignore_id=True) == node_value(font, ignore_id=True)), None)
            if existing is None:
                existing = copy.deepcopy(font)
                existing.set("id", str(max([int(f.get("id", "0")) for f in face] + [-1]) + 1))
                face.append(existing); face.set("fontCnt", str(len(face)))
            faces.set("itemCnt", str(len(faces)))
            self.maps[key] = existing.get("id")
        return self.maps[key]

    def mapped(self, name, old_id):
        key = (name, old_id)
        if key not in self.maps:
            original = next((e for e in self.collection(self.ref, name) if e.get("id") == old_id), None)
            if original is None: raise ValueError("Missing standard style")
            item = copy.deepcopy(original)
            for node in item.iter():
                for attr, value in list(node.attrib.items()):
                    if attr in COLLECTIONS:
                        node.set(attr, self.mapped(COLLECTIONS[attr][0], value))
                    elif local(node) == "fontRef":
                        node.set(attr, self.font(attr, value))
                    elif attr == "binaryItemIDRef":
                        raise ValueError("Block styles may not import binary resources")
            if name == "borderFills":
                # Native BorderFill export spells the transparent COLORREF sentinel
                # explicitly. Restrict this encoding to newly imported standard fills.
                for brush in item.iter(f"{{{HC}}}winBrush"):
                    if brush.get("faceColor", "").casefold() == "none": brush.set("faceColor", "#FFFFFFFF")
            if name == "charProperties":
                # The pinned engine emits these explicit disabled effects on load/export.
                # Complete only new standard styles; never rewrite existing definitions.
                defaults = {"underline": dict(type="NONE", shape="SOLID", color="#000000"),
                            "strikeout": dict(shape="NONE", color="#000000"), "outline": dict(type="NONE"),
                            "shadow": dict(type="NONE", color="#000000", offsetX="0", offsetY="0")}
                for tag, attrs in defaults.items():
                    if item.find(f"{{{HH}}}{tag}") is None: E.SubElement(item, f"{{{HH}}}{tag}", attrs)
            self.maps[key] = self.append(name, item)
        return self.maps[key]

    def own_style(self, cid, pid, name):
        collection = self.collection(self.dest, "styles")
        new_id = str(max([int(e.get("id", "0")) for e in collection] + [-1]) + 1)
        style = E.Element(f"{{{HH}}}style", dict(id=new_id, type="PARA", name=name, engName=name,
                         paraPrIDRef=pid, charPrIDRef=cid, nextStyleIDRef=new_id, langID="1042", lockForm="0"))
        collection.append(style); collection.set("itemCnt", str(len(collection)))
        return new_id


class BlockBuilder:
    def __init__(self, header, section, available):
        _, reference, _, self.formats = report_styles()
        self.reference, self.header = reference, header
        self.merge = MergeStyles(header, reference)
        self.available = available
        self.serial = max([int(e.get("id")) for e in section.iter() if (e.get("id") or "").isdigit()] + [0])
        self.styles = {}; self.variants = {}; self.fills = {}

    def next_id(self):
        self.serial += 1
        if self.serial > 4294967295: raise ValueError("No available block object ID")
        return str(self.serial)

    def style(self, role):
        if role not in self.styles:
            source_role = "source" if role in ("cell", "caption", "host") else role
            cid, pid = self.formats[source_role]
            if role in ("cell", "caption", "host"):
                chars = self.reference.find(f".//{{{HH}}}charProperties")
                paras = self.reference.find(f".//{{{HH}}}paraProperties")
                char = copy.deepcopy(next(c for c in chars if c.get("id") == cid))
                para = copy.deepcopy(next(p for p in paras if p.get("id") == pid))
                cid = str(max(int(e.get("id")) for e in chars) + 1); char.set("id", cid)
                pid = str(max(int(e.get("id")) for e in paras) + 1); para.set("id", pid)
                char.set("height", "100" if role == "host" else "1200")
                para.find(f"{{{HH}}}align").set("horizontal", "CENTER")
                for spacing in para.iter(f"{{{HH}}}lineSpacing"): spacing.set("value", "110")
                set_margins(para, after=600 if role == "caption" else 1000 if role == "host" else 0)
                if role == "caption": E.SubElement(char, f"{{{HH}}}underline", dict(type="BOTTOM", shape="SOLID", color="#000000"))
                chars.append(char); paras.append(para)
            mapped_cid, mapped_pid = self.merge.mapped("charProperties", cid), self.merge.mapped("paraProperties", pid)
            self.styles[role] = (mapped_cid, mapped_pid, self.merge.own_style(mapped_cid, mapped_pid, "HWP Assistant " + role))
        return self.styles[role]

    def bold(self, cid):
        if cid not in self.variants:
            chars = self.header.find(f".//{{{HH}}}charProperties")
            char = copy.deepcopy(next(c for c in chars if c.get("id") == cid))
            if char.find(f"{{{HH}}}bold") is None:
                index = next((i for i, child in enumerate(char) if local(child) == "underline"), len(char))
                char.insert(index, E.Element(f"{{{HH}}}bold"))
            self.variants[cid] = self.merge.append("charProperties", char)
        return self.variants[cid]

    def paragraph(self, text, role, *, strong=False, limit=None):
        cid, pid, sid = self.style(role)
        p = E.Element(f"{{{HP}}}p", dict(id=self.next_id(), paraPrIDRef=pid, styleIDRef=sid, pageBreak="0", columnBreak="0", merged="0"))
        flags = emphasis_ranges(text)
        size = 22 if role == "title" else 15 if role in ("square", "circle", "dash") else 12
        limit = limit or (self.available / 100 - 33 if role in ("square", "circle", "dash") else self.available / 100 - 10) / size
        parts = lines(text, limit, title=role == "title")
        offset = 0
        for index, part in enumerate(parts):
            start = 0
            while start < len(part):
                highlighted = bool(flags[offset + start]) or strong
                end = start + 1
                while end < len(part) and (bool(flags[offset + end]) or strong) == highlighted: end += 1
                run = E.SubElement(p, f"{{{HP}}}run", {"charPrIDRef": self.bold(cid) if highlighted else cid})
                t = E.SubElement(run, f"{{{HP}}}t"); t.text = part[start:end]
                start = end
            if index < len(parts) - 1: E.SubElement(t, f"{{{HP}}}lineBreak")
            offset += len(part)
        return p, len(parts) * size * (1.6 if size >= 15 else 1.1)

    def fill(self, header, edge):
        key = (header, edge)
        if key not in self.fills:
            # Source standard prototypes contain grey header and omitted side edges.
            source_id = "32" if edge == "left" else "33" if edge == "right" else "31"
            original = next(e for e in self.reference.iter(f"{{{HH}}}borderFill") if e.get("id") == source_id)
            fill = copy.deepcopy(original)
            for child in list(fill):
                if local(child).endswith("Border"): child.set("width", "0.1 mm")
                if not header and local(child) == "fillBrush": fill.remove(child)
            self.fills[key] = self.merge.append("borderFills", fill)
        return self.fills[key]

    def table(self, texts):
        cid, pid, sid = self.style("host")
        host = E.Element(f"{{{HP}}}p", dict(id=self.next_id(), paraPrIDRef=pid, styleIDRef=sid, pageBreak="0", columnBreak="0", merged="0"))
        run = E.SubElement(host, f"{{{HP}}}run", {"charPrIDRef": cid})
        tbl = E.SubElement(run, f"{{{HP}}}tbl", dict(id=self.next_id(), zOrder="0", numberingType="TABLE", textWrap="TOP_AND_BOTTOM", textFlow="BOTH_SIDES", lock="0", dropcapstyle="None", pageBreak="CELL", repeatHeader="1", rowCnt="3", colCnt="3", cellSpacing="0", borderFillIDRef=self.merge.mapped("borderFills", "2"), noAdjust="0"))
        table_width = min(self.available, 48190)
        widths = [table_width // 3] * 3; widths[-1] += table_width - sum(widths)
        sz = E.SubElement(tbl, f"{{{HP}}}sz", dict(width=str(table_width), height="0", widthRelTo="ABSOLUTE", heightRelTo="ABSOLUTE", protect="0"))
        E.SubElement(tbl, f"{{{HP}}}pos", dict(treatAsChar="1", affectLSpacing="0", flowWithText="1", allowOverlap="0", holdAnchorAndSO="0", vertRelTo="PARA", horzRelTo="PARA", vertAlign="TOP", horzAlign="LEFT", vertOffset="0", horzOffset="0"))
        E.SubElement(tbl, f"{{{HP}}}outMargin", dict(left="0", right="0", top="0", bottom="700"))
        E.SubElement(tbl, f"{{{HP}}}inMargin", dict(left="510", right="510", top="141", bottom="141"))
        total = 0
        for row in range(3):
            tr = E.SubElement(tbl, f"{{{HP}}}tr")
            cells = texts[row * 3:(row + 1) * 3]
            prepared = [self.paragraph(item["text"], "cell", strong=row == 0, limit=(widths[col] - 1020) / 1200) for col, item in enumerate(cells)]
            height = max(1984, math.ceil(max(cost for _, cost in prepared) * 100) + 282)
            total += height
            for col, (p, _) in enumerate(prepared):
                tc = E.SubElement(tr, f"{{{HP}}}tc", dict(name="", header="1" if row == 0 else "0", hasMargin="1", protect="0", editable="0", dirty="0", borderFillIDRef=self.fill(row == 0, "left" if col == 0 else "right" if col == 2 else "middle")))
                sl = E.SubElement(tc, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign="CENTER", linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0")); sl.append(p)
                E.SubElement(tc, f"{{{HP}}}cellAddr", dict(colAddr=str(col), rowAddr=str(row)))
                E.SubElement(tc, f"{{{HP}}}cellSpan", dict(colSpan="1", rowSpan="1"))
                E.SubElement(tc, f"{{{HP}}}cellSz", dict(width=str(widths[col]), height=str(height)))
                E.SubElement(tc, f"{{{HP}}}cellMargin", dict(left="510", right="510", top="141", bottom="141"))
        sz.set("height", str(total))
        if total > 59000: raise ValueError("Block table exceeds one page")
        return host


def compose(data, value, spec):
    if digest(data) != value.source_sha256: raise HTTPException(422, "미저장 문서의 해시가 일치하지 않습니다.")
    try:
        header, section, _, available = parsed(data)
        index = value.target.paragraph
        if index >= len(section): raise HTTPException(409, "삽입 기준 문단이 없습니다.")
        target = section[index]
        forbidden = {"tbl", "pic", "ole", "equation", "chart", "video", "shape", "fieldBegin", "fieldEnd"}
        if any(local(node) in forbidden for node in target.iter()):
            raise HTTPException(422, "표·필드·개체 안에는 블록을 삽입할 수 없습니다.")
        if digest(plain_text(target).encode()) != value.target.expected_text_sha256:
            raise HTTPException(409, "삽입 기준 문단이 변경되었습니다.")
        plan = block_texts(spec, value.variables)
        builder = BlockBuilder(header, section, available)
        nodes = []
        if spec["kind"] == "table":
            if plan[0].get("caption"): nodes.append(builder.paragraph(plan[0]["text"], "caption")[0])
            nodes.append(builder.table([item for item in plan if "cell_row" in item]))
        else:
            for item in plan:
                nodes.append(builder.paragraph(item["text"], item.get("style_kind", item["kind"]))[0])
        for offset, node in enumerate(nodes, 1): section.insert(index + offset, node)
        out = io.BytesIO()
        replacements = {"Contents/header.xml": serialize_block_xml(header),
                        "Contents/section0.xml": serialize_block_xml(section)}
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as dest:
            for entry in source.infolist(): dest.writestr(copy.copy(entry), replacements.get(entry.filename, source.read(entry)))
        result = out.getvalue()
        if len(result) > MAX_SOURCE: raise HTTPException(413, "삽입 결과가 10MB를 초과합니다.")
        validate(result)
        return result, {"texts": plan, "insert_after": index, "top_level_count": len(nodes), "usable_width": available}
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        raise HTTPException(422, "이 HWPX의 서식·본문 구조에 안전하게 블록을 추가할 수 없습니다.") from exc


class Semantic:
    """Resolve references by values, rather than engine-renumbered numeric IDs."""
    def __init__(self, data):
        self.header, self.section, self.roots, _ = parsed(data)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.files = {name: z.read(name) for name in z.namelist()}
        self.collections = {name: {e.get("id"): e for e in self.header.iter(f"{{{HH}}}{tag}")}
                            for name, tag in set(COLLECTIONS.values())}
        self.collections.update({"bullets": {e.get("id"): e for e in self.header.iter(f"{{{HH}}}bullet")},
                                 "numberings": {e.get("id"): e for e in self.header.iter(f"{{{HH}}}numbering")}})
        self.fonts = {(face.get("lang", "").casefold(), font.get("id")): font
                      for face in self.header.iter(f"{{{HH}}}fontface") for font in face}
        manifest = self.roots["Contents/content.hpf"]
        self.binary = {e.get("id"): e.get("href") for e in manifest.iter() if local(e) == "item"}

    def ref(self, name, value, seen):
        key = (name, value)
        if value == "4294967295": return ("unspecified", name)
        if key in seen: return ("cycle", name)
        node = self.collections.get(name, {}).get(value)
        if node is None: raise ValueError("Missing document style reference: " + name)
        return self.node(node, seen | {key})

    def node(self, node, seen=frozenset(), parent_kind=None):
        kind = local(node)
        if kind in ("linesegarray", "lineseg"): return None
        attrs = []
        for key, value in sorted(node.attrib.items()):
            volatile_id = kind in ("p", "tbl", "pic", "rect", "ellipse", "arc", "polygon", "curve", "container", "line", "connectLine", "font", "charPr", "paraPr", "borderFill", "tabPr", "style", "bullet", "numbering")
            if (key == "id" and (volatile_id or (kind == "item" and "opf" in node.tag))) or (kind == "subList" and key in ("textWidth", "textHeight")): continue
            if key in COLLECTIONS:
                value = self.ref(COLLECTIONS[key][0], value, seen)
            elif kind == "fontRef":
                font = self.fonts.get((key.casefold(), value))
                if font is None: raise ValueError("Missing document font reference")
                value = self.node(font, seen)
            elif key == "idRef" and kind == "heading":
                heading = node.get("type")
                # OUTLINE idRef=0 uses the section's outlineShapeIDRef (or the
                # engine's built-in outline), rather than a missing numbering 0.
                if heading == "OUTLINE" and value == "0": value = ("section-outline",)
                elif heading in ("NUMBER", "OUTLINE", "NUMBERING"): value = self.ref("numberings", value, seen)
                elif heading == "BULLET": value = self.ref("bullets", value, seen)
                elif heading == "NONE": value = "0"
            elif key == "idref" and kind == "itemref":
                value = self.binary.get(value)
                if value is None: raise ValueError("Missing document spine reference")
            elif key == "binaryItemIDRef":
                filename = self.binary.get(value)
                if not filename or filename not in self.files: raise ValueError("Missing image resource")
                value = digest(self.files[filename])
            attrs.append((key, value))
        children = []
        for child in node:
            normalized = self.node(child, seen, kind)
            if normalized is None: continue
            # The engine may merge adjacent identically formatted text runs.
            if kind == "p" and local(child) == "run" and children and children[-1][0] == child.tag and children[-1][1] == normalized[1]:
                previous = children.pop()
                children.append((previous[0], previous[1], previous[2], previous[3] + normalized[3], normalized[4]))
            else: children.append(normalized)
        # Merge adjacent text nodes created by run coalescing without changing text.
        if kind == "p":
            merged = []
            for child in children:
                if child[0] == f"{{{HP}}}run":
                    texts = []
                    for part in child[3]:
                        if texts and part[0] == f"{{{HP}}}t" and texts[-1][0] == part[0] and texts[-1][1] == part[1] and not part[3] and not texts[-1][3]:
                            previous = texts.pop(); texts.append((previous[0], previous[1], previous[2] + part[2], (), part[4]))
                        else: texts.append(part)
                    child = (child[0], child[1], child[2], tuple(texts), child[4])
                merged.append(child)
            children = merged
        text = node.text or ""
        if kind != "t" and not text.strip(): text = ""
        return (node.tag, tuple(attrs), text, tuple(children), (node.tail or "") if parent_kind == "t" else "")


def verify_engine_output(source, expected, actual):
    """Fail closed on any changed body/object/format/resource, allowing only IDs/caches."""
    try:
        old, planned, rendered = Semantic(source), Semantic(expected), Semantic(actual)
        if planned.node(planned.section) != rendered.node(rendered.section):
            raise HTTPException(422, {"message": "편집기 출력이 기존 본문·개체·서식 또는 승인 블록을 변경했습니다.", "component": "body"})
        if old.header.attrib != rendered.header.attrib:
            raise HTTPException(422, {"message": "기존 문서의 머리 정보가 변경되었습니다.", "component": "header"})
        non_ref = lambda sem: [sem.node(node) for node in sem.header if local(node) != "refList"]
        if non_ref(old) != non_ref(rendered):
            raise HTTPException(422, {"message": "기존 문서의 시작 번호·문서 설정이 변경되었습니다.", "component": "header-settings"})
        known = {"fontfaces", *old.collections.keys()}
        other_refs = lambda sem: [sem.node(node) for node in sem.header.find(f"{{{HH}}}refList") if local(node) not in known]
        if other_refs(old) != other_refs(rendered):
            raise HTTPException(422, {"message": "기존 문서의 추가 참조 정보가 변경되었습니다.", "component": "other-references"})
        for name, definitions in old.collections.items():
            expected_definitions = {repr(old.ref(name, key, frozenset())) for key in definitions}
            actual_definitions = {repr(rendered.ref(name, key, frozenset())) for key in rendered.collections.get(name, {})}
            if not expected_definitions.issubset(actual_definitions):
                raise HTTPException(422, {"message": "편집기 출력에서 기존 문서 서식이 누락되었습니다.", "component": name})
        original_fonts = {repr(old.node(font)) for font in old.fonts.values()}
        if not original_fonts.issubset({repr(rendered.node(font)) for font in rendered.fonts.values()}):
            raise HTTPException(422, {"message": "편집기 출력에서 기존 글꼴 정보가 누락되었습니다.", "component": "fonts"})
        for name, raw in old.files.items():
            if name in ("Contents/header.xml", "Contents/section0.xml") or name.startswith("Preview/"):
                continue
            if name not in rendered.files:
                raise HTTPException(422, {"message": "편집기 출력에서 기존 문서 자원이 누락되었습니다.", "component": "resources"})
            if name == "Contents/content.hpf":
                if old.node(old.roots[name]) != rendered.node(rendered.roots[name]):
                    raise HTTPException(422, {"message": "기존 HWPX 작성 정보·자원 연결·구역 순서가 변경되었습니다.", "component": "package"})
            elif name.endswith((".xml", ".hpf")):
                if old.node(old.roots[name]) != rendered.node(rendered.roots[name]):
                    raise HTTPException(422, "기존 HWPX 문서 설정이 변경되었습니다.")
            elif rendered.files[name] != raw:
                raise HTTPException(422, "기존 HWPX 자원 바이트가 변경되었습니다.")
        return True
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "편집기 출력의 본문·서식·자원 보존을 확인할 수 없습니다.") from exc
