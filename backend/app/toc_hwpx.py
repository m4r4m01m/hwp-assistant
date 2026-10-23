"""Pure, bounded native TOC transforms with in-document identities and safety proofs."""
import copy
import io
import json
import math
import re
import uuid
import zipfile
from xml.etree import ElementTree as E
from fastapi import HTTPException
from .block_contract import MAX_SOURCE, canonical
from .block_hwpx import HP, HH, HC, BlockBuilder, Semantic, local, parsed, plain_text, serialize_block_xml, verify_engine_output
from .document_standard import set_margins, report_styles
from .hwpx import digest, validate
from .toc_contract import POLICY

PREFIX = "HWP Assistant TOC "
HEADING = re.compile(r"^HWP Assistant TOC heading:([a-f0-9-]{36}):([12])$")
BOX = re.compile(r"^HWP Assistant TOC box:([a-f0-9-]{36})$")
ROOT = re.compile(r"^HWP Assistant TOC root:([a-f0-9-]{36})$")
ENTRY = re.compile(r"^HWP Assistant TOC entry:([a-f0-9-]{36}):([a-f0-9-]{36})$")
FRAME_SLOTS = ((0, 0, "top-left"), (0, 2, "top-right"), (1, 0, "bottom-left"), (1, 2, "bottom-right"))


def frame_paragraphs(table, nested, root, stylemap, ident, heading_count):
    """A fixed native title frame, never a general allowance for extra text."""
    rows = table.findall(f"{{{HP}}}tr")
    if table.get("colCnt") != "3" or table.get("rowCnt") != str(heading_count + 2) or len(rows) != heading_count + 2:
        raise ValueError("Wrong reference TOC grid")
    cells = [row.findall(f"{{{HP}}}tc") for row in rows]
    if [len(row) for row in cells] != [3, 2] + [1] * heading_count or len(nested) != heading_count + 5:
        raise ValueError("Wrong reference TOC cells")
    entries, blanks = [], []
    for y, row in enumerate(cells):
        for offset, cell in enumerate(row):
            x = offset if y == 0 else offset * 2 if y == 1 else 0
            if cell.find(f"{{{HP}}}cellAddr").attrib != dict(colAddr=str(x), rowAddr=str(y)):
                raise ValueError("Reference TOC cell position changed")
            span = dict(colSpan="3" if y >= 2 else "1", rowSpan="2" if (y, x) == (0, 1) else "1")
            if cell.find(f"{{{HP}}}cellSpan").attrib != span:
                raise ValueError("Reference TOC span changed")
            paragraphs = cell.findall(f"{{{HP}}}subList/{{{HP}}}p")
            if len(paragraphs) != 1:
                raise ValueError("Reference TOC cell paragraph count changed")
            paragraph = paragraphs[0]
            if (y, x) == (0, 1):
                if paragraph is not root or not native_text(paragraph).strip():
                    raise ValueError("Reference TOC title moved")
            elif y >= 2:
                entries.append(paragraph)
            else:
                slot = next(slot for row, column, slot in FRAME_SLOTS if (row, column) == (y, x))
                style = stylemap.get(paragraph.get("styleIDRef"))
                if style is None or style.get("name") != PREFIX + "frame:" + ident + ":" + slot or native_text(paragraph) != "":
                    raise ValueError("Reference TOC spacer changed")
                blanks.append(paragraph)
    if len(blanks) != 4 or set(nested) != {root, *entries, *blanks}:
        raise ValueError("Unexpected reference TOC paragraph")
    return entries


def native_text(p, anchor=False):
    """Exact native scalar text; explicit soft breaks are newlines, not layout breaks."""
    result = []
    for child in p:
        if local(child) == "linesegarray": continue
        if local(child) != "run": raise ValueError("Unsupported paragraph child")
        for part in child:
            if local(part) != "t":
                if anchor and local(part) in ("secPr", "ctrl") and all(local(x) in ("ctrl", "colPr") for x in part.iter()): continue
                if anchor and local(part) == "secPr": continue
                raise ValueError("Heading contains a control or object")
            result.append(part.text or "")
            for inline in part:
                if local(inline) != "lineBreak" or len(inline) or inline.attrib: raise ValueError("Heading contains unsupported inline data")
                result.extend(("\n", inline.tail or ""))
    text = "".join(result)
    if any(ord(ch) < 32 and ch != "\n" for ch in text): raise ValueError("Unsupported heading text")
    return text


def styles(header):
    entries = list(header.iter(f"{{{HH}}}style"))
    ids = [x.get("id") for x in entries]
    if len(ids) != len(set(ids)) or any(not (x or "").isdigit() or int(x) > 255 for x in ids):
        raise HTTPException(422, "문단 스타일 ID는 중복 없는 0~255 범위여야 합니다.")
    return {x.get("id"): x for x in entries}


def descriptor(p, index, identifier=None, level=None):
    text = native_text(p)
    if not text.strip() or not 1 <= len(text) <= 300: raise ValueError("Heading scalar length exceeds bounds")
    label = text.replace("\n", "")
    result = dict(section=0, paragraph=index, text=label, native_text=text, text_sha256=digest(text.encode()), length=len(text), cp_length=len(text))
    if identifier is not None: result.update(id=identifier, heading_id=identifier, level=level)
    return result


