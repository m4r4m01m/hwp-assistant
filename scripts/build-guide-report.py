"""Guide-refined report artifact, reusing the standard's approved-text pipeline.

This is an explicit artifact recipe, not a general template engine or live TOC field.
"""
import argparse
import copy
import importlib.util
import json
import math
from pathlib import Path
from xml.etree import ElementTree as E
import zipfile

module = importlib.util.spec_from_file_location("standard_report", Path(__file__).with_name("build-standard-report.py"))
base = importlib.util.module_from_spec(module)
module.loader.exec_module(base)
tag, HH, HP = base.tag, base.HH, base.HP


def specification():
    old = base.specification()
    lookup = {i["id"]: i for i in old["items"]}
    items, pages, toc = [], [[] for _ in range(9)], []

    def text(page, value, kind="square"):
        key = f"g{len(items):03d}"
        items.append(dict(id=key, text=value, kind=kind))
        if page: pages[page - 1].append(dict(type="text", id=key))
        return key

    def gap(page, points): pages[page - 1].append(dict(type="gap", points=points))

    def heading(page, value, level=1, entry=True):
        key = text(page, value, "h1" if level == 1 else "h2")
        if entry: toc.append(dict(target=key, label=key, page=page, level=level))

    def group(page, main, supporting, detail):
        text(page, "□ " + main, "square")
        text(page, "ㅇ " + supporting, "circle")
        text(page, "- " + detail, "dash")

    def old_object(page, object_type):
        for index, block in enumerate(old["pages"][page - 1]):
            if block["type"] != object_type: continue
            caption = lookup[old["pages"][page - 1][index - 1]["id"]]
            text(page, caption["text"], "caption")
            node = copy.deepcopy(block)
            if object_type == "table":
                node["rows"] = [[text(None, lookup[k]["text"], "th" if y == 0 else "cell") for k in row] for y, row in enumerate(block["rows"])]
            else:
                node["labels"] = [text(None, lookup[k]["text"], "diagram") for k in block["labels"]]
            pages[page - 1].append(node)

    gap(1, 60)
    text(1, "금융권 해킹 대응 보고서", "cover")
    gap(1, 30)
    text(1, "경영진 의사결정과 90일 실행계획", "subtitle")
    gap(1, 60)
    text(1, "2026. 10. 7.", "date")
    gap(1, 60)
    text(1, "경영진 검토용", "department")
    text(1, "공개자료 기준: 2026. 10. 6. · 사실과 제안 구분", "covernote")

    heading(3, "1. 추진 배경 및 개요")
    group(3, "(요약) 고객정보 보호, 업무 연속성, 외부 결제망 관리를 함께 점검할 필요",
          "공개 사례에서 정보유출·전산 중단·외부 결제정보 유출이 각각 확인됨[1~3]",
          "사고 건수의 증가율은 단정하지 않으며, 조직의 실제 노출과 복구 능력은 별도 확인 필요")
    old_object(3, "table")
    group(3, "(결정) 경영진은 점검 책임자와 우선 검증 대상을 확정할 필요",
          "보안·IT·업무 부서가 핵심 업무 1개와 중요 외부 업체를 선정해 운영 증거를 확인할 것을 제안함",
          "예산·인력·목표값은 내부 현황 확인 후 산정하며, 특정 제품 구매를 전제로 하지 않음")

    heading(4, "2. 주요 내용")
    heading(4, "가. 공개 사고 사례 비교", 2)
    group(4, "(차이점) 정보유출 사고와 업무 중단 사고는 확인할 통제와 복구 기준이 다름",
          "아래 사례는 공개자료에 기재된 사실과 보고서의 관리 시사점을 구분해 비교함",
          "회사의 사고 공지만으로 전체 피해 규모나 공격 기법을 확정하지 않음")
    old_object(4, "table")
    text(4, "※ 롯데카드 제재는 일부 업무의 영업정지이며 전체 서비스 중단을 뜻하지 않음. 자료: [1~3].", "guide_note")

    heading(5, "2. 주요 내용 (계속)", entry=False)
    heading(5, "나. 공격 경로와 피해 확산", 2)
    old_object(5, "image")
    text(5, "※ 그림은 통제 위치를 설명하는 개념도이며 개별 사고의 실제 공격 순서를 재현한 것이 아님.", "guide_note")
    group(5, "(통제) 진입 차단부터 복구까지 연결해 보안 통제의 작동 여부를 확인할 필요",
          "보안·IT 부서는 관리자 다중인증, 최소 권한, 중요 구간 분리 및 탐지 시나리오를 확인할 것을 제안함",
          "외부 업체도 계약·운영·종료 단계별 책임과 통지 경로를 점검하되, 법적 의무는 별도 확인 필요[4, 5]")

    heading(6, "2. 주요 내용 (계속)", entry=False)
    heading(6, "다. 우선 통제와 확인 증거", 2)
    group(6, "(검증) 정책의 존재와 실제 운영 결과를 구분해 완료 여부를 판단할 필요",
          "각 담당 부서는 아래 증거를 수집하고 누락·실패 항목의 보완 기한을 지정할 것을 제안함",
          "미조치 예외에는 책임자·임시 통제·만료일을 기록하고 반복 연장 시 경영진 검토 필요")
    old_object(6, "table")
    text(6, "※ 담당 부서와 완료 시점은 제안이며 조직의 규정·업무 영향·시스템 제약에 따라 확정 필요.", "guide_note")

    heading(7, "3. 시사점 및 향후계획")
    heading(7, "가. 90일 실행계획", 2)
    old_object(7, "image")
    group(7, "(추진) 현황 점검 → 복구·탐지 연습 → 잔여 위험 판단 순으로 추진할 것을 제안함",
          "30일 내 책임자·기준을 정하고 60일 내 연습 증거, 90일 내 통합 검증 결과를 확보하는 계획임",
          "목표값은 현황 조사 후 정하며 검증 전 수치를 완료 성과로 보고하지 않음")
    old_object(7, "table")

    heading(8, "3. 시사점 및 향후계획 (계속)", entry=False)
    heading(8, "나. 사고 대응과 고객 보호", 2)
    old_object(8, "table")
    group(8, "(보호) 고객에게 확인 사실·조사 중인 사항·현재 필요한 행동을 나누어 안내할 필요",
          "고객·준법 부서는 문의 경로와 다음 안내 시점을 명시하고 적용되는 신고·통지 요건을 확인할 것을 제안함",
          "서비스 재개 후에도 잔여 침해·데이터 무결성·고객 보호 조치를 확인한 뒤 종료 여부 판단 필요")
    text(8, "※ 법정 신고·통지 기한은 적용 법령과 사고 유형에 따라 준법 담당자의 확인 필요.", "guide_note")

    key = text(9, "붙임. 출처와 작성 범위", "attachment")
    toc.append(dict(target=key, label=key, page=9, level=1))
    for item in old["items"]:
        if item["kind"] == "source": text(9, item["text"], "source")
    group(9, "(범위) 공개자료의 확인 범위에서 작성한 경영진 검토용 보고서임",
          "사실 확인 기준일은 2026. 10. 6.이며 전수 사고 통계나 개별 사고의 기술 조사 결과는 아님",
          "표 1·3~5와 그림 1~2는 제안·분석이며 내부 자산·예산·계약·복구 능력은 별도 확인 필요")
    text(9, "※ 서식은 사용자 지정 HWPX 기준서와 2026. 7. 교육 가이드의 본문 작성 규칙을 반영함.", "guide_note")

    # Titles and page numbers are independently approved text, never literal dot strings.
    toc_title = text(None, "< 목  차 >", "toc_title")
    for entry in toc: entry["number"] = text(None, str(entry["page"]), "toc" if entry["level"] == 1 else "toc_sub")
    pages[1].append(dict(type="toc", title=toc_title, entries=toc))
    return dict(title="금융권 해킹 대응 보고서", as_of="2026-10-06", revised="2026-10-07", items=items, pages=pages, toc=toc)


