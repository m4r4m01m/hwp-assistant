"""Create a new report from the uploaded writing standard; never edit its source.

prepare makes text-free scaffolds. assemble only lays out text exported after
rhwp fenced exact commands; the corresponding receipts remain in the work folder.
This is an opt-in artifact workflow, not an automatic change to report-task APIs.
"""
import argparse
import copy
import hashlib
import io
import json
import struct
import math
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.document_standard import load_standard, set_margins

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
HS = "http://www.hancom.co.kr/hwpml/2011/section"
OPF = "http://www.idpf.org/2007/opf/"
for prefix, uri in (("hp", HP), ("hh", HH), ("hc", HC), ("hs", HS), ("opf", OPF)):
    E.register_namespace(prefix, uri)

def tag(name):
    return f"{{{HP}}}{name}"

def xml(root):
    return E.tostring(root, encoding="utf-8", xml_declaration=True)

def specification():
    items, pages = [], [[] for _ in range(9)]
    def text(page, value, kind="body"):
        key = f"t{len(items):03d}"
        items.append(dict(id=key, text=value.replace("\n", " "), kind=kind))
        if page: pages[page - 1].append(dict(type="text", id=key))
        return key
    def table(page, title, widths, rows):
        text(page, title, "caption")
        ids = [[text(None, cell, "th" if i == 0 else "cell") for cell in row] for i, row in enumerate(rows)]
        pages[page - 1].append(dict(type="table", widths=widths, rows=ids))
    def gap(page, points):
        pages[page - 1].append(dict(type="gap", points=points))
    def diagram(page, name, title, labels):
        text(page, title, "caption")
        ids = [text(None, s, "diagram") for s in labels]
        pages[page - 1].append(dict(type="image", name=name, labels=ids))

    gap(1, 60)
    text(1, "금융권 해킹 대응 보고서", "cover")
    gap(1, 30)
    text(1, "경영진 의사결정과 90일 실행계획", "subtitle")
    gap(1, 60)
    text(1, "2026. 10. 6.", "date")
    gap(1, 60)
    text(1, "경영진 검토용", "department")
    text(1, "공개자료 기반 · 사실과 제안 구분", "covernote")

    gap(2, 24)
    text(2, "목  차", "toc_title")
    gap(2, 30)
    for line in ["1. 핵심 요약과 의사결정 ············ 3", "2. 공개 사고 사례 비교 ············· 4", "3. 공격 경로와 피해 확산 ············ 5", "4. 우선 통제와 확인 증거 ············ 6", "5. 90일 실행계획 ·················· 7", "6. 사고 대응과 고객 보호 ············ 8", "붙임. 출처와 작성 범위 ·············· 9"]:
        text(2, line, "toc")
        gap(2, 10)
    gap(2, 18)
    text(2, "표 1~5: 의사결정·사례·통제·계획·사고 대응", "note")
    text(2, "그림 1~2: 위험 전이 경로·90일 추진 순서", "note")

    text(3, "1. 핵심 요약과 의사결정", "h1")
    text(3, "가. 경영진이 알아야 할 사실", "h2")
    text(3, "공개 사고는 고객정보 보호뿐 아니라 업무 연속성과 외부 결제망 관리가 함께 필요함을 보여 준다. 롯데카드 정보유출, SGI서울보증 전산 중단, 코아메소드 결제정보 유출은 피해 경로와 필요한 통제가 서로 다르다.[1~3]")
    text(3, "나. 이번 보고서의 판단", "h2")
    text(3, "사고 건수의 증가율을 단정하기보다 우리 조직의 노출 자산, 핵심 외부 업체, 복구 가능성을 증거로 확인해야 한다. 아래 우선순위와 일정은 공개 사례를 바탕으로 작성한 실행 제안이다.")
    table(3, "<표 1> 이번 회의에서 결정할 사항", [22, 45, 33], [
        ["의사결정", "승인할 실행 범위", "첫 확인 증거"],
        ["노출 축소", "외부 공개 자산·계정 전수 점검과 긴급 패치 담당 지정", "자산 목록과 예외 승인대장"],
        ["복구 검증", "핵심 업무 1개를 선정해 격리 환경 복구 연습", "복구 시간·무결성 확인 기록"],
        ["공급망 점검", "중요 결제·수탁 업체 목록과 사고 통지·중단 기준 확인", "업체별 책임자와 대체 절차"],
    ])
    text(3, "※ 예산 금액과 인력 규모는 내부 현황 확인 후 산정한다. 특정 제품 구매나 일괄 교체를 전제로 하지 않는다.", "note")

    text(4, "2. 공개 사고 사례 비교", "h1")
    text(4, "가. 확인된 사실", "h2")
    table(4, "<표 2> 사고 유형별 사실과 시사점", [21, 45, 34], [
        ["사례", "공개자료에서 확인된 사실", "관리 시사점(분석)"],
        ["롯데카드\n정보유출", "금융위는 2025년 8월 사고의 297만 명 개인신용정보 유출과 보안 관리 미흡을 발표했다. 2026. 7. 31. 과징금 50억 원·일부 영업정지 1.5개월을 의결했다.[1]", "패치·암호화·악성코드 통제를 실제 운영 증거로 확인"],
        ["SGI서울보증\n업무 중단", "2025. 7. 14. 랜섬웨어 공격 후, 금융위는 7. 21. 서버 복구와 대고객 업무 정상화를 확인했다고 밝혔다.[2]", "백업 존재 여부와 서비스 복구 가능성을 별도 검증"],
        ["코아메소드\n결제정보 유출", "회사는 2026. 9. 9. 공지에서 카드번호 유출과 일부 건의 추가 정보 유출을 알렸다. 카드사 통지·이상거래 모니터링을 설명했다.[3]", "금융사 밖 결제 처리 구간까지 계약·탐지·통지 연결"],
    ])
    text(4, "나. 해석할 때 주의할 점", "h2")
    text(4, "롯데카드의 영업정지는 신규 회원 모집 등 일부 업무에 관한 조치로, 전체 서비스 중단과 구분한다. 코아메소드 내용은 회사 공지이며, 피해 규모와 공격 기법을 이 자료만으로 확정하지 않는다.", "note")
    text(4, "자료: 금융위원회 보도자료[1, 2], 코아메소드 공지[3].", "note")

    text(5, "3. 공격 경로와 피해 확산", "h1")
    text(5, "가. 여러 사고를 함께 볼 때의 위험 구조", "h2")
    diagram(5, "risk-flow", "<그림 1> 진입 지점에서 고객 피해까지의 위험 전이", [
        "진입 지점", "취약한 외부 자산", "탈취 계정·외부 업체", "내부 확산", "권한 확대·횡적 이동", "중요 데이터·업무 접근", "피해 발생", "정보 유출·업무 중단", "고객 피해·대응 비용", "노출 축소", "권한 분리·탐지", "격리·복구·고객 보호",
    ])
    text(5, "※ 특정 사고의 공격 순서를 재현한 그림이 아니라, 통제 위치를 설명하기 위한 개념도다. 실제 원인은 개별 조사 결과로 확인해야 한다.", "note")
    text(5, "나. 통제는 경로 전체에 배치", "h2")
    text(5, "외부 노출을 줄여도 계정이나 공급업체 경로는 남을 수 있다. 관리자 다중인증, 최소 권한, 중요 구간 분리, 이상 징후 탐지와 복구 연습을 이어서 점검한다.")
    text(5, "다. 외부 업체도 동일한 업무 흐름에서 점검", "h2")
    text(5, "중요 업체를 식별하고 계약·운영·종료 단계마다 책임을 확인한다. FSB와 NIST 자료는 이러한 수명주기 관리를 위한 참고 틀이며 국내의 개별 법적 의무를 대신하지 않는다.[4, 5]")

    text(6, "4. 우선 통제와 확인 증거", "h1")
    text(6, "가. 문서가 아닌 운영 결과로 확인", "h2")
    table(6, "<표 3> 우선 통제의 담당과 완료 증거(제안)", [21, 42, 22, 15], [
        ["통제", "확인할 증거", "주관", "시점"],
        ["자산·패치", "외부 자산 목록, 패치 기한, 미적용 예외와 만료일", "IT 운영", "30일"],
        ["계정·권한", "관리자 다중인증, 불필요 계정 회수, 접근 로그 표본", "보안·IAM", "30일"],
        ["탐지·대응", "주요 탐지 시나리오, 경보 처리 시간, 격리 연습 기록", "보안 관제", "60일"],
        ["백업·복구", "격리 백업의 복원 결과, 업무별 목표 대비 실제 복구 시간", "IT·업무", "60일"],
        ["외부 업체", "중요 업체 목록, 사고 통지 경로, 재위탁·종료 절차", "구매·보안", "60일"],
        ["고객 보호", "문의·통지 초안, 피해 접수 절차, 유관 부서 합동 연습", "고객·준법", "90일"],
    ])
    text(6, "나. 예외를 남기되 만료일을 지정", "h2")
    text(6, "업무 영향 때문에 즉시 조치하지 못한 항목은 책임자, 임시 통제, 완료 기한을 함께 기록한다. 예외가 반복 연장되면 경영진 검토 대상으로 올린다.")
    text(6, "※ 위 담당·기한은 제안이며 실제 조직명, 현행 규정과 시스템 제약을 확인해 확정한다.", "note")

    text(7, "5. 90일 실행계획", "h1")
    diagram(7, "roadmap", "<그림 2> 점검에서 검증·정착으로 이어지는 추진 순서", [
        "1~30일", "현황·긴급 조치", "31~60일", "연습·보완", "61~90일", "검증·정착", "책임자와 기준 확정", "복구·탐지 증거 확보", "잔여 위험 의사결정",
    ])
    table(7, "<표 4> 단계별 산출물과 완료 조건(제안)", [18, 42, 40], [
        ["단계", "핵심 산출물", "완료 조건"],
        ["30일", "자산·중요 업체 목록, 긴급 조치와 예외대장", "누락 항목 및 미조치 위험에 책임자·기한 지정"],
        ["60일", "핵심 업무 복구·탐지·업체 연락 연습 기록", "실패 구간을 재현하고 보완 조치의 재검증 일정 확정"],
        ["90일", "통합 모의훈련, 잔여 위험 보고, 정기 점검 계획", "업무 책임자가 복구 결과를 확인하고 경영진이 잔여 위험 판단"],
    ])
    text(7, "운영 지표는 자산 점검률, 기한 초과 취약점 수, 복구 목표 달성 여부, 업체 연락 성공 여부로 시작한다. 목표값은 30일 현황 점검 뒤 정하며, 검증 전 수치를 성과로 보고하지 않는다.", "note")

    text(8, "6. 사고 대응과 고객 보호", "h1")
    text(8, "가. 초기 대응의 책임을 미리 연결", "h2")
    table(8, "<표 5> 사고 단계별 행동과 기록(제안)", [19, 44, 37], [
        ["단계", "우선 행동", "반드시 남길 기록"],
        ["인지·분류", "보안·업무·준법 책임자를 연결하고 영향 범위 확인", "최초 인지 시각, 확인 사실, 미확인 가설"],
        ["확산 차단", "업무 영향과 증거 보존을 고려해 계정·구간 격리", "조치 승인, 대상, 시간, 수집한 증거"],
        ["복구·검증", "안전한 복구본 확인 후 단계적으로 업무 재개", "무결성·기능 점검, 업무 책임자 확인"],
        ["통지·보호", "적용 법령·계약상 신고·통지 요건 검토와 고객 안내", "통지 대상·문안·시점, 문의·피해 접수"],
    ])
    text(8, "나. 고객 안내에서 지킬 원칙", "h2")
    text(8, "확인된 사실, 확인 중인 사항, 고객이 지금 할 수 있는 행동을 나눠 알린다. 조사 중인 피해 규모나 원인을 단정하지 않고, 문의 경로와 다음 안내 시점을 명확히 한다.")
    text(8, "다. 종료 판단", "h2")
    text(8, "서비스 재개만으로 사고를 종결하지 않는다. 잔여 침해 여부, 데이터 무결성, 고객 보호 조치와 재발 방지 항목을 함께 확인한다.")
    text(8, "※ 구체적인 법정 신고·통지 기한은 적용 법령과 사고 유형에 따라 준법 담당자가 확인한다.", "note")

    text(9, "붙임. 출처와 작성 범위", "attachment")
    text(9, "가. 공개자료", "h2")
    for value in [
        "[1] 금융위원회, 「롯데카드 개인정보 유출 사고 관련 제재」, 2026. 7. 31.\nhttps://www.fsc.go.kr/no010101/87455",
        "[2] 금융위원회, SGI서울보증 사이버 침해사고 후속조치 관련 보도자료, 2025. 7. 30.\nhttps://www.fsc.go.kr/no010101/85029",
        "[3] 코아메소드, 개인정보 유출 관련 안내, 2026. 9. 9.\nhttps://www.coam.co.kr/notice/view/927",
        "[4] FSB, 제3자 위험 관리·감독 도구집, 2023. 12. 4.\nhttps://www.fsb.org/2023/12/final-report-on-enhancing-third-party-risk-management-and-oversight-a-toolkit-for-financial-institutions-and-financial-authorities/",
        "[5] NIST, SP 800-161 Rev. 1 Update 1, 2024. 11. 1.\nhttps://www.nist.gov/publications/cybersecurity-supply-chain-risk-management-practices-systems-and-organizations",
    ]:
        text(9, value, "source")
    text(9, "나. 작성 범위와 한계", "h2")
    text(9, "작성 기준일: 2026. 10. 6. 공개된 공식 보도자료와 회사 공지의 확인 범위에서 작성했다. 이 보고서는 모든 사고를 집계한 통계나 개별 사고의 기술 조사 결과가 아니다.", "note")
    text(9, "표 1·3~5와 그림 1~2는 이번 보고서의 제안·분석이다. 내부 자산, 예산, 계약, 복구 능력은 제공받지 않았으므로 실행 전 현황을 확인해야 한다.", "note")
    text(9, "서식 기준: 사용자가 제공한 「문서작성 표준-게시용(최종)」. 표·그림은 본 보고서를 위해 새로 작성했다.", "note")
    return dict(title="금융권 해킹 대응 보고서", as_of="2026-10-06", items=items, pages=pages)