class TocSemantic(Semantic):
    def node(self, node, seen=frozenset(), parent_kind=None):
        if local(node) == "style" and ROOT.fullmatch(node.get("name", "")):
            node = copy.deepcopy(node); node.set("engName", "managed-toc-metadata")
        return super().node(node, seen, parent_kind)


def body_hash(sem, host_index=None):
    payload = (sem.section.tag, tuple(sorted(sem.section.attrib.items())),
               [sem.node(p) for i, p in enumerate(sem.section) if i != host_index],
               sorted(sem.header.attrib.items()), [sem.node(x) for x in sem.header if local(x) != "refList"],
               [(name, sem.node(sem.roots[name]) if name.endswith((".xml", ".hpf")) else digest(raw))
                for name, raw in sorted(sem.files.items()) if name not in ("Contents/header.xml", "Contents/section0.xml") and not name.startswith("Preview/")])
    return digest(canonical(payload).encode())


def suggested_levels(sem):
    # Request-local pinned typography; no lexical or model-based heading inference.
    _, reference, _, roles = report_styles()
    font_maps = [{(f.get("lang", "").casefold(), x.get("id")):x.get("face") for f in h.iter(f"{{{HH}}}fontface") for x in f} for h in (sem.header, reference)]
    def char_sig(fonts, c):
        ref = c.find(f"{{{HH}}}fontRef")
        return (c.get("height"), c.find(f"{{{HH}}}bold") is not None, c.get("textColor"),
                tuple((lang, fonts.get((lang.casefold(), ident))) for lang, ident in sorted(ref.attrib.items())))
    def para_sig(p):
        case = p.find(f"{{{HP}}}switch/{{{HP}}}case")
        branch = case if case is not None else p
        margin = branch.find(f"{{{HH}}}margin")
        return (p.find(f"{{{HH}}}align").get("horizontal"),
                tuple((local(x), x.get("value")) for x in margin if local(x) in ("left", "right", "intent")),
                tuple(sorted(branch.find(f"{{{HH}}}lineSpacing").attrib.items())))
    chars = {x.get("id"):char_sig(font_maps[0], x) for x in sem.header.iter(f"{{{HH}}}charPr")}
    paras = {x.get("id"):para_sig(x) for x in sem.header.iter(f"{{{HH}}}paraPr")}
    standard = []
    for role, level in (("heading", 1), ("subheading", 2)):
        cid, pid = roles[role]
        rc = next(x for x in reference.iter(f"{{{HH}}}charPr") if x.get("id") == cid)
        rp = next(x for x in reference.iter(f"{{{HH}}}paraPr") if x.get("id") == pid)
        standard.append((char_sig(font_maps[1], rc), para_sig(rp), level))
    def classify(p):
        run_chars = [chars.get(r.get("charPrIDRef")) for r in p.findall(f"{{{HP}}}run")]
        para = paras.get(p.get("paraPrIDRef"))
        for char, expected_para, level in standard:
            if run_chars and all(c == char for c in run_chars) and para == expected_para: return level
        return None
    return classify