class Builder(base.Builder):
    def __init__(self, work):
        super().__init__(work)
        for kind, bullet in self.profile["bullets"].items():
            self.style(kind, 1500, False, "LEFT", 160, 750)
            para = next(p for p in self.paras if p.get("id") == self.formats[kind][1])
            base.set_margins(para, left=bullet["marker_left"], intent=-bullet["hanging"], after=750)
        self.style("guide_note", 1300, False, "LEFT", 130, 650)
        char = next(c for c in self.chars if c.get("id") == self.formats["guide_note"][0])
        fonts = char.find(f"{{{HH}}}fontRef")
        for lang in fonts.attrib:
            face = next(f for f in self.header.iter(f"{{{HH}}}fontface") if f.get("lang", "").lower() == lang.lower())
            font = next((f for f in face if f.get("face") == "맑은 고딕"), None)
            if font is None:
                font = copy.deepcopy(list(face)[0]); font.set("id", str(max(int(f.get("id")) for f in face) + 1)); font.set("face", "맑은 고딕")
                face.append(font); face.set("fontCnt", str(len(face)))
            fonts.set(lang, font.get("id"))
        self.style("toc_sub", 1400, False, "LEFT", 160, 1600)
        self.style("toc", 1600, True, "LEFT", 160, 1800)
        for kind in ("toc", "toc_sub"):
            para = next(p for p in self.paras if p.get("id") == self.formats[kind][1])
            para.set("tabPrIDRef", "1")
            base.set_margins(para, left=2000 if kind == "toc" else 3500, right=2000, after=1800 if kind == "toc" else 1000)

    def toc(self, block, approved):
        source = list(self.source_sec)[129].find(f".//{tag('tbl')}")
        tbl = copy.deepcopy(source)
        self.n += 1
        tbl.attrib.update(id=str(self.n), zOrder="0", repeatHeader="0", pageBreak="NONE")
        tbl.find(tag("sz")).set("height", "56500")
        for cell in tbl.iter(tag("tc")):
            address = cell.find(tag("cellAddr")); sl = cell.find(tag("subList"))
            for p in list(sl): sl.remove(p)
            x, y = int(address.get("colAddr")), int(address.get("rowAddr"))
            if y == 0 and x == 1:
                sl.append(self.restyled(approved[block["title"]], "toc_title"))
            elif y == 2:
                cell.find(tag("cellSz")).set("height", "52000")
                cell.find(tag("cellMargin")).attrib.update(left="0", right="0", top="3500", bottom="3000")
                for entry in block["entries"]:
                    kind = "toc" if entry["level"] == 1 else "toc_sub"
                    p = self.restyled(approved[entry["label"]], kind)
                    r = E.SubElement(p, tag("run"), {"charPrIDRef": self.formats[kind][0]})
                    t = E.SubElement(r, tag("t")); t.text = " "
                    E.SubElement(t, tag("tab"), dict(width="1000", leader="3", type="2"))
                    number = self.restyled(approved[entry["number"]], kind)
                    for run in number.findall(tag("run")): p.append(run)
                    sl.append(p)
            else: sl.append(self.paragraph("host"))
        p = self.paragraph("host"); p.find(tag("run")).append(tbl)
        return p


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "assemble"])
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(); work = args.work; work.mkdir(parents=True, exist_ok=True)
    b = Builder(work)
    if args.phase == "prepare":
        plan = specification()
        for batch, start in enumerate(range(0, len(plan["items"]), 20)):
            section = b.section()
            for n, item in enumerate(plan["items"][start:start + 20], 1):
                item.update(batch=batch, paragraph=n, before=f"GUIDE_SLOT_{item['id']}")
                p = b.paragraph("body", item["before"]); p.set("pageBreak", "1"); section.append(p)
            b.write(work / f"scaffold-{batch}.hwpx", section)
        (work / "report-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(items=len(plan["items"]), batches=batch+1)))
        return
    plan = json.loads((work / "report-plan.json").read_text(encoding="utf-8"))
    receipts = json.loads((work / "exact-command-receipts.json").read_text(encoding="utf-8"))["receipts"]
    approved = {}
    for batch in sorted({i["batch"] for i in plan["items"]}):
        with zipfile.ZipFile(work / f"applied-{batch}.hwpx") as z:
            section = E.fromstring(z.read("Contents/section0.xml"))
        for item in (i for i in plan["items"] if i["batch"] == batch):
            p = list(section)[item["paragraph"]]
            actual = "".join(t.text or "" for t in p.iter(tag("t")))
            if actual != item["text"]: raise ValueError("Unapproved text " + item["id"])
            if not any(r["id"] == item["id"] and r["text_sha256"] == base.hashlib.sha256(actual.encode()).hexdigest() for r in receipts):
                raise ValueError("Missing receipt " + item["id"])
            approved[item["id"]] = p
    section = b.section(); images = []; by_id = {i["id"]: i for i in plan["items"]}
    for page, blocks in enumerate(plan["pages"]):
        for n, block in enumerate(blocks):
            kind = block["type"]
            if kind == "text":
                key = block["id"]; role = by_id[key]["kind"]
                p = b.restyled(approved[key], role)
                if role == "cover":
                    p = b.table(dict(rows=[[key]], widths=[100]), approved)
                    tbl = p.find(f".//{tag('tbl')}"); tbl.find(tag("sz")).set("height", "6519")
                    cell = tbl.find(f".//{tag('tc')}"); cell.set("borderFillIDRef", b.cover_border)
                    cell.find(tag("cellSz")).set("height", "6519")
                    sl = cell.find(tag("subList")); sl.remove(list(sl)[0]); sl.append(b.restyled(approved[key], "cover"))
            elif kind == "toc": p = b.toc(block, approved)
            elif kind == "table": p = b.table(block, approved)
            elif kind == "image": images.append(block["name"]); p = b.picture(block["name"], len(images))
            else:
                role = f"gap-{block['points']}"
                if role not in b.formats: b.style(role, block["points"] * 100, False, "CENTER", 100, 0)
                p = b.paragraph(role)
            if page and n == 0: p.set("pageBreak", "1")
            section.append(p)
    b.write(work / "financial-cyber-standard.hwpx", section, images)
    print(json.dumps(dict(planned_pages=9, content_tables=5, images=2, toc_entries=len(plan["toc"]))))


if __name__ == "__main__": run()