class Builder:
    def __init__(self, work):
        self.work = work
        self.profile, reference = load_standard()
        self.seed = zipfile.ZipFile(io.BytesIO(reference))
        self.header = E.fromstring(self.seed.read("Contents/header.xml"))
        self.source_sec = E.fromstring(self.seed.read("Contents/section0.xml"))
        self.chars = self.header.find(f".//{{{HH}}}charProperties")
        self.paras = self.header.find(f".//{{{HH}}}paraProperties")
        self.borders = self.header.find(f".//{{{HH}}}borderFills")
        self.formats = {}
        self.n = 10000
        formats = {
            "body": (1400, False, "JUSTIFY", 160, 700), "h1": (1600, True, "LEFT", 160, 1400),
            "h2": (1500, True, "LEFT", 160, 700), "cover": (2400, True, "CENTER", 160, 0),
            "subtitle": (1800, True, "CENTER", 160, 0), "date": (1800, True, "CENTER", 160, 0),
            "department": (2000, True, "CENTER", 160, 1000), "covernote": (1200, False, "CENTER", 130, 0),
            "toc_title": (2000, True, "CENTER", 160, 1000), "toc": (1600, True, "LEFT", 160, 0),
            "note": (1200, False, "JUSTIFY", 130, 650), "source": (1200, False, "LEFT", 130, 1000),
            "caption": (1400, False, "CENTER", 110, 500), "cell": (1200, False, "CENTER", 110, 0),
            "th": (1200, False, "CENTER", 110, 0), "diagram": (1200, False, "LEFT", 110, 0),
            "attachment": (2000, True, "LEFT", 160, 1400), "host": (100, False, "CENTER", 100, 0),
            "table_host": (100, False, "CENTER", 100, 1000),
        }
        for kind, values in formats.items():
            self.style(kind, *values)
        for element in self.header.iter():
            if element.get("shadeColor", "").lower() == "none": element.set("shadeColor", "#FFFFFFFF")
        self.cell_borders = {}
        for shade in (False, True):
            for position in ("first", "middle", "last"):
                base = copy.deepcopy(next(c for c in self.borders if c.get("id") == "2"))
                bid = str(max(int(c.get("id")) for c in self.borders) + 1)
                base.set("id", bid)
                for c in list(base):
                    name = c.tag.split("}")[-1]
                    if "Border" in name:
                        c.set("width", "0.1 mm"); c.set("color", "#000000"); c.set("type", "SOLID")
                        if name == "leftBorder" and position == "first" or name == "rightBorder" and position == "last": c.set("type", "NONE")
                    elif name == "fillBrush": base.remove(c)
                if shade:
                    brush = E.SubElement(base, f"{{{HC}}}fillBrush")
                    E.SubElement(brush, f"{{{HC}}}winBrush", {"faceColor": "#E5E5E5", "hatchColor": "#000000", "alpha": "0"})
                self.borders.append(base); self.cell_borders[shade, position] = bid
        self.borders.set("itemCnt", str(len(self.borders)))
        cover = copy.deepcopy(next(c for c in self.borders if c.get("id") == self.profile["cover_title_border_id"]))
        self.cover_border = str(max(int(c.get("id")) for c in self.borders) + 1)
        cover.set("id", self.cover_border)
        self.borders.append(cover); self.borders.set("itemCnt", str(len(self.borders)))

    def style(self, kind, height, headline, align, line, after):
        char = copy.deepcopy(next(c for c in self.chars if c.get("id") == ("78" if headline else "11")))
        cid = str(max(int(c.get("id")) for c in self.chars) + 1)
        char.attrib.update(id=cid, height=str(height), textColor="#000000", shadeColor="#FFFFFFFF", borderFillIDRef="1")
        for c in char:
            name = c.tag.split("}")[-1]
            if name in ("ratio", "spacing", "relSz", "offset"):
                for k in c.attrib: c.set(k, "100" if name in ("ratio", "relSz") else "0")
            if name == "underline": c.set("type", "BOTTOM" if kind in ("caption", "attachment") else "NONE")
        if kind in ("caption", "attachment") and char.find(f"{{{HH}}}underline") is None:
            E.SubElement(char, f"{{{HH}}}underline", dict(type="BOTTOM", shape="SOLID", color="#000000"))
        para = copy.deepcopy(next(p for p in self.paras if p.get("id") == "2"))
        pid = str(max(int(p.get("id")) for p in self.paras) + 1)
        para.set("id", pid)
        for c in para.iter():
            name = c.tag.split("}")[-1]
            if name == "align": c.set("horizontal", align)
            if name in ("intent", "left", "right", "prev", "next") and "value" in c.attrib: c.set("value", str(after) if name == "next" else "0")
            if name == "lineSpacing": c.attrib.update(type="PERCENT", value=str(line), unit="HWPUNIT")
            if name == "breakSetting": c.attrib.update(breakLatinWord="BREAK_WORD", breakNonLatinWord="KEEP_WORD", keepWithNext="0", keepLines="0", widowOrphan="0")
        left = {"body": self.profile["body_indent_hwpunit"], "h2": self.profile["subheading_indent_hwpunit"],
                "note": self.profile["note_indent_hwpunit"], "source": self.profile["note_indent_hwpunit"],
                "toc": self.profile["toc_indent_hwpunit"]}.get(kind, 0)
        set_margins(para, left=left, after=after)
        self.chars.append(char); self.paras.append(para)
        self.chars.set("itemCnt", str(len(self.chars))); self.paras.set("itemCnt", str(len(self.paras)))
        self.formats[kind] = (cid, pid)

    def paragraph(self, kind="body", text=""):
        self.n += 1
        cid, pid = self.formats[kind]
        p = E.Element(tag("p"), {"id": str(self.n), "paraPrIDRef": pid, "styleIDRef": "0", "pageBreak": "0", "columnBreak": "0", "merged": "0"})
        r = E.SubElement(p, tag("run"), {"charPrIDRef": cid})
        E.SubElement(r, tag("t")).text = text
        return p

    def section(self):
        sec = E.Element(f"{{{HS}}}sec")
        p = self.paragraph("host")
        r = p.find(tag("run"))
        source = self.source_sec.find(f".//{tag('secPr')}")
        properties = copy.deepcopy(source)
        for c in list(properties):
            if c.tag.split("}")[-1] in ("parameterset", "presentation"): properties.remove(c)
        r.insert(0, properties)
        ctrl = E.SubElement(r, tag("ctrl"))
        E.SubElement(ctrl, tag("colPr"), {"id": "0", "type": "NEWSPAPER", "layout": "LEFT", "colCount": "1", "sameSz": "1", "sameGap": "0"})
        ctrl = E.SubElement(r, tag("ctrl"))
        E.SubElement(ctrl, tag("pageNum"), {"pos": "BOTTOM_CENTER", "formatType": "DIGIT", "sideChar": "-"})
        sec.append(p)
        return sec

    def write(self, path, sec, images=None):
        hpf = E.fromstring(self.seed.read("Contents/content.hpf"))
        metadata = hpf.find(f"{{{OPF}}}metadata")
        for child in list(metadata): metadata.remove(child)
        E.SubElement(metadata, f"{{{OPF}}}title").text = "금융권 해킹 대응 보고서"
        E.SubElement(metadata, f"{{{OPF}}}language").text = "ko"
        manifest = hpf.find(f"{{{OPF}}}manifest")
        for i, name in enumerate(images or []):
            E.SubElement(manifest, f"{{{OPF}}}item", {"id": f"image{i+1}", "href": f"BinData/{name}.png", "media-type": "image/png", "isEmbeded": "1"})
        replacements = {"Contents/header.xml": xml(self.header), "Contents/section0.xml": xml(sec), "Contents/content.hpf": xml(hpf), "Preview/PrvText.txt": "".join(sec.itertext()).encode("utf-8")}
        with zipfile.ZipFile(path, "w") as out:
            for entry in self.seed.infolist():
                out.writestr(copy.copy(entry), replacements.get(entry.filename, self.seed.read(entry)))
            for name in images or []: out.writestr(f"BinData/{name}.png", (self.work / f"{name}.png").read_bytes())

    def table(self, node, approved):
        rows, widths = node["rows"], node["widths"]
        widths = [round(48190 * w / sum(widths)) for w in widths]
        widths[-1] = 48190 - sum(widths[:-1])
        p = self.paragraph("table_host")
        r = p.find(tag("run"))
        self.n += 1
        tbl = E.SubElement(r, tag("tbl"), {"id": str(self.n), "zOrder": "0", "numberingType": "TABLE", "textWrap": "TOP_AND_BOTTOM", "textFlow": "BOTH_SIDES", "lock": "0", "dropcapstyle": "None", "pageBreak": "CELL", "repeatHeader": "1", "rowCnt": str(len(rows)), "colCnt": str(len(widths)), "cellSpacing": "0", "borderFillIDRef": "1", "noAdjust": "0"})
        E.SubElement(tbl, tag("sz"), {"width": "48190", "height": str(2500 * len(rows)), "widthRelTo": "ABSOLUTE", "heightRelTo": "ABSOLUTE", "protect": "0"})
        E.SubElement(tbl, tag("pos"), {"treatAsChar": "1", "affectLSpacing": "0", "flowWithText": "1", "allowOverlap": "0", "holdAnchorAndSO": "0", "vertRelTo": "PARA", "horzRelTo": "PARA", "vertAlign": "TOP", "horzAlign": "LEFT", "vertOffset": "0", "horzOffset": "0"})
        E.SubElement(tbl, tag("outMargin"), dict(left="0", right="0", top="0", bottom="700"))
        E.SubElement(tbl, tag("inMargin"), dict(left="510", right="510", top="141", bottom="141"))
        total_height = 0
        for y, row in enumerate(rows):
            tr = E.SubElement(tbl, tag("tr"))
            # Allocate generous minimum heights; rhwp still computes actual wraps.
            row_height = max(2600, max(math.ceil(sum(1200 if ord(ch) > 255 else 650 for ch in ''.join(approved[key].itertext())) / (widths[x] - 1020)) * 1320 + 850 for x, key in enumerate(row)))
            total_height += row_height
            for x, key in enumerate(row):
                bid = self.cell_borders[y == 0, "first" if x == 0 else "last" if x == len(row) - 1 else "middle"]
                tc = E.SubElement(tr, tag("tc"), {"name": "", "header": "1" if y == 0 else "0", "hasMargin": "1", "protect": "0", "editable": "0", "dirty": "0", "borderFillIDRef": bid})
                sl = E.SubElement(tc, tag("subList"), {"id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": "CENTER", "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0", "textHeight": "0", "hasTextRef": "0", "hasNumRef": "0"})
                sl.append(self.restyled(approved[key], "th" if y == 0 else "cell"))
                E.SubElement(tc, tag("cellAddr"), {"colAddr": str(x), "rowAddr": str(y)})
                E.SubElement(tc, tag("cellSpan"), dict(colSpan="1", rowSpan="1"))
                E.SubElement(tc, tag("cellSz"), {"width": str(widths[x]), "height": str(row_height)})
                E.SubElement(tc, tag("cellMargin"), dict(left="510", right="510", top="141", bottom="141"))
        tbl.find(tag("sz")).set("height", str(total_height))
        return p

    def restyled(self, original, kind):
        p = copy.deepcopy(original)
        self.n += 1
        p.attrib.update(id=str(self.n), paraPrIDRef=self.formats[kind][1], pageBreak="0")
        for c in list(p):
            if c.tag == tag("linesegarray"): p.remove(c)
        for r in p.findall(tag("run")): r.set("charPrIDRef", self.formats[kind][0])
        return p

    def picture(self, name, index):
        pic = E.parse(self.work / "pic-prototype.xml").getroot()
        self.n += 1
        pic.attrib.update(id=str(self.n), zOrder="0", textWrap="TOP_AND_BOTTOM")
        width, height = 48190, 18312 if name == "risk-flow" else 12665
        for c in pic.iter():
            local = c.tag.split("}")[-1]
            if local in ("orgSz", "curSz"): c.attrib.update(width=str(width), height=str(height))
            if local == "sz": c.attrib.update(width=str(width), height=str(height), widthRelTo="ABSOLUTE", heightRelTo="ABSOLUTE")
            if local == "pos": c.attrib.update(treatAsChar="1", affectLSpacing="0", flowWithText="1", allowOverlap="0", vertRelTo="PARA", horzRelTo="PARA", vertAlign="TOP", horzAlign="LEFT", vertOffset="0", horzOffset="0")
            if local in ("outMargin", "imgClip"):
                if local == "outMargin": c.attrib.update(left="0",right="0",top="0",bottom="500")
            if local == "img": c.set("binaryItemIDRef", f"image{index}")
            if local == "pt1": c.set("x", str(width)); c.set("y", "0")
            if local == "pt2": c.set("x", str(width)); c.set("y", str(height))
            if local == "pt3": c.set("x", "0"); c.set("y", str(height))
            if local in ("imgClip", "imgDim"):
                pixels = struct.unpack(">II", (self.work / f"{name}.png").read_bytes()[16:24])
                if local == "imgClip": c.attrib.update(left="0", right=str(pixels[0] * 75), top="0", bottom=str(pixels[1] * 75))
                else: c.attrib.update(dimwidth=str(pixels[0] * 75), dimheight=str(pixels[1] * 75))
        p = self.paragraph("host"); p.find(tag("run")).append(pic)
        return p


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "assemble"])
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(); work = args.work
    builder = Builder(work)
    if args.phase == "prepare":
        spec = specification()
        (work / "report-plan.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        for batch, start in enumerate(range(0, len(spec["items"]), 20)):
            sec = builder.section()
            for i, item in enumerate(spec["items"][start:start + 20], 1):
                item.update(batch=batch, paragraph=i, before=f"STANDARD_SLOT_{item['id']}")
                p = builder.paragraph("body", item["before"]); p.set("pageBreak", "1")
                sec.append(p)
            builder.write(work / f"scaffold-{batch}.hwpx", sec)
        (work / "report-plan.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(items=len(spec["items"]), batches=batch + 1)))
    else:
        spec = json.loads((work / "report-plan.json").read_text(encoding="utf-8")); approved = {}
        for batch in sorted({i["batch"] for i in spec["items"]}):
            with zipfile.ZipFile(work / f"applied-{batch}.hwpx") as z:
                section = E.fromstring(z.read("Contents/section0.xml"))
                for item in (i for i in spec["items"] if i["batch"] == batch):
                    p = list(section)[item["paragraph"]]
                    actual = "".join(t.text or "" for t in p.iter(tag("t")))
                    # HWPX encodes line breaks as sibling lineBreak elements.
                    expected = item["text"].replace("\n", "")
                    if actual.replace("\n", "") != expected: raise ValueError((item["id"], actual, expected))
                    approved[item["id"]] = p
        sec = builder.section(); images = []
        for page, blocks in enumerate(spec["pages"]):
            start = len(sec)
            for block in blocks:
                kind = block["type"]
                if kind == "text":
                    key = block["id"]; item = next(i for i in spec["items"] if i["id"] == key)
                    p = builder.restyled(approved[key], item["kind"])
                    if item["kind"] == "cover":
                        p = builder.table(dict(rows=[[key]], widths=[100]), approved)
                        tbl = p.find(f".//{tag('tbl')}")
                        tbl.find(tag("sz")).set("height", "6520")
                        tc = tbl.find(f".//{tag('tc')}")
                        tc.set("borderFillIDRef", builder.cover_border)
                        tc.find(tag("cellSz")).set("height", "6520")
                        sl = tc.find(tag("subList")); sl.remove(list(sl)[0]); sl.append(builder.restyled(approved[key], "cover"))
                elif kind == "table": p = builder.table(block, approved)
                elif kind == "image":
                    images.append(block["name"]); p = builder.picture(block["name"], len(images))
                else:
                    name = f"gap-{block['points']}"
                    if name not in builder.formats: builder.style(name, block["points"] * 100, False, "CENTER", 100, 0)
                    p = builder.paragraph(name)
                if page and len(sec) == start: p.set("pageBreak", "1")
                sec.append(p)
        builder.write(work / "financial-cyber-standard.hwpx", sec, images)
        print(json.dumps(dict(pages=9, tables=5, images=2, source_sha256=hashlib.sha256((work / 'template.hwpx').read_bytes()).hexdigest())))

if __name__ == "__main__": run()