def inspected(data):
    sem = TocSemantic(data); stylemap = styles(sem.header); suggest = suggested_levels(sem)
    active, hosts, candidates = {}, [], []
    top = set(sem.section)
    if any(HEADING.fullmatch(stylemap.get(p.get("styleIDRef"), E.Element("style")).get("name", "")) for p in sem.section.iter(f"{{{HP}}}p") if p not in top):
        raise HTTPException(422, "표나 개체 안으로 복사한 제목 식별자는 아직 지원하지 않습니다.")
    for index, p in enumerate(sem.section):
        style = stylemap.get(p.get("styleIDRef"))
        if style is None: raise HTTPException(422, "본문의 문단 스타일 연결을 확인할 수 없습니다.")
        name = style.get("name", "")
        marked = HEADING.fullmatch(name)
        if marked:
            ident, level = marked.groups(); uuid.UUID(ident)
            if ident in active: raise HTTPException(422, "같은 제목 ID가 두 문단에 있습니다. 복사한 제목을 먼저 정리하세요.")
            try: active[ident] = descriptor(p, index, ident, int(level))
            except ValueError: active[ident] = dict(id=ident, heading_id=ident, section=0, paragraph=index, level=int(level), unsupported=True)
        elif name.startswith(PREFIX + "heading:"):
            raise HTTPException(422, "제목 식별자가 손상되었습니다.")
        box = BOX.fullmatch(name)
        if box: hosts.append((index, p, box.group(1)))
        elif name.startswith(PREFIX + "box:"): raise HTTPException(422, "목차 상자 식별자가 손상되었습니다.")
        try:
            candidate = descriptor(p, index)
            if marked: candidate.update(heading_id=ident, id=ident, level=int(level))
            candidate["suggested_level"] = int(level) if marked else suggest(p)
            candidates.append(candidate)
        except ValueError: pass
    managed_nested = []
    for p in sem.section.iter(f"{{{HP}}}p"):
        name = stylemap.get(p.get("styleIDRef"), E.Element("style")).get("name", "")
        if name.startswith(PREFIX + "root:") or name.startswith(PREFIX + "entry:") or name.startswith(PREFIX + "frame:"):
            managed_nested.append(p)
        if p not in top and name.startswith(PREFIX + "box:"):
            raise HTTPException(422, "목차 상자가 표나 개체 안으로 복사되었습니다.")
    allowed = set(hosts[0][1].iter(f"{{{HP}}}p")) if len(hosts) == 1 else set()
    if any(p not in allowed for p in managed_nested):
        raise HTTPException(422, "관리 목차의 상자·제목·항목 연결이 손상되었습니다. 새 목차로 대체하지 않았습니다.")
    if len(hosts) > 1: raise HTTPException(422, "관리 목차 상자가 중복되었습니다.")
    toc = None
    if hosts:
        index, host, ident = hosts[0]
        toc = dict(id=ident, status="damaged", reason="관리 목차의 구조 또는 내용을 수동으로 변경했습니다.", headings=[])
        try:
            uuid.UUID(ident)
            if index < 1: raise ValueError("Missing anchor")
            toc["anchor"] = dict(section=0, paragraph=index - 1, expected_text_sha256=digest(native_text(sem.section[index - 1], anchor=True).encode()))
            nested = list(host.iter(f"{{{HP}}}p"))[1:]
            table = host.find(f"{{{HP}}}run/{{{HP}}}tbl")
            if table is None or len(host.findall(f".//{{{HP}}}tbl")) != 1 or not nested: raise ValueError("Malformed managed table")
            roots = [p for p in nested if stylemap.get(p.get("styleIDRef"), E.Element("style")).get("name") == PREFIX + "root:" + ident]
            if len(roots) != 1: raise ValueError("Missing or duplicate root marker")
            rootstyle = stylemap[roots[0].get("styleIDRef")]
            raw_meta = rootstyle.get("engName", "")
            if len(raw_meta) > 8192: raise ValueError("Metadata too large")
            meta = json.loads(raw_meta)
            if meta.get("policy") != POLICY or meta.get("toc_id") != ident: raise ValueError("Wrong metadata identity")
            ids = meta["heading_ids"]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or len(ids) != len(set(ids)): raise ValueError("Wrong heading count")
            from .reference_report import TOC_LAYOUT_POLICIES, TOC_LAYOUT_POLICY
            layout = meta.get("layout")
            if layout in TOC_LAYOUT_POLICIES:
                entries = frame_paragraphs(table, nested, roots[0], stylemap, ident, len(ids))
                if layout == TOC_LAYOUT_POLICY and (table.get("pageBreak") != "CELL" or table.get("textWrap") != "TOP_AND_BOTTOM"
                    or table.find(f"{{{HP}}}pos").get("treatAsChar") != "0" or table.find(f"{{{HP}}}pos").get("flowWithText") != "1"):
                    raise ValueError("Reference TOC row flow changed")
            elif layout is None:
                if roots[0] is not nested[0] or len(nested) != len(ids) + 1: raise ValueError("Wrong heading count")
                entries = nested[1:]
            else:
                raise ValueError("Unsupported TOC layout")
            for p, heading_id in zip(entries, ids):
                uuid.UUID(heading_id)
                entry = stylemap.get(p.get("styleIDRef"))
                if entry is None or entry.get("name") != PREFIX + "entry:" + ident + ":" + heading_id: raise ValueError("Entry linkage changed")
            if digest(canonical(sem.node(host)).encode()) != meta["managed_sha256"]: raise ValueError("Managed contents changed")
            toc.update(headings=[active[x] for x in ids if x in active and not active[x].get("unsupported")], heading_ids=ids,
                       missing_ids=[x for x in ids if x not in active], unsupported_ids=[x for x in ids if x in active and active[x].get("unsupported")],
                       pages=meta["pages"], body_sha256=meta["body_sha256"], paragraph=index)
            if layout is not None: toc["layout"] = layout
            current_hash = body_hash(sem, index)
            stale = current_hash != meta["body_sha256"] or any(x not in active or active[x].get("unsupported") for x in ids)
            toc.update(status="stale" if stale else "current", reason=("기존 제목이 삭제되었거나 지원하지 않는 구조입니다. 목차 갱신을 중단합니다." if toc["missing_ids"] or toc["unsupported_ids"] else "본문이나 제목이 바뀌었습니다. 명시적으로 쪽수를 갱신하세요.") if stale else None)
        except (ValueError, KeyError, TypeError, AttributeError): pass
    return sem, active, candidates, toc


def analyze(data, source_sha256):
    if digest(data) != source_sha256: raise HTTPException(422, "미저장 문서의 해시가 일치하지 않습니다.")
    try:
        sem, _, candidates, toc = inspected(data)
        return {"candidates": candidates, "toc": toc, "body_sha256": body_hash(sem, toc.get("paragraph") if toc else None)}
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "이 문서의 제목·목차 구조를 안전하게 확인할 수 없습니다.") from exc


def packed(data, header, section):
    out = io.BytesIO()
    replacements = {"Contents/header.xml": serialize_block_xml(header), "Contents/section0.xml": serialize_block_xml(section)}
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as dest:
        for entry in source.infolist(): dest.writestr(copy.copy(entry), replacements.get(entry.filename, source.read(entry)))
    result = out.getvalue()
    if len(result) > MAX_SOURCE: raise HTTPException(413, "목차 결과가 10MB를 초과합니다.")
    validate(result)
    return result


