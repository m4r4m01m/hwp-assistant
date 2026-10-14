"""Bounded report drafts. A draft is never a saved HWPX document."""
import json
import re
import math
from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .hwpx import digest
from .document_standard import role


class ReportValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def unicode_json(cls, value):
        try:
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(raw) > 5000000:
                raise ValueError("보고서 크기 초과")
        except (UnicodeError, TypeError) as exc:
            raise ValueError("유효한 UTF-8 보고서가 필요합니다.") from exc
        return value


def paragraph(value):
    if not value.strip() or any(ord(c) < 32 for c in value) or any(c in value for c in "\u2028\u2029"):
        raise ValueError("제목·본문은 줄바꿈 없는 한 문단이어야 합니다.")
    return value


class ReportTable(ReportValue):
    caption: str = Field(min_length=1, max_length=90)
    columns: list[str] = Field(min_length=2, max_length=4)
    rows: list[list[str]] = Field(min_length=1, max_length=5)

    _caption = field_validator("caption")(paragraph)

    @model_validator(mode="after")
    def cells(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("표의 열 수가 일치해야 합니다.")
        for cell in self.columns + [c for row in self.rows for c in row]:
            paragraph(cell)
            if len(cell) > 52: raise ValueError("표 셀은 52자 이내여야 합니다.")
        if any(len(c) > 20 for c in self.columns): raise ValueError("표 머리글은 20자 이내여야 합니다.")
        return self


class ReportDiagram(ReportValue):
    caption: str = Field(min_length=1, max_length=90)
    steps: list[str] = Field(min_length=2, max_length=4)

    _caption = field_validator("caption")(paragraph)

    @field_validator("steps")
    @classmethod
    def nodes(cls, values):
        for value in values:
            paragraph(value)
            if len(value) > 48: raise ValueError("흐름도 단계는 48자 이내여야 합니다.")
        return values


class ReportEmphasis(ReportValue):
    text: str = Field(min_length=2, max_length=30)
    style: Literal["bold", "blue", "bold_blue"]

    _text = field_validator("text")(paragraph)


class ReportSection(ReportValue):
    heading: str = Field(min_length=1, max_length=120)
    paragraphs: list[str] = Field(max_length=9)
    tables: list[ReportTable] = Field(default_factory=list, max_length=1)
    diagrams: list[ReportDiagram] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def content(self):
        if not (self.paragraphs or self.tables or self.diagrams):
            raise ValueError("본문·표·흐름도 중 하나가 필요합니다.")
        return self

    @field_validator("heading")
    @classmethod
    def plain_heading(cls, value):
        return paragraph(re.sub(r"^\s*\d+[.)]\s*", "", value))

    @field_validator("paragraphs")
    @classmethod
    def bounded_paragraphs(cls, values):
        if sum(len(v) for v in values) > 1500 or any(len(v) > 800 for v in values):
            raise ValueError("각 본문은 800자, 한 절은 1500자 이내여야 합니다.")
        return [paragraph(v) for v in values]


class ReportSource(ReportValue):
    id: str = Field(pattern=r"^S[1-9][0-9]?$")
    title: str = Field(min_length=1, max_length=180)
    url: str = Field(min_length=1, max_length=600)
    published: str = Field(max_length=40)

    _title = field_validator("title")(paragraph)

    @field_validator("published")
    @classmethod
    def clean_date(cls, value):
        return paragraph(value) if value else value

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in value):
            raise ValueError("출처는 인증정보 없는 HTTP(S) 주소여야 합니다.")
        return value


class ReportDraft(ReportValue):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=700)
    sections: list[ReportSection] = Field(min_length=3, max_length=6)
    sources: list[ReportSource] = Field(max_length=10)
    limitations: list[str] = Field(max_length=6)
    emphasis: list[ReportEmphasis] = Field(default_factory=list, max_length=12)

    _text = field_validator("title", "summary")(paragraph)

    @classmethod
    def generation_schema(cls):
        # Stored drafts may omit rich fields. Fresh Codex structured output must
        # explicitly return empty arrays: strict schemas require every property.
        schema = cls.model_json_schema()
        def strict(node):
            if isinstance(node, dict):
                if "properties" in node: node["required"] = list(node["properties"])
                for child in node.values(): strict(child)
            elif isinstance(node, list):
                for child in node: strict(child)
        strict(schema)
        return schema

    @model_validator(mode="after")
    def references(self):
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("출처 ID 중복")
        if sum(len(s.tables) for s in self.sections) > 2 or sum(len(s.diagrams) for s in self.sections) > 2:
            raise ValueError("보고서에는 표와 흐름도를 각각 2개까지 사용할 수 있습니다.")
        values = [self.summary] + [p for s in self.sections for p in s.paragraphs]
        for s in self.sections:
            for t in s.tables: values.extend([t.caption] + t.columns + [c for row in t.rows for c in row])
            for d in s.diagrams: values.extend([d.caption] + d.steps)
        text = " ".join(values)
        if set(re.findall(r"\[(S\d+)\]", text)) - set(ids):
            raise ValueError("본문에 없는 출처 ID를 참조했습니다.")
        # Keep citations readable in plain HWPX paragraphs; full links remain in sources.
        urls = {s.id: s.url for s in self.sources}
        def citations(value):
            def link(match):
                if urls.get(match[1]) != match[2]:
                    raise ValueError("본문 출처 링크와 출처 목록의 주소가 다릅니다.")
                return f"[{match[1]}]"
            return re.sub(r"\[(S\d+)\]\((https?://[^\s)]+)\)", link, value)
        self.summary = citations(self.summary)
        for section in self.sections:
            section.paragraphs = [citations(p) for p in section.paragraphs]
            for table in section.tables:
                table.caption = citations(table.caption)
                table.columns = [citations(c) for c in table.columns]
                table.rows = [[citations(c) for c in row] for row in table.rows]
            for diagram in section.diagrams:
                diagram.caption = citations(diagram.caption)
                diagram.steps = [citations(s) for s in diagram.steps]
        if len({e.text for e in self.emphasis}) != len(self.emphasis):
            raise ValueError("강조 문구가 중복되었습니다.")
        for e in self.emphasis:
            if not any(e.text in citations(v) for v in values):
                raise ValueError("강조 문구는 실제 본문·표·흐름도의 일부여야 합니다.")
        for value in self.limitations:
            paragraph(value)
            if len(value) > 400:
                raise ValueError("확인 사항은 각각 400자 이내여야 합니다.")
        return self


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def report_hash(value):
    return digest(canonical(value).encode("utf-8"))


