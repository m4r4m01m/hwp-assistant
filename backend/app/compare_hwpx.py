"""Bounded, read-only semantic comparison of the immutable HWPX inputs.

Paragraph/cell addresses are local to each input; they are never treated as stable
IDs. Definition IDs and cached line positions are not human document changes.
"""
import io
import json
import re
import zipfile
from collections import Counter
from bisect import bisect_left
from difflib import SequenceMatcher

from fastapi import HTTPException
from .hwpx import digest, validate

MAX_BLOCKS = 2000
MAX_TEXT = 250_000
MAX_NODES = 100_000
REFS = {"charPrIDRef": "charPr", "paraPrIDRef": "paraPr", "styleIDRef": "style",
        "borderFillIDRef": "borderFill", "tabPrIDRef": "tabPr", "numberingIDRef": "numbering",
        "bulletIDRef": "bullet"}
SKIP = {"linesegarray", "lineseg", "caretPosition"}


def local(node):
    return node.tag.split("}")[-1]


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def snapshot(data):
    names, roots = validate(data)
    if sum(sum(1 for _ in r.iter()) for r in roots.values()) > MAX_NODES:
        raise HTTPException(422, "문서 비교의 XML 항목 한도를 초과했습니다.")
    header = roots["Contents/header.xml"]
    definitions = {(local(n), n.get("id")): n for n in header.iter() if n.get("id") is not None}
    fonts = {(n.get("lang", "").lower(), f.get("id")): f.get("face", "")
             for n in header.iter() if local(n) == "fontface" for f in n}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        resources = {name: digest(archive.read(name)) for name in names if name.startswith("BinData/")}
    binary_refs = {}
    for n in roots["Contents/content.hpf"].iter():
        href = n.get("href", "").removeprefix("../").removeprefix("./")
        if href in resources:
            binary_refs[n.get("id")] = resources[href]
    memo = {}

    def reference(kind, ident, stack):
        key = (kind, ident)
        if key in stack or len(stack) > 12:
            return [kind, "unresolved-cycle"]
        if key not in memo:
            node = definitions.get(key)
            memo[key] = canonical(node, stack + (key,)) if node is not None else [kind, "missing", ident]
        return memo[key]

    def canonical(node, stack=(), *, skip_content=False, depth=0):
        if depth > 40:
            raise HTTPException(422, "문서 비교의 중첩 깊이 한도를 초과했습니다.")
        tag = local(node)
        attrs = {}
        for key, value in sorted(node.attrib.items()):
            key = key.split("}")[-1]
            if key in ("id", "instid"):
                continue
            if key in REFS:
                attrs[key] = reference(REFS[key], value, stack)
            elif tag == "fontRef":
                attrs[key] = fonts.get((key.lower(), value), ["missing-font", key, value])
            elif key == "binaryItemIDRef":
                attrs[key] = binary_refs.get(value, ["missing-resource", value])
            else:
                attrs[key] = value
        children = [canonical(c, stack, skip_content=skip_content, depth=depth + 1) for c in node
                    if local(c) not in SKIP and not (skip_content and local(c) == "subList")]
        return [tag, attrs, (node.text or "") if tag == "t" or (node.text or "").strip() else "", children]

    blocks, warnings = [], set()

    def cell_coordinate(value):
        if not re.fullmatch(r"[0-9]{1,5}", value) or int(value) > 10000:
            raise HTTPException(422, "문서 비교에서 유효하지 않은 표 셀 주소를 발견했습니다.")
        return int(value)

    def add(kind, text, shape, anchor, label):
        if len(blocks) >= MAX_BLOCKS or len(text) > 20_000:
            raise HTTPException(422, "문서 비교는 2,000개 문단·개체, 문단당 20,000자까지 지원합니다.")
        blocks.append({"kind": kind, "text": text, "format": fingerprint(shape),
                       "anchor": anchor, "label": label})

    def paragraph(p, sec, pi, cell_path=(), depth=0):
        if depth > 8:
            raise HTTPException(422, "중첩 표 비교는 8단계까지 지원합니다.")
        anchor = {"section": sec, "paragraph": pi, "cell_path": list(cell_path)}
        label = f"{sec + 1}구역 · {pi + 1}문단"
        if cell_path:
            label += " · " + " / ".join(f"표 {h['table'] + 1}, {h['row'] + 1}행 {h['col'] + 1}열, {h['paragraph'] + 1}문단" for h in cell_path)
        runs = [n for n in p if local(n) == "run"]
        text_parts, formatting, controls = [], [], []
        for run in runs:
            text = ""
            for n in run:
                if local(n) == "t":
                    text += n.text or ""
                    for child in n:
                        text += {"tab": "\t", "lineBreak": "\n", "nbSpace": "\u00a0", "fwSpace": "\u3000"}.get(local(child), "".join(child.itertext()))
                        text += child.tail or ""
                elif local(n) == "ctrl":
                    controls.extend(list(n))
                elif local(n) not in SKIP | {"secPr", "colPr", "charPr"}:
                    controls.append(n)
            text_parts.append(text)
            style = reference("charPr", run.get("charPrIDRef", "0"), ())
            # Adjacent runs with equal effective formatting are serialization noise.
            if formatting and formatting[-1][1] == style:
                formatting[-1][0] += len(text)
            else:
                formatting.append([len(text), style])
        text = "".join(text_parts)
        shape = [reference("paraPr", p.get("paraPrIDRef", "0"), ()),
                 reference("style", p.get("styleIDRef", "0"), ()), formatting,
                 {k: p.get(k) for k in ("pageBreak", "columnBreak") if p.get(k) not in (None, "0")}]
        if text or not controls:
            add("cell" if cell_path else "paragraph", text, shape, anchor, label)
        table_order = 0
        for ci, control in enumerate(controls):
            tag = local(control)
            control_anchor = {**anchor, "control": ci}
            if tag == "tbl":
                control_anchor["table"] = table_order
                table_order += 1
                rows, cols = control.get("rowCnt", "?"), control.get("colCnt", "?")
                add("table", f"표 {rows}행 × {cols}열", [shape[:2], canonical(control, skip_content=True)],
                    control_anchor, label + " · 표")
                cells = [c for row in control if local(row) == "tr" for c in row if local(c) == "tc"]
                for cell_index, cell in enumerate(cells):
                    address = next((n for n in cell if local(n) == "cellAddr"), None)
                    row = cell_coordinate(address.get("rowAddr", "0")) if address is not None else 0
                    col = cell_coordinate(address.get("colAddr", str(cell_index))) if address is not None else cell_index
                    for sub in cell:
                        if local(sub) != "subList":
                            continue
                        for cpi, child in enumerate(n for n in sub if local(n) == "p"):
                            hop = {"control": ci, "table": table_order - 1, "cell": cell_index, "paragraph": cpi, "row": row, "col": col}
                            paragraph(child, sec, pi, (*cell_path, hop), depth + 1)
            else:
                kind = "image" if tag == "pic" else "object"
                title = {"pic": "그림", "header": "머리말", "footer": "꼬리말", "footNote": "각주", "endNote": "미주"}.get(tag, tag)
                add(kind, title, canonical(control), control_anchor, label + " · " + title)
                if kind == "object":
                    warnings.add("수식·도형·필드·머리말 등은 개체 단위로 비교합니다. 개체 내부 글자별 수정자는 제공하지 않습니다.")

    sections = sorted((name for name in roots if re.fullmatch(r"Contents/section\d+\.xml", name)),
                      key=lambda name: int(re.search(r"section(\d+)", name)[1]))
    for sec, name in enumerate(sections):
        section = roots[name]
        for pi, p in enumerate(n for n in section if local(n) == "p"):
            paragraph(p, sec, pi)
        settings = [canonical(n) for n in section.iter() if local(n) in ("secPr", "colPr")]
        if settings:
            add("section", "용지·여백·구역 설정", settings, {"section": sec, "paragraph": 0, "cell_path": []}, f"{sec + 1}구역 설정")
    for name in sorted(roots):
        if name.startswith("Contents/") and name not in sections and name not in ("Contents/header.xml", "Contents/content.hpf"):
            add("object", name, canonical(roots[name]), {"section": 0, "paragraph": 0, "cell_path": []}, "문서 부가 구성 · " + name)
            warnings.add("바탕쪽 등 부가 구성은 구성 단위로 비교하며 해당 위치의 자동 강조는 지원하지 않습니다.")
    if sum(len(b["text"]) for b in blocks) > MAX_TEXT:
        raise HTTPException(422, "문서 비교는 총 250,000자까지 지원합니다.")
    return {"blocks": blocks, "warnings": sorted(warnings)}