class TocBuilder(BlockBuilder):
    def __init__(self, header, section, available):
        super().__init__(header, section, available)
        from .reference_report import toc_settings, BODY_SPACING_POLICY, TOC_LAYOUT_POLICIES
        self.reference_settings = toc_settings(header)
        self.reference_spacing = None
        self.layout = self.reference_settings.get("toc_layout") if self.reference_settings else None
        self.frame_source = None
        if self.reference_settings:
            from .block_hwpx import MergeStyles
            _, self.reference, _, self.formats = report_styles(visual_settings=self.reference_settings)
            self.merge = MergeStyles(header, self.reference)
            if self.reference_settings.get("body_spacing") == BODY_SPACING_POLICY or self.layout in TOC_LAYOUT_POLICIES:
                # The pinned example's title spans two rows with 10pt empty
                # side cells; its entries use 14pt / 160% blank paragraphs.
                # Keep the managed root+entry paragraph identity contract and
                # express those spaces as margins instead of anonymous text.
                from .document_standard import source_parts
                _, source_header, source_section = source_parts()
                source_table = source_section[129].find(f".//{{{HP}}}tbl")
                self.frame_source = source_table
                title_height = sum(max(int(tc.find(f"{{{HP}}}cellSz").get("height"))
                    for tc in tr.findall(f"{{{HP}}}tc") if tc.find(f"{{{HP}}}cellSpan").get("rowSpan") == "1")
                    for tr in source_table.findall(f"{{{HP}}}tr")[:2])
                blank = source_table.findall(f"{{{HP}}}tr")[-1].find(f"{{{HP}}}tc/{{{HP}}}subList/{{{HP}}}p")
                char = next(c for c in source_header.iter(f"{{{HH}}}charPr")
                            if c.get("id") == blank.find(f"{{{HP}}}run").get("charPrIDRef"))
                para = next(p for p in source_header.iter(f"{{{HH}}}paraPr") if p.get("id") == blank.get("paraPrIDRef"))
                line = next(para.iter(f"{{{HH}}}lineSpacing"))
                if self.reference_settings.get("body_spacing") == BODY_SPACING_POLICY:
                    self.reference_spacing = (title_height, int(char.get("height")) * int(line.get("value")) / 100)

    def paragraph_spacing(self, role, previous=None):
        if not self.reference_settings:
            return None
        prefix = {"root": "toc_title", "major": "toc_major", "minor": "toc_minor"}[role]
        before, after = (self.reference_settings[prefix + suffix] for suffix in ("_before_pt", "_after_pt"))
        if role == "root" and self.layout:
            # Native merged title cells now supply the title band's geometry.
            return before, after
        if self.reference_spacing is None or before or after:
            return before, after
        title_height, entry_gap = self.reference_spacing
        if role == "root":
            padding = sum(round(self.reference_settings[f"toc_padding_{side}_mm"] * 7200 / 25.4) for side in ("top", "bottom"))
            title_line = self.reference_settings["toc_title_size_pt"] * self.reference_settings["toc_title_line_percent"]
            gap = max(0, title_height - title_line - padding)
            return round(gap / 2) / 100, (gap - round(gap / 2)) / 100
        previous_prefix = {"root": "toc_title", "major": "toc_major", "minor": "toc_minor"}.get(previous)
        if previous_prefix and self.reference_settings[previous_prefix + "_after_pt"]:
            return before, after
        if role == "major" or previous in ("root", "major"):
            before = entry_gap / 100
        return before, after

    def own_style(self, cid, pid, name, metadata=None):
        sid = self.merge.own_style(cid, pid, name)
        collection = self.merge.collection(self.merge.dest, "styles")
        # Host and entry styles carry no changing metadata. Reuse semantic equals,
        # retaining every original definition; each refresh needs only a new root.
        if not ROOT.fullmatch(name):
            sem = Semantic.__new__(Semantic)
            sem.collections = {kind: {e.get("id"):e for e in self.header.iter(f"{{{HH}}}{tag}")} for kind, tag in
                (("styles","style"),("charProperties","charPr"),("paraProperties","paraPr"),("borderFills","borderFill"),("tabProperties","tabPr"),("numberings","numbering"),("bullets","bullet"))}
            sem.fonts = {(face.get("lang", "").casefold(), font.get("id")):font for face in self.header.iter(f"{{{HH}}}fontface") for font in face}
            sem.binary = {}; sem.files = {}
            key = sem.ref("styles", sid, frozenset())
            match = next((x for x in collection if x.get("id") != sid and x.get("name") == name and sem.ref("styles", x.get("id"), frozenset()) == key), None)
            if match is not None:
                collection.remove(next(x for x in collection if x.get("id") == sid)); collection.set("itemCnt", str(len(collection)))
                return match.get("id")
        if int(sid) > 255: raise HTTPException(422, "문단 스타일 슬롯이 부족합니다. 기존 스타일은 삭제하지 않았습니다.")
        if metadata is not None: next(x for x in self.header.iter(f"{{{HH}}}style") if x.get("id") == sid).set("engName", metadata)
        return sid

    def toc_style(self, role, name, spacing=None):
        source_role = "title" if role == "root" else "heading" if role == "major" else "minor_heading" if role == "minor" else "empty"
        cid, pid = self.formats[source_role]
        chars = self.reference.find(f".//{{{HH}}}charProperties"); paras = self.reference.find(f".//{{{HH}}}paraProperties")
        char = copy.deepcopy(next(c for c in chars if c.get("id") == cid)); para = copy.deepcopy(next(p for p in paras if p.get("id") == pid))
        cid = str(max(int(c.get("id")) for c in chars) + 1); pid = str(max(int(p.get("id")) for p in paras) + 1)
        char.set("id", cid); char.set("height", "2000" if role == "root" else "1600" if role == "major" else "1400" if role == "minor" else "1000" if role == "frame" else "100")
        para.set("id", pid); para.find(f"{{{HH}}}align").set("horizontal", "CENTER" if role == "root" else "LEFT")
        para.find(f"{{{HH}}}border").attrib.update(borderFillIDRef="1", offsetTop="0", offsetBottom="0", connect="0", ignoreMargin="0")
        left = 3500 if role == "minor" else 2000 if role == "major" else 0
        set_margins(para, left=left, right=2000 if role in ("major", "minor") else 0)
        right = 2000
        if self.reference_settings and role in ("root", "major", "minor"):
            from .reference_source import apply_character, apply_paragraph
            reference_role = {"root": "toc_title", "major": "toc_major", "minor": "toc_minor"}[role]
            apply_character(self.reference, char, reference_role, self.reference_settings)
            from .reference_report import native_character
            native_character(char)
            apply_paragraph(para, reference_role, self.reference_settings)
            if spacing is not None:
                apply_paragraph(para, reference_role, {
                    reference_role + "_before_pt": spacing[0], reference_role + "_after_pt": spacing[1]})
            left = round(self.reference_settings[reference_role + "_left_indent_pt"] * 100)
            right = round(self.reference_settings[reference_role + "_right_indent_pt"] * 100)
        if role in ("major", "minor"):
            tab = E.Element(f"{{{HH}}}tabPr", dict(id=str(max(int(t.get("id")) for t in self.reference.iter(f"{{{HH}}}tabPr")) + 1), autoTabLeft="0", autoTabRight="0"))
            switch = E.SubElement(tab, f"{{{HP}}}switch")
            padding = round((self.reference_settings["toc_padding_left_mm"] + self.reference_settings["toc_padding_right_mm"]) * 7200 / 25.4) if self.reference_settings else 1020
            position = self.available - padding - left - right
            case = E.SubElement(switch, f"{{{HP}}}case", {f"{{{HP}}}required-namespace":"http://www.hancom.co.kr/hwpml/2016/HwpUnitChar"})
            E.SubElement(case, f"{{{HH}}}tabItem", dict(pos=str(position), type="RIGHT", leader="DASH", unit="HWPUNIT"))
            fallback = E.SubElement(switch, f"{{{HP}}}default")
            E.SubElement(fallback, f"{{{HH}}}tabItem", dict(pos=str(position * 2), type="RIGHT", leader="DASH"))
            self.reference.find(f".//{{{HH}}}tabProperties").append(tab); para.set("tabPrIDRef", tab.get("id"))
        if not self.reference_settings or role in ("host", "frame"):
            for spacing in para.iter(f"{{{HH}}}lineSpacing"): spacing.set("value", "100" if role == "host" else "160")
        if role == "frame":
            from .reference_report import native_character
            native_character(char)
        chars.append(char); paras.append(para)
        mapped_cid = self.merge.mapped("charProperties", cid); mapped_pid = self.merge.mapped("paraProperties", pid)
        return mapped_cid, mapped_pid, self.own_style(mapped_cid, mapped_pid, name)

    def text_p(self, text, role, name, page=None, spacing=None):
        cid, pid, sid = self.toc_style(role, name, spacing)
        p = E.Element(f"{{{HP}}}p", dict(id=self.next_id(), paraPrIDRef=pid, styleIDRef=sid, pageBreak="0", columnBreak="0", merged="0"))
        run = E.SubElement(p, f"{{{HP}}}run", {"charPrIDRef":cid}); t = E.SubElement(run, f"{{{HP}}}t"); t.text = text
        if page is not None:
            E.SubElement(t, f"{{{HP}}}tab", dict(width="0", leader="0", type="1" )).tail = str(page)
        return p, sid

    def border(self, first=False, last=False):
        source = next(e for e in self.reference.iter(f"{{{HH}}}borderFill") if e.get("id") == "2")
        fill = copy.deepcopy(source)
        for child in list(fill):
            kind = local(child)
            if kind == "fillBrush": fill.remove(child)
            elif kind.endswith("Border"):
                child.attrib.update(type="SOLID" if kind in ("leftBorder", "rightBorder") or kind == "topBorder" and first or kind == "bottomBorder" and last else "NONE", width="0.1 mm", color="#A0A0A0")
                if self.reference_settings:
                    if child.get("type") != "NONE":
                        child.set("type", self.reference_settings.get("toc_border_style", "SOLID"))
                    child.set("width", f"{self.reference_settings.get('toc_border_width_mm', .1):g} mm")
                    child.set("color", self.reference_settings.get("toc_border_color", "#A0A0A0"))
            elif kind == "diagonal":
                # Native writer header.rs write_diagonal omits type NONE.
                # Match that representation only for newly created TOC borders.
                fill.remove(child)
        return self.merge.append("borderFills", fill)

    def frame_border(self, source_id):
        fill = copy.deepcopy(next(e for e in self.reference.iter(f"{{{HH}}}borderFill") if e.get("id") == source_id))
        for child in list(fill):
            kind = local(child)
            if kind in ("fillBrush", "diagonal"):
                fill.remove(child)
            elif kind.endswith("Border"):
                child.attrib.update(type=self.reference_settings["toc_border_style"] if child.get("type") != "NONE" else "NONE",
                    width=f"{self.reference_settings['toc_border_width_mm']:g} mm", color=self.reference_settings["toc_border_color"])
        return self.merge.append("borderFills", fill)

    def add_reference_frame(self, table, ident):
        """Copy the pinned frame's five cells; keep entries individually pageable."""
        if self.frame_source is None:
            raise ValueError("Missing frozen reference TOC frame")
        old_rows = table.findall(f"{{{HP}}}tr")
        title_row = old_rows[0]
        title_cell = title_row.find(f"{{{HP}}}tc")
        source_rows = self.frame_source.findall(f"{{{HP}}}tr")[:2]
        source_cells = [row.findall(f"{{{HP}}}tc") for row in source_rows]
        source_width = int(self.frame_source.find(f"{{{HP}}}sz").get("width"))
        original_center = int(source_cells[0][1].find(f"{{{HP}}}cellSz").get("width"))
        source_heights = [max(int(cell.find(f"{{{HP}}}cellSz").get("height")) for cell in cells
                             if cell.find(f"{{{HP}}}cellSpan").get("rowSpan") == "1") for cells in source_cells]
        # Preserve the original proportions by default; a larger selected font
        # may widen the central opening instead of forcing an accidental wrap.
        text = plain_text(title_cell.find(f"{{{HP}}}subList/{{{HP}}}p"))
        width = math.ceil(sum(1 if ord(ch) > 255 else .5 for ch in text) * self.reference_settings["toc_title_size_pt"] * 100)
        padding = title_cell.find(f"{{{HP}}}cellMargin")
        width += int(padding.get("left")) + int(padding.get("right"))
        width += round(100 * (self.reference_settings["toc_title_left_indent_pt"] + self.reference_settings["toc_title_right_indent_pt"]))
        center = min(self.available - 2, max(round(self.available * original_center / source_width), width))
        left = (self.available - center) // 2
        widths = (left, center, self.available - left - center)
        old_title_height = int(title_cell.find(f"{{{HP}}}cellSz").get("height"))
        title_height = max(sum(source_heights), old_title_height)
        upper = round(title_height * source_heights[0] / sum(source_heights))
        heights = (upper, title_height - upper)
        frames = [E.Element(f"{{{HP}}}tr"), E.Element(f"{{{HP}}}tr")]
        for y, cells in enumerate(source_cells):
            for source_cell in cells:
                x = int(source_cell.find(f"{{{HP}}}cellAddr").get("colAddr"))
                if (y, x) == (0, 1):
                    cell = title_cell
                    cell.find(f"{{{HP}}}cellAddr").attrib.update(colAddr="1", rowAddr="0")
                    cell.find(f"{{{HP}}}cellSpan").set("rowSpan", "2")
                    cell.find(f"{{{HP}}}cellSz").attrib.update(width=str(center), height=str(title_height))
                else:
                    cell = copy.deepcopy(source_cell)
                    sublist = cell.find(f"{{{HP}}}subList")
                    for child in list(sublist): sublist.remove(child)
                    slot = next(slot for row, column, slot in FRAME_SLOTS if (row, column) == (y, x))
                    paragraph, _ = self.text_p("", "frame", PREFIX + "frame:" + ident + ":" + slot)
                    sublist.append(paragraph)
                    cell.find(f"{{{HP}}}cellSz").attrib.update(width=str(widths[x]), height=str(heights[y]))
                cell.set("borderFillIDRef", self.frame_border(source_cell.get("borderFillIDRef")))
                frames[y].append(cell)
        table.remove(title_row)
        first_row = list(table).index(old_rows[1])
        for offset, row in enumerate(frames): table.insert(first_row + offset, row)
        for y, row in enumerate(old_rows[1:], 2):
            cell = row.find(f"{{{HP}}}tc")
            cell.find(f"{{{HP}}}cellAddr").set("rowAddr", str(y))
            cell.find(f"{{{HP}}}cellSpan").set("colSpan", "3")
        table.attrib.update(rowCnt=str(len(old_rows) + 1), colCnt="3")
        size = table.find(f"{{{HP}}}sz")
        size.set("height", str(int(size.get("height")) + title_height - old_title_height))

    def toc_table(self, ident, headings, pages):
        from .reference_report import TOC_LAYOUT_POLICY
        cid, pid, sid = self.toc_style("host", PREFIX + "box:" + ident)
        host = E.Element(f"{{{HP}}}p", dict(id=self.next_id(), paraPrIDRef=pid, styleIDRef=sid, pageBreak="0", columnBreak="0", merged="0"))
        run = E.SubElement(host, f"{{{HP}}}run", {"charPrIDRef":cid})
        table = E.SubElement(run, f"{{{HP}}}tbl", dict(id=self.next_id(), zOrder="0", numberingType="TABLE", textWrap="TOP_AND_BOTTOM", textFlow="BOTH_SIDES", lock="0", dropcapstyle="None", pageBreak="CELL", repeatHeader="0", rowCnt=str(len(headings)+1), colCnt="1", cellSpacing="0", borderFillIDRef=self.border(True, True), noAdjust="0"))
        size = E.SubElement(table, f"{{{HP}}}sz", dict(width=str(self.available), height="0", widthRelTo="ABSOLUTE", heightRelTo="ABSOLUTE", protect="0"))
        # Native inline tables defer an oversized merged frame as one object,
        # leaving a blank page. Flow the new frame by cells without reducing
        # its font, row heights or spacing; old frozen layouts stay inline.
        E.SubElement(table, f"{{{HP}}}pos", dict(treatAsChar="0" if self.layout == TOC_LAYOUT_POLICY else "1", affectLSpacing="0", flowWithText="1", allowOverlap="0", holdAnchorAndSO="0", vertRelTo="PARA", horzRelTo="PARA", vertAlign="TOP", horzAlign="LEFT", vertOffset="0", horzOffset="0"))
        E.SubElement(table, f"{{{HP}}}outMargin", dict(left="0", right="0", top="0", bottom="700"))
        cell_padding = {side: str(round(self.reference_settings[f"toc_padding_{side}_mm"] * 7200 / 25.4)) for side in ("left", "right", "top", "bottom")} if self.reference_settings else dict(left="510", right="510", top="141", bottom="141")
        E.SubElement(table, f"{{{HP}}}inMargin", cell_padding)
        roles = ["root"] + ["major" if h["level"] == 1 else "minor" for h in headings]
        spacing = [self.paragraph_spacing(role, roles[i-1] if i else None) for i, role in enumerate(roles)]
        title = plain_text(self.frame_source.findall(f"{{{HP}}}tr")[0].findall(f"{{{HP}}}tc")[1].find(f"{{{HP}}}subList/{{{HP}}}p")) if self.layout else "< 목 차 >"
        rootp, root_sid = self.text_p(title, "root", PREFIX + "root:" + ident, spacing=spacing[0])
        paragraphs = [rootp] + [self.text_p(h["text"], roles[i+1], PREFIX + "entry:" + ident + ":" + h["id"], pages[h["id"]], spacing[i+1])[0] for i, h in enumerate(headings)]
        total = 0
        for row, p in enumerate(paragraphs):
            tr = E.SubElement(table, f"{{{HP}}}tr")
            tc = E.SubElement(tr, f"{{{HP}}}tc", dict(name="", header="0", hasMargin="1", protect="0", editable="0", dirty="0", borderFillIDRef=self.border(row==0, row==len(paragraphs)-1)))
            sl = E.SubElement(tc, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign="CENTER", linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0")); sl.append(p)
            text_length = len(plain_text(p)); point = 20 if row == 0 else 16 if headings[row-1]["level"] == 1 else 14
            estimated_lines = max(1, math.ceil(text_length * point * 80 / max(10000, self.available - 7520)))
            if self.reference_settings:
                reference_role = "toc_title" if row == 0 else "toc_major" if headings[row-1]["level"] == 1 else "toc_minor"
                point = self.reference_settings[reference_role + "_size_pt"]
                room = self.available - int(cell_padding["left"]) - int(cell_padding["right"]) - 100 * (self.reference_settings[reference_role + "_left_indent_pt"] + self.reference_settings[reference_role + "_right_indent_pt"])
                estimated_lines = max(1, math.ceil(text_length * point * 80 / max(10000, room)))
                height = math.ceil(estimated_lines * point * self.reference_settings[reference_role + "_line_percent"] + 100 * sum(spacing[row])) + int(cell_padding["top"]) + int(cell_padding["bottom"])
            else:
                height = math.ceil(estimated_lines * point * 160 + 282)
            total += height
            E.SubElement(tc, f"{{{HP}}}cellAddr", dict(colAddr="0", rowAddr=str(row)))
            E.SubElement(tc, f"{{{HP}}}cellSpan", dict(colSpan="1", rowSpan="1"))
            E.SubElement(tc, f"{{{HP}}}cellSz", dict(width=str(self.available), height=str(height)))
            E.SubElement(tc, f"{{{HP}}}cellMargin", cell_padding)
        size.set("height", str(total))
        if self.layout:
            self.add_reference_frame(table, ident)
        return host, root_sid


def compose_toc(data, value):
    if digest(data) != value.source_sha256: raise HTTPException(422, "미저장 문서의 해시가 일치하지 않습니다.")
    try:
        sem, active, _, toc = inspected(data)
        header, section, available = sem.header, sem.section, parsed(data)[3]
        ident = str(value.toc_id); target = value.target.paragraph
        if target >= len(section): raise HTTPException(409, "목차 삽입 기준 문단이 없습니다.")
        if digest(native_text(section[target], anchor=True).encode()) != value.target.expected_text_sha256: raise HTTPException(409, "목차 삽입 기준 문단이 변경되었습니다.")
        if toc:
            if toc["id"] != ident or toc["status"] == "damaged": raise HTTPException(409, "기존 관리 목차가 다르거나 수동으로 변경되었습니다. 덮어쓰지 않았습니다.")
            if toc["missing_ids"] or toc["unsupported_ids"] or [str(h.heading_id) for h in value.headings] != toc["heading_ids"]:
                raise HTTPException(409, "기존 목차의 제목이 삭제되었거나 지원하지 않는 구조로 변경되었습니다. 선택 목록을 자동으로 줄이지 않았습니다.")
            if value.target.model_dump() != toc["anchor"]: raise HTTPException(409, "기존 목차의 삽입 위치를 유지해야 합니다.")
        selected = []
        stylemap = styles(header)
        builder = TocBuilder(header, section, available)
        if toc:
            # Existing managed layouts are frozen even if a document's general
            # formatting marker was changed or upgraded independently.
            builder.layout = toc.get("layout")
        for chosen in value.headings:
            index = chosen.paragraph
            if index >= len(section) or toc and index == toc["paragraph"]: raise HTTPException(409, "선택한 제목 문단이 없습니다.")
            heading = descriptor(section[index], index, str(chosen.heading_id), chosen.level)
            if heading["text_sha256"] != chosen.expected_text_sha256: raise HTTPException(409, "선택한 제목이 변경되었습니다.")
            previous = stylemap.get(section[index].get("styleIDRef"))
            existing = HEADING.fullmatch(previous.get("name", ""))
            if existing and existing.group(1) != heading["id"] or heading["id"] in active and active[heading["id"]]["paragraph"] != index: raise HTTPException(409, "제목 ID가 기존 제목과 일치하지 않습니다.")
            if not existing or existing.group(2) != str(chosen.level):
                clone = copy.deepcopy(previous); clone.set("name", PREFIX + "heading:" + heading["id"] + ":" + str(chosen.level)); clone.set("engName", clone.get("name"))
                new_id = builder.merge.append("styles", clone)
                if int(new_id) > 255: raise HTTPException(422, "문단 스타일 슬롯이 부족합니다.")
                section[index].set("styleIDRef", new_id)
            selected.append(heading)
        pages = {str(key): page for key, page in value.pages.items()} or {h["id"]:1 for h in selected}
        host, root_sid = builder.toc_table(ident, selected, pages)
        if toc: section.remove(section[toc["paragraph"]])
        section.insert(target + 1, host)
        preliminary = packed(data, header, section)
        planned = TocSemantic(preliminary)
        metadata = dict(policy=POLICY, toc_id=ident, heading_ids=[h["id"] for h in selected], pages=pages,
                        body_sha256=body_hash(planned, target+1), managed_sha256=digest(canonical(planned.node(planned.section[target+1])).encode()))
        if builder.layout is not None: metadata["layout"] = builder.layout
        rootstyle = next(x for x in header.iter(f"{{{HH}}}style") if x.get("id") == root_sid)
        rootstyle.set("engName", canonical(metadata))
        result = packed(data, header, section)
        _, _, _, final_toc = inspected(result)
        if not final_toc or final_toc["status"] != "current": raise ValueError("Composed metadata failed self-check")
        return result, {"toc_id":ident, "pass_index":value.pass_index, "pages":pages, "body_sha256":metadata["body_sha256"],
                        "headings":final_toc["headings"], "anchor":final_toc["anchor"], "plan":{"insert_after":target, "usable_width":available, "top_level_count":1, "toc_paragraph":target+1}}
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        raise HTTPException(422, "이 HWPX에 목차를 안전하게 구성할 수 없습니다.") from exc


def verify_toc(source, expected, actual, value, plan):
    verify_engine_output(source, expected, actual)
    try:
        _, _, _, toc = inspected(actual)
        if not toc or toc["id"] != str(value.toc_id) or toc["status"] != "current": raise ValueError("TOC durability failed")
        evidence = value.layout_evidence
        if evidence.export_sha256 != digest(actual): raise ValueError("Page evidence export mismatch")
        measured = {}
        by_id = {h["id"]: h for h in toc["headings"]}
        for item in evidence.headings:
            ident = str(item.id)
            if ident in measured or ident not in by_id: raise ValueError("Duplicate or unknown page evidence")
            heading = by_id[ident]
            if any(getattr(item, key) != heading[key] for key in ("section", "paragraph", "length", "text_sha256")): raise ValueError("Heading descriptor mismatch")
            if item.page > evidence.page_count: raise ValueError("Page outside actual document")
            measured[ident] = item.page
        if set(measured) != set(by_id): raise ValueError("Missing physical page evidence")
        page_values = [measured[h["id"]] for h in toc["headings"]]
        if page_values != sorted(page_values): raise ValueError("Pages out of heading order")
        converged = measured == plan["pages"]
        return {"verified":True, "converged":converged, "can_adopt":converged, "exhausted":not converged and value.pass_index >= 3,
                "measured_pages":measured, "pass_index":value.pass_index, "toc_id":toc["id"], "body_sha256":toc["body_sha256"], "headings":toc["headings"]}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "실제 쪽수와 제목 ID·문서 출력 연결을 확인할 수 없습니다.") from exc
