"""Lay out already verified report paragraphs; never invent or replace their text."""
import copy
import io
import math
import re
import zipfile
from xml.etree import ElementTree as E
from .report_hwpx import HP, HH, paragraphs


def width(text):
    return sum(1 if ord(c) > 255 else .56 for c in text)


def lines(text, limit, title=False):
    words = re.findall(r"\S+\s*|\s+", text)
    if title and limit < width(text) <= limit * 2:
        # Balance a two-line title at a word boundary; preserve every character.
        cuts = [m.end() for m in re.finditer(r"\s+", text)]
        if cuts:
            cut = min(cuts, key=lambda n: abs(width(text[:n]) - width(text) / 2))
            if max(width(text[:cut]), width(text[cut:])) <= limit:
                return [text[:cut], text[cut:]]
    result, current = [], ""
    for word in words:
        if current and width(current + word.rstrip()) > limit:
            result.append(current); current = ""
        current += word
    if current: result.append(current)
    return result or [text]


def layout_report(data, plan):
    section, before = paragraphs(data)
    if before != [""] + [p["text"] for p in plan]:
        raise ValueError("Report text changed before layout")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        header = E.fromstring(z.read("Contents/header.xml"))
        chars = {e.get("id"): e for e in header.iter(f"{{{HH}}}charPr")}
        paras = {e.get("id"): e for e in header.iter(f"{{{HH}}}paraPr")}
        prepared = []
        for item in plan:
            p = section[item["paragraph"]]
            run = p.find(f"{{{HP}}}run"); cid = run.get("charPrIDRef")
            size = int(chars[cid].get("height")) / 100
            pr = paras[p.get("paraPrIDRef")]
            margins = pr.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")
            margin = {e.tag.split("}")[-1]: int(e.get("value")) / 100 for e in margins}
            # Conservative word boundaries also work around this pinned engine's
            # incomplete KEEP_WORD rendering. These are soft breaks inside one paragraph.
            available = 481.9 - margin["left"] - max(0, -margin["intent"]) - 10
            limit = available / size
            parts = lines(item["text"], limit, item["kind"] == "title")
            for child in list(p): p.remove(child)
            for n, part in enumerate(parts):
                r = E.SubElement(p, f"{{{HP}}}run", {"charPrIDRef": cid})
                t = E.SubElement(r, f"{{{HP}}}t"); t.text = part
                if n < len(parts) - 1: E.SubElement(t, f"{{{HP}}}lineBreak")
            line_count = sum(max(1, math.ceil(width(part.rstrip()) / limit)) for part in parts)
            spacing = 1.3 if item["kind"] == "source" else 1.6
            cost = line_count * size * spacing + margin["next"]
            prepared.append((p, item, cost))
        used = 0
        for index, (p, item, cost) in enumerate(prepared):
            # Keep a heading and its first paragraph together where they fit.
            following = prepared[index + 1][2] if item["kind"] == "heading" and index + 1 < len(prepared) else 0
            needed = cost + following if cost + following <= 625 else cost
            new_page = used > 0 and used + needed > 625
            p.set("pageBreak", "1" if new_page else "0")
            used = cost if new_page else used + cost
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as dest:
            for entry in z.infolist():
                dest.writestr(copy.copy(entry), E.tostring(section, encoding="utf-8", xml_declaration=True)
                              if entry.filename == "Contents/section0.xml" else z.read(entry))
    result = output.getvalue()
    assert paragraphs(result)[1] == before
    return result