def align(left, right):
    """Order-aware anchors, then bounded similar replacements; never trust para IDs.

    Returned pairs carry an ambiguity flag so a repeated paragraph is not given
    a confident historical owner when its correspondence cannot be established.
    """
    key = lambda b: (b["kind"], b["text"])
    a, b = [key(x) for x in left], [key(x) for x in right]
    ca, cb = Counter(a), Counter(b)
    pairs, removed, added = [], [], []
    for tag, i, end_i, j, end_j in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for x, y in zip(range(i, end_i), range(j, end_j)):
                repeated = ca[a[x]] > 1 or cb[b[y]] > 1
                pairs.append((x, y, repeated and (x != y or ca[a[x]] != cb[b[y]]
                                                  or left[x]["format"] != right[y]["format"])))
        elif tag == "replace":
            available = set(range(j, end_j))
            for x in range(i, end_i):
                candidates = []
                for y in range(max(j, j + x - i - 8), min(end_j, j + x - i + 9)):
                    if y not in available or left[x]["kind"] != right[y]["kind"]:
                        continue
                    # Repetitive long strings must not trigger quadratic work
                    # for each candidate in an already bounded matching window.
                    similarity = SequenceMatcher(None, left[x]["text"][:1000], right[y]["text"][:1000], autojunk=True).ratio()
                    if similarity >= .45 or (end_i - i == end_j - j == 1):
                        candidates.append((similarity, -abs((x - i) - (y - j)), y))
                if candidates:
                    candidates.sort(reverse=True)
                    y = candidates[0][2]
                    ambiguous = len(candidates) > 1 and candidates[0][:1] == candidates[1][:1]
                    pairs.append((x, y, ambiguous)); available.remove(y)
                else:
                    removed.append(x)
            added.extend(sorted(available))
        else:
            removed.extend(range(i, end_i)); added.extend(range(j, end_j))
    # A unique exact block moved across an anchor is still the same content.
    by_key = {b[y]: y for y in added if ca[b[y]] == cb[b[y]] == 1}
    for x in list(removed):
        y = by_key.get(a[x])
        if y is not None:
            pairs.append((x, y, False)); removed.remove(x); added.remove(y)
    return pairs, removed, added


def change_kind(left, right):
    if left["text"] != right["text"]:
        return "modified"
    if left["format"] != right["format"]:
        return "format" if left["kind"] in ("paragraph", "cell") else "object"
    return None


def moved_pairs(pairs):
    """Only relative-order changes count as moves; insertions do not blame every
    following paragraph. A longest increasing subsequence is the stable backbone.
    """
    ordered = sorted(pairs)
    tails, tail_indices, previous = [], [], []
    for index, (_, ri, _) in enumerate(ordered):
        pos = bisect_left(tails, ri)
        previous.append(tail_indices[pos - 1] if pos else -1)
        if pos == len(tails):
            tails.append(ri); tail_indices.append(index)
        else:
            tails[pos], tail_indices[pos] = ri, index
    stable = set()
    current = tail_indices[-1] if tail_indices else -1
    while current >= 0:
        stable.add(ordered[current][:2]); current = previous[current]
    return {pair[:2] for pair in ordered} - stable
