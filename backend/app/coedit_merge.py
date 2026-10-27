"""Conservative three-way HWPX merge. Never resolve overlapping edits by last writer wins."""
import copy
import difflib
import io
import zipfile
from xml.etree import ElementTree as E
from fastapi import HTTPException
from .hwpx import validate
from .block_hwpx import serialize_block_xml


def conflict():
    raise HTTPException(409, "같은 내용 또는 문서 구조를 동시에 수정했습니다. 내 편집을 다운로드한 뒤 최신 공동문서를 확인하세요.")


def merge_text(base, current, incoming):
    if incoming == base or incoming == current: return current
    if current == base: return incoming
    base, current, incoming = base or "", current or "", incoming or ""
    if max(map(len, (base, current, incoming))) > 4096: conflict()
    def edits(value):
        return [(a, b, value[c:d]) for tag, a, b, c, d in difflib.SequenceMatcher(None, base, value, autojunk=False).get_opcodes() if tag != "equal"]
    left, right = edits(current), edits(incoming)
    for a, b, text in right:
        for x, y, other in left:
            if (a, b, text) == (x, y, other): continue
            if max(a, x) < min(b, y) or (a == b and x <= a <= y) or (x == y and a <= x <= b): conflict()
    result = base
    for a, b, text in sorted(set(left + right), reverse=True): result = result[:a] + text + result[b:]
    return result


def _same(a, b):
    return E.tostring(a) == E.tostring(b)


def _node(base, current, incoming):
    if _same(base, incoming) or _same(current, incoming): return copy.deepcopy(current)
    if _same(base, current): return copy.deepcopy(incoming)
    if not base.tag == current.tag == incoming.tag or not len(base) == len(current) == len(incoming): conflict()
    paragraph_ids = [child.get("id") for child in base if child.tag.rsplit("}", 1)[-1] == "p"]
    if len(paragraph_ids) > 1 and (None in paragraph_ids or len(set(paragraph_ids)) != len(paragraph_ids)):
        conflict()
    # Matching child IDs prevent index-based merges across reordered paragraphs/runs.
    for name in ("id", "paraID"):
        if len({value.get(name) for value in (base, current, incoming)}) != 1: conflict()
    result = copy.deepcopy(current)
    for key in base.attrib.keys() | current.attrib.keys() | incoming.attrib.keys():
        a, b, c = base.get(key), current.get(key), incoming.get(key)
        if c == a or c == b: value = b
        elif b == a: value = c
        else: conflict()
        if value is None: result.attrib.pop(key, None)
        else: result.set(key, value)
    result.text = merge_text(base.text, current.text, incoming.text)
    result.tail = merge_text(base.tail, current.tail, incoming.tail)
    result[:] = [_node(a, b, c) for a, b, c in zip(base, current, incoming)]
    return result


def merge_hwpx(base, current, incoming):
    for data in (base, current, incoming): validate(data)
    if incoming == base or incoming == current: return current
    if current == base: return incoming
    def unpack(data):
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            return {item.filename: source.read(item) for item in source.infolist()}
    old, live, edit = map(unpack, (base, current, incoming))
    result = {}
    for name in old.keys() | live.keys() | edit.keys():
        a, b, c = old.get(name), live.get(name), edit.get(name)
        if c == a or c == b: value = b
        elif b == a: value = c
        elif name.startswith("Preview/"):
            value = b  # Embedded thumbnail is not document content; the engine renders HWPX.
        elif name.startswith("Contents/section") and name.endswith(".xml") and all(value is not None for value in (a, b, c)):
            roots = [E.fromstring(value) for value in (a, b, c)]
            for root in roots:
                for parent in root.iter():
                    for child in list(parent):
                        if child.tag.rsplit("}", 1)[-1] == "linesegarray": parent.remove(child)
            value = serialize_block_xml(_node(*roots))
        else:
            # In particular, concurrent style-ID allocation cannot be safely joined.
            conflict()
        if value is not None: result[name] = value
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(current)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        known = set()
        for item in source.infolist():
            if item.filename in result: target.writestr(item, result[item.filename]); known.add(item.filename)
        for name in sorted(result.keys() - known): target.writestr(name, result[name])
    data = output.getvalue(); validate(data)
    return data
