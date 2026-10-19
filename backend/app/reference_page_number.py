"""Native current/total page fields isolated into separate footer paragraphs.

The pinned renderer conflates two AutoNumber controls in one paragraph. A small
borderless inline table keeps each native field in its own paragraph/cell while
remaining an editable HWPX footer. No current or total page value is baked in.
"""
import copy
from xml.etree import ElementTree as E

from .document_standard import HP, HH, set_margins


def _append(collection, node):
    node.set("id", str(max((int(item.get("id")) for item in collection), default=-1) + 1))
    collection.append(node)
    collection.set("itemCnt", str(len(collection)))
    return node.get("id")


def page_total_footer(header, host, cid, pid, position):
    """Append a native PAGE / TOTAL_PAGE footer to a new report's control host.

    ``cid``/``pid`` are the frozen page-number character/paragraph styles. The
    font, size and color are inherited from those styles; only cell alignment
    and zero local margins are derived for the three independently laid fields.
    Returns the new native footer element.
    """
    chars = header.find(f".//{{{HH}}}charProperties")
    paras = header.find(f".//{{{HH}}}paraProperties")
    char = next(item for item in chars if item.get("id") == str(cid))
    paragraph = next(item for item in paras if item.get("id") == str(pid))
    align = position.removeprefix("BOTTOM_")
    outer = copy.deepcopy(paragraph)
    outer.find(f"{{{HH}}}align").set("horizontal", align)
    set_margins(outer)
    outer_pid = _append(paras, outer)
    cell_pids = []
    for horizontal in ("RIGHT", "CENTER", "LEFT"):
        cell = copy.deepcopy(paragraph)
        cell.find(f"{{{HH}}}align").set("horizontal", horizontal)
        set_margins(cell)
        cell_pids.append(_append(paras, cell))
    run = host.find(f"{{{HP}}}run")
    if run is None:
        raise ValueError("Page-number host has no run")
    ctrl = E.SubElement(run, f"{{{HP}}}ctrl")
    footer = E.SubElement(ctrl, f"{{{HP}}}footer", dict(id="910001", applyPageType="BOTH"))
    sub = E.SubElement(footer, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK",
        vertAlign="TOP", linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0"))
    p = E.SubElement(sub, f"{{{HP}}}p", dict(id="910002", paraPrIDRef=outer_pid,
        styleIDRef="0", pageBreak="0", columnBreak="0", merged="0"))
    r = E.SubElement(p, f"{{{HP}}}run", dict(charPrIDRef=str(cid)))
    # Leave enough room for the bounded report's two-digit physical page count.
    size = int(char.get("height"))
    # A full-em separator cell also accommodates the pinned renderer's minimum
    # inline-cell text box; a half-em cell lets '/' overlap the total-page glyph.
    widths = (size * 2, size, size * 2)
    line = paragraph.find(f".//{{{HH}}}lineSpacing")
    height = max(size, round(size * float(line.get("value", "100")) / 100))
    table = E.SubElement(r, f"{{{HP}}}tbl", dict(id="910010", zOrder="0", numberingType="TABLE",
        textWrap="TOP_AND_BOTTOM", textFlow="BOTH_SIDES", lock="0", dropcapstyle="None", pageBreak="CELL",
        repeatHeader="0", rowCnt="1", colCnt="3", cellSpacing="0", borderFillIDRef="1", noAdjust="0"))
    E.SubElement(table, f"{{{HP}}}sz", dict(width=str(sum(widths)), height=str(height),
        widthRelTo="ABSOLUTE", heightRelTo="ABSOLUTE", protect="0"))
    E.SubElement(table, f"{{{HP}}}pos", dict(treatAsChar="1", affectLSpacing="0", flowWithText="1", allowOverlap="0",
        holdAnchorAndSO="0", vertRelTo="PARA", horzRelTo="PARA", vertAlign="TOP", horzAlign=align,
        vertOffset="0", horzOffset="0"))
    zero = dict(left="0", right="0", top="0", bottom="0")
    E.SubElement(table, f"{{{HP}}}outMargin", zero)
    E.SubElement(table, f"{{{HP}}}inMargin", zero)
    row = E.SubElement(table, f"{{{HP}}}tr")
    for index, (kind, width, cell_pid) in enumerate(zip(("PAGE", None, "TOTAL_PAGE"), widths, cell_pids)):
        cell = E.SubElement(row, f"{{{HP}}}tc", dict(name="", header="0", hasMargin="1", protect="0",
            editable="0", dirty="0", borderFillIDRef="1"))
        content = E.SubElement(cell, f"{{{HP}}}subList", dict(id="", textDirection="HORIZONTAL", lineWrap="BREAK",
            vertAlign="CENTER", linkListIDRef="0", linkListNextIDRef="0", textWidth="0", textHeight="0", hasTextRef="0", hasNumRef="0"))
        cp = E.SubElement(content, f"{{{HP}}}p", dict(id=str(910020 + index), paraPrIDRef=cell_pid,
            styleIDRef="0", pageBreak="0", columnBreak="0", merged="0"))
        cr = E.SubElement(cp, f"{{{HP}}}run", dict(charPrIDRef=str(cid)))
        if kind:
            control = E.SubElement(cr, f"{{{HP}}}ctrl")
            number = E.SubElement(control, f"{{{HP}}}autoNum", dict(num="1", numType=kind))
            E.SubElement(number, f"{{{HP}}}autoNumFormat", dict(type="DIGIT", userChar="", prefixChar="", suffixChar="", supscript="0"))
        E.SubElement(cr, f"{{{HP}}}t").text = "/" if kind is None else None
        E.SubElement(cell, f"{{{HP}}}cellAddr", dict(colAddr=str(index), rowAddr="0"))
        E.SubElement(cell, f"{{{HP}}}cellSpan", dict(colSpan="1", rowSpan="1"))
        E.SubElement(cell, f"{{{HP}}}cellSz", dict(width=str(width), height=str(height)))
        E.SubElement(cell, f"{{{HP}}}cellMargin", zero)
    return footer


append_page_total_footer = page_total_footer
