"""Check the actual rendered report against its approved plan, without live DB writes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree as E
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("work", type=Path)
args = parser.parse_args(); work = args.work
plan = json.loads((work / "report-plan.json").read_text(encoding="utf-8"))
proof = json.loads((work / "render-proof.json").read_text(encoding="utf-8"))
receipts = json.loads((work / "exact-command-receipts.json").read_text(encoding="utf-8"))["receipts"]
assert proof["count"] == proof["reopened"]["pageCount"] == 9
assert proof["contentLoss"]["count"] == 0
assert len(receipts) == len(plan["items"]) == len({r["id"] for r in receipts})
by_id = {i["id"]: i for i in plan["items"]}
for item in plan["items"]:
    assert any(r["id"] == item["id"] and r["text_sha256"] == hashlib.sha256(item["text"].encode()).hexdigest() for r in receipts)

compact = lambda s: re.sub(r"\s+", "", s)
svg_pages = {p: E.parse(work / "pages" / f"page-{p}.svg").getroot() for p in range(1, 10)}
visible = {p: compact("".join("".join(e.itertext()) for e in root.iter("{http://www.w3.org/2000/svg}text"))) for p, root in svg_pages.items()}
toc_results = []
for item in plan["toc"]:
    title = by_id[item["label"]]["text"]
    assert compact(title) in visible[2], title
    assert compact(title) in visible[item["page"]], (title, item["page"])
    assert by_id[item["number"]]["text"] == str(item["page"])
    toc_results.append({"title": title, "physical_page": item["page"], "heading_rendered": True})

# Check marker positions in the engine's SVG, not merely values written to XML.
markers = []
for p in range(3, 10):
    nodes = list(svg_pages[p].iter("{http://www.w3.org/2000/svg}text"))
    first_x = {}
    for marker in ("□", "ㅇ", "-"):
        found = next(e for e in nodes if (e.text or "").strip() == marker
                     and float(e.get("font-size")) == 20 and float(e.get("y")) < 1000)
        first_x[marker] = float(found.get("x"))
    assert abs((first_x["ㅇ"] - first_x["□"]) - 1000/75) < 1
    assert abs((first_x["-"] - first_x["□"]) - 2000/75) < 1
    markers.append({"page": p, "marker_x_px": first_x})

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
with zipfile.ZipFile(work / "financial-cyber-standard.hwpx") as z:
    section = E.fromstring(z.read("Contents/section0.xml"))
    texts = ["".join(t.text or "" for t in p.iter(f"{{{HP}}}t")) for p in section.iter(f"{{{HP}}}p") if p.find(f".//{{{HP}}}tbl") is None]
    assert not any("GUIDE_SLOT_" in t or "STANDARD_SLOT_" in t for t in texts)
    tables = len(list(section.iter(f"{{{HP}}}tbl")))
    images = len(list(section.iter(f"{{{HP}}}pic")))
    tabs = len(list(section.iter(f"{{{HP}}}tab")))
    assert (tables, images, tabs) == (7, 2, len(plan["toc"]))
    bullet_counts = Counter(t[0] for t in texts if t.startswith(("□ ", "ㅇ ", "- ")))
    assert bullet_counts == Counter({"□": 8, "ㅇ": 8, "-": 8})
    for page in plan["pages"]:
        for block in page:
            if block["type"] != "text": continue
            text = by_id[block["id"]]["text"]
            assert text in texts, text

result = {"pages": 9, "toc": toc_results, "toc_kind": "native leaders; render-verified snapshot, not a live field",
          "content_tables": 5, "layout_tables": 2, "images": 2, "outline_points": dict(bullet_counts),
          "marker_positions": markers, "exact_commands": len(receipts), "content_loss_count": 0,
          "real_engine": True, "fresh_model_calls": 0, "human_click_approval": False, "hancom_verified": False,
          "engine_warnings": proof["warnings"]["summary"],
          "limitations": ["rhwp still splits some Korean words despite KEEP_WORD in HWPX", "Font substitution possible in preview", "TOC must be regenerated after pagination changes"]}
(work / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"pages": 9, "toc_entries_verified": len(toc_results), "exact_commands": len(receipts), "content_loss": 0}))