def report_blocks(draft, *, concise=False, rich=False):
    """Budget pages before fenced application, retaining bullet continuation styles."""
    draft = ReportDraft.model_validate(draft)
    blocks = [(draft.title, "title", False, {}), ("핵심 요약", "heading", False, {}), (draft.summary, "body", False, {})]
    table_number = diagram_number = 0
    for i, section in enumerate(draft.sections, 1):
        blocks.append((f"{i}. {section.heading}", "heading", True, {}))
        blocks.extend((p, "body", False, {}) for p in section.paragraphs)
        for visual in [*section.tables, *section.diagrams]:
            is_table = isinstance(visual, ReportTable)
            if is_table: table_number += 1
            else: diagram_number += 1
            number = table_number if is_table else diagram_number
            kind = "table" if is_table else "diagram"
            # All cell/label/connector text is staged through the same fenced commands.
            blocks.append((f"{'표' if is_table else '그림'} {number}. {visual.caption}", "source", False,
                           {"visual_caption": kind} if rich else {}))
            rows = [visual.columns, *visual.rows] if is_table else [[part for j, step in enumerate(visual.steps) for part in (["→", step] if j else [step])]]
            for y, row in enumerate(rows):
                for x, cell in enumerate(row):
                    blocks.append((cell, "source", False, {"visual_id": f"{kind}-{number}", "visual_kind": kind,
                        "visual_row": y, "visual_column": x, "visual_columns": len(row)} if rich else {}))
    if draft.sources or draft.limitations:
        blocks.append(("출처 및 확인 사항", "heading", True, {}))
        page_chars = 0
        for s in draft.sources:
            value = f"[{s.id}] {s.title} ({s.published or '발행일 미확인'}) · {s.url}"
            new_page = page_chars + len(value) > 1100
            page_chars = len(value) if new_page else page_chars + len(value)
            blocks.append((value, "source", new_page, {}))
        for p in draft.limitations:
            value = "확인 사항: " + p
            new_page = page_chars + len(value) > 1100
            page_chars = len(value) if new_page else page_chars + len(value)
            blocks.append((value, "body", new_page, {}))
    if concise:
        from .report_outline import split_bullet
        blocks = [(part, kind, forced and i == 0, meta) for text, kind, forced, meta in blocks
                  for i, part in enumerate(split_bullet(text, [e.text for e in draft.emphasis] if rich else ()) if kind == "body" else [text])]
    result, used = [], 0
    for text, kind, forced, meta in blocks:
        style_kind = role(text, kind)
        # Preserve every character; split only at an existing space when possible.
        # Bounded chunks leave room for larger mandated fonts and indents.
        chunks = []
        remaining = text
        while kind == "body" and len(remaining) > 380:
            cut = remaining.rfind(" ", 250, 380)
            cut = cut + 1 if cut >= 250 else 380
            chunks.append(remaining[:cut]); remaining = remaining[cut:]
        chunks.append(remaining)
        for index, chunk in enumerate(chunks):
            weighted = sum(1 if ord(c) > 255 else .6 for c in chunk)
            width, line, gap = (33, 22.4, 14) if kind == "body" else (27, 35.2, 25) if kind == "title" else (29, 25.6, 14) if kind == "heading" else (39, 15.6, 7)
            if style_kind in ("square", "circle", "dash"):
                width, line, gap = 29, 24, 10
            cost = math.ceil(weighted / width) * line + gap
            new_page = (forced and index == 0) or (used > 0 and used + cost > 570)
            used = cost if new_page else used + cost
            result.append({"text": chunk, "kind": kind, "page_break": new_page, **meta})
            if rich:
                result[-1]["emphasis"] = [e.model_dump() for e in draft.emphasis if e.text in chunk]
            if style_kind in ("square", "circle", "dash"):
                result[-1]["style_kind"] = style_kind + ("_continuation" if index else "")
    return result
