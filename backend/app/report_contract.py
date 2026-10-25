from .standard_policy import cover_enabled, toc_mode
"""Bounded report drafts. A draft is never a saved HWPX document."""
import json
import re
import math
from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .hwpx import digest
from .document_standard import role

PARENTHETICAL_SCOPES = (
    ("square", "주요 항목 □"), ("circle", "하위 설명 ㅇ"), ("dash", "세부 설명 -"),
    ("dot", "보충 항목 ․"), ("body", "일반 문단"), ("summary", "핵심 요약"),
)
PARENTHETICAL_LABELS = "요약|배경|차이점|조치|제안|현황|목적|쟁점|방안|일정|결론|핵심"


def validate_parenthetical_scope(value):
    if not isinstance(value, str):
        raise ValueError("괄호 요약어 범위는 정해진 문자열이어야 합니다.")
    selected = value.split(",") if value else []
    canonical_scope = ",".join(key for key, _ in PARENTHETICAL_SCOPES if key in selected)
    if canonical_scope != value:
        raise ValueError("괄호 요약어 범위는 square,circle,dash,dot,body,summary 순서로 중복 없이 선택하세요.")
    return value


def parenthetical_role(value):
    """Canonical prose role before selected bullet/number glyphs are applied."""
    # The bounded lead-label grammar also accepts a marker directly before '('.
    # Classify that existing spelling without inserting spaces or rewriting it.
    match = re.match(r"^\s*([□ㅇ○\-․])(?=\s|\()", value)
    if match:
        return {"□": "square", "ㅇ": "circle", "○": "circle", "-": "dash", "․": "dot"}[match[1]]
    if re.match(r"^\s*(?:[가-하][.)]|\(\d+\)|\d+\)|\([가-하]\)|[①-⑳㉮-㉻]|\*+|주\d+\))\s+", value):
        return None  # Explicit headings and notes are not body label targets.
    return "body"


def parenthetical_enabled(settings, scope):
    if settings.get("body_parenthetical_labels") is False:
        return False
    if scope in ("square", "circle", "dash", "dot") and settings.get("enabled_body_roles") is not None and scope not in settings["enabled_body_roles"].split(","):
        return False
    selected = settings.get("body_parenthetical_scope")
    return selected is None or scope in selected.split(",")


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


class ReportResearchWarning(ReportValue):
    code: Literal["research_incomplete", "sources_unverified", "research_not_requested", "inherited_research_unverified"]
    message: str = Field(min_length=1, max_length=400)

    _message = field_validator("message")(paragraph)


class ReportResearchNotice(ReportValue):
    """Server-owned provenance frozen into a new draft, never generated by AI."""
    version: Literal["research-notice-v1"]
    policy: Literal["require_completed", "allow_unverified"]
    requested: bool
    status: Literal["not_requested", "completed", "unverified"]
    completed_calls: int = Field(ge=0, le=100000)
    started_calls: int = Field(ge=0, le=100000)
    incomplete_calls: int = Field(ge=0, le=100000)
    source_verification: Literal["unverified"]
    warnings: list[ReportResearchWarning] = Field(max_length=4)


class ReportDraft(ReportValue):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=700)
    sections: list[ReportSection] = Field(min_length=3, max_length=6)
    sources: list[ReportSource] = Field(max_length=10)
    limitations: list[str] = Field(max_length=6)
    emphasis: list[ReportEmphasis] = Field(default_factory=list, max_length=12)
    # Do not add even a null field to historical drafts or their hashes/plans.
    research_notice: ReportResearchNotice | None = Field(default=None, exclude_if=lambda value: value is None)

    _text = field_validator("title", "summary")(paragraph)

    @classmethod
    def generation_schema(cls):
        # Stored drafts may omit rich fields. Fresh Codex structured output must
        # explicitly return empty arrays: strict schemas require every property.
        schema = cls.model_json_schema()
        # Provenance is assigned from observed execution, never by the model.
        schema["properties"].pop("research_notice", None)
        for definition in ("ReportResearchNotice", "ReportResearchWarning"):
            schema.get("$defs", {}).pop(definition, None)
        def strict(node):
            if isinstance(node, dict):
                if "properties" in node: node["required"] = list(node["properties"])
                for child in node.values(): strict(child)
            elif isinstance(node, list):
                for child in node: strict(child)
        strict(schema)
        # These bounds already apply in validators. Advertise them to the
        # generator too, so a schema-valid rich report does not routinely fail
        # only after its last streamed section. Stored draft policy is unchanged.
        definitions = schema["$defs"]
        definitions["ReportTable"]["properties"]["columns"]["items"].update(minLength=1, maxLength=20)
        rows = definitions["ReportTable"]["properties"]["rows"]["items"]
        rows.update(minItems=2, maxItems=4)
        rows["items"].update(minLength=1, maxLength=52)
        definitions["ReportDiagram"]["properties"]["steps"]["items"].update(minLength=1, maxLength=48)
        definitions["ReportSection"]["properties"]["paragraphs"]["items"].update(minLength=1, maxLength=800)
        definitions["ReportSource"]["properties"]["title"]["description"] = (
            "Verified document or page title only; do not append its URL or publication date."
        )
        definitions["ReportSource"]["properties"]["published"]["description"] = (
            "One complete, source-supported publication date only; use an empty string if unknown. "
            "Do not include update-history prose, an inferred day or a truncated date."
        )
        schema["properties"]["limitations"]["items"].update(minLength=1, maxLength=400)
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


def citation_marker(identifier, style):
    """Presentation only: canonical source IDs in the stored draft never change."""
    number = identifier[1:]
    if style == "STARS":
        return "*" * int(number)
    if style == "NOTE_SHORT":
        return f"주{number})" if number == "1" else f"{number})"
    return f"주{number})" if style == "NOTE" else f"*{number}" if style == "ASTERISK" else f"[{identifier}]"


def citation_style_for_scope(style, source_count):
    """AUTO is a new explicit setting; legacy fixed marker styles keep their meaning."""
    return ("STARS" if source_count <= 1 else "NOTE") if style == "AUTO" else style


def limitation_source_ranges(value, source_ids):
    """Resolve an explicit, fully known source range in a new plan only.

    Require a nearby source label: ranges and single bare tokens may also name
    products. Unknown, descending or incomplete ranges remain original text.
    """
    def expand(match):
        if not re.search(r"(?:^|[^\w])(?:출처|자료|문서|근거)(?:\s*(?:번호|목록))?\s*[:：]?\s*$", value[:match.start()]):
            return match[0]
        first, last = int(match[1]), int(match[2])
        identifiers = [f"S{number}" for number in range(first, last + 1)]
        if not identifiers or not set(identifiers).issubset(source_ids):
            return match[0]
        return "".join(f"[{identifier}]" for identifier in identifiers)
    return re.sub(r"(?<![A-Za-z0-9_\[/])S([1-9]\d?)\s*[~～–]\s*S([1-9]\d?)(?![A-Za-z0-9_\]/])", expand, value)


def presentation_text(value, settings, source_ids, *, body=False, marks=None, marker_numbers=None, scope=None):
    scoped = "body_parenthetical_scope" in settings
    target_role = scope or parenthetical_role(value)
    hide_label = body and (not parenthetical_enabled(settings, target_role) if scoped and target_role else settings.get("body_parenthetical_labels") is False)
    if hide_label:
        # Only the bounded lead-label grammar used by the existing emphasis
        # contract is eligible. Conditions and parentheses later in prose stay.
        label = PARENTHETICAL_LABELS if scoped or settings.get("preset_id") else "[^()]{1,24}"
        value = re.sub(r"^(\s*(?:[□ㅇ○․-]\s*)?)\((" + label + r")\)(\s*)",
                       lambda match: match[1] if match[3] else match[0], value, count=1)
    style = settings.get("citation_style", "BRACKET_S")
    if style == "AUTO":
        count = len(marker_numbers) if marker_numbers is not None else len({identifier for identifier in re.findall(r"\[(S\d+)\]", value) if identifier in source_ids})
        style = citation_style_for_scope(style, count)
    if style == "NOTE_SHORT":
        # Consecutive canonical citations become one displayed list. Their
        # source identities remain unchanged and ranges use the final text.
        value = re.sub(r"\[(S\d+)\]\s+(?=\[S\d+\])", r"[\1]", value)
    offset = 0
    previous_end = None
    def replace(match):
        nonlocal offset, previous_end
        if match[1] not in source_ids:
            return match[0]
        identifier = f"S{marker_numbers[match[1]]}" if marker_numbers is not None else match[1]
        result = citation_marker(identifier, style)
        separator = ", " if marker_numbers is not None and previous_end == match.start() else ""
        if marks is not None:
            start = match.start() + offset + len(separator)
            marks.append((start, start + len(result)))
        previous_end = match.end()
        offset += len(separator) + len(result) - len(match[0])
        return separator + result
    return re.sub(r"\[(S\d+)\]", replace, value)


def report_blocks(draft, *, concise=False, rich=False, visual_settings=None, cover=None):
    """Budget pages before fenced application, retaining bullet continuation styles."""
    draft = ReportDraft.model_validate(draft)
    settings = visual_settings or {}
    reference = settings.get("preset_id") in ("appendix-1", "appendix-2", "appendix-3")
    source_ids = {source.id for source in draft.sources}
    rendered_marks = {}
    def present(value, *, body=False, scope=None):
        marks = []
        # New reference notes are numbered per prose section/visual below,
        # before the plan is frozen or any HWPX command is applied.
        display = {**settings, "citation_style": "BRACKET_S"} if reference else settings
        result = presentation_text(value, display, source_ids, body=body, marks=marks, scope=scope)
        if not reference and settings.get("citation_superscript") and marks:
            rendered_marks[result] = marks
        return result
    scoped_labels = "body_parenthetical_scope" in settings
    emphasis = [{**item.model_dump(), "text": present(item.text, body=not scoped_labels)} for item in draft.emphasis]
    blocks = [(draft.title, "title", False, {})]
    if reference:
        from .reference_report import BODY_SPACING_POLICY, TITLE_OVERFLOW_POLICY
        from .report_rich import TABLE_LAYOUT_POLICY
        # New plans opt in explicitly; a previously frozen plan/layout is never
        # restyled merely because the application code has been updated.
        blocks[0][3]["body_spacing"] = BODY_SPACING_POLICY
        blocks[0][3]["title_overflow"] = TITLE_OVERFLOW_POLICY
        blocks[0][3]["table_layout"] = TABLE_LAYOUT_POLICY
        # New plans explicitly own automatic wrapping. Replaying a frozen plan
        # without this marker retains its original manual line partition.
        blocks[0][3]["native_wrap"] = "reference-native-wrap-v1"
        if toc_mode(settings) != "NONE":
            from .reference_report import TOC_LAYOUT_POLICY
            blocks[0][3]["toc_layout"] = TOC_LAYOUT_POLICY
    if reference and not cover_enabled(settings):
        from .reference_report import TITLE_LAYOUT_POLICY
        blocks[0][3]["title_layout"] = TITLE_LAYOUT_POLICY
    if reference and cover_enabled(settings) and (cover is not None or "cover_mode" in settings or "toc_mode" in settings):
        cover = cover or {}
        from .reference_report import COVER_LAYOUT_POLICY
        # Presence is an explicit new layout contract. Absence preserves the
        # shape of older plans, including previews frozen before this feature.
        blocks[0][3]["cover_layout"] = COVER_LAYOUT_POLICY
        for field in ("date", "department"):
            if cover.get(field):
                blocks.append((paragraph(cover[field]), "body", False, {"style_kind": "cover_" + field}))
    if settings.get("show_core_summary", True):
        summary_heading = {}
        if reference and settings.get("enabled_heading_roles"):
            from .standard_policy import enabled_role
            summary_heading = {"style_kind": enabled_role("heading", settings)}
        blocks.extend([("핵심 요약", "heading", False, summary_heading),
                       (present(draft.summary, body=True, scope="summary"), "body", False, {"parenthetical_role": "summary"})])
    table_number = diagram_number = 0
    for i, section in enumerate(draft.sections, 1):
        if reference:
            from .reference_report import body_item, numbered_marker
            from .standard_policy import enabled_role, HEADING_ROLES
            heading_role = enabled_role("heading", settings)
            marker = numbered_marker(settings[f"numbered_{HEADING_ROLES.index(heading_role) + 1}_marker"], i)
            blocks.append((f"{marker} {section.heading}", "heading", True, {"style_kind": heading_role} if heading_role != "heading" else {}))
            counters = [0, 0, 0, 0]
            from .standard_policy import organize_paragraphs
            for prose in organize_paragraphs(section.paragraphs, settings):
                label_role = parenthetical_role(prose)
                if label_role:
                    label_role = enabled_role(label_role, settings)
                # Strip only eligible lead labels before replacing the canonical
                # bullet with a numbered marker; (1)/(가) are structural text.
                prose = presentation_text(prose, {**settings, "citation_style": "BRACKET_S"}, source_ids, body=True, scope=label_role)
                text, reference_role = body_item(prose, settings, counters, apply_numbered_markers=True)
                item_meta = {"style_kind": reference_role, **({"parenthetical_role": label_role} if label_role else {})}
                if label_role in ("square", "circle", "dash", "dot"):
                    item_meta["note_body_role"] = label_role
                # A numbered display of a square body item is still prose.
                # Only an explicit subordinate title joins the native TOC.
                if re.match(r"^[가-하]\.\s", prose):
                    item_meta["toc_level"] = 2
                blocks.append((present(text), "body", False, item_meta))
        else:
            blocks.append((f"{i}. {section.heading}", "heading", True, {}))
            blocks.extend((present(p, body=True), "body", False,
                           {"parenthetical_role": label_role} if (label_role := parenthetical_role(p)) else {}) for p in section.paragraphs)
        for visual in [*section.tables, *section.diagrams]:
            is_table = isinstance(visual, ReportTable)
            if is_table: table_number += 1
            else: diagram_number += 1
            number = table_number if is_table else diagram_number
            kind = "table" if is_table else "diagram"
            # All cell/label/connector text is staged through the same fenced commands.
            # The reference example uses the supplied caption itself. Keep
            # legacy numbering, but do not add an unrequested prefix in V3.
            caption = present(visual.caption) if reference else f"{'표' if is_table else '그림'} {number}. {present(visual.caption)}"
            note_visual = {"note_visual_group": f"{kind}-{number}", "note_visual_kind": kind} if reference else {}
            blocks.append((caption, "source", False,
                           {**({"visual_caption": kind} if rich else {}), **note_visual,
                            **({"note_visual_caption": True} if reference else {})}))
            rows = [visual.columns, *visual.rows] if is_table else [[part for j, step in enumerate(visual.steps) for part in (["→", step] if j else [step])]]
            for y, row in enumerate(rows):
                for x, cell in enumerate(row):
                    blocks.append((present(cell), "source", False, {**note_visual, **({"visual_id": f"{kind}-{number}", "visual_kind": kind,
                        "visual_row": y, "visual_column": x, "visual_columns": len(row)} if rich else {})}))
    if (draft.sources and not reference) or draft.limitations:
        limitation_heading = {"style_kind": enabled_role("heading", settings)} if reference else {}
        blocks.append(("확인 사항" if reference else "출처 및 확인 사항", "heading", True, limitation_heading))
        page_chars = 0
        for s in ([] if reference else draft.sources):
            value = f"{citation_marker(s.id, settings.get('citation_style', 'BRACKET_S'))} {s.title} ({s.published or '발행일 미확인'}) · {s.url}"
            new_page = page_chars + len(value) > 1100
            page_chars = len(value) if new_page else page_chars + len(value)
            blocks.append((value, "source", new_page, {}))
        limitation_counters = [0, 0, 0, 0]
        for p in draft.limitations:
            metadata = {}
            if reference:
                # Limitations are report content too. Resolve the same enabled
                # bullet roles as the sections rather than bypassing indents.
                prose = limitation_source_ranges(p, source_ids)
                if not re.match(r"^[□ㅇ○\-․]\s+", prose):
                    prose = "□ " + prose
                label_role = enabled_role(parenthetical_role(prose), settings)
                prose = present(prose, body=True, scope=label_role)
                value, limitation_role = body_item(prose, settings, limitation_counters, apply_numbered_markers=True)
                metadata = {"style_kind": limitation_role, "parenthetical_role": label_role, "note_body_role": label_role}
            else:
                value = "확인 사항: " + present(p)
            new_page = page_chars + len(value) > 1100
            page_chars = len(value) if new_page else page_chars + len(value)
            blocks.append((value, "body", new_page, metadata))
    if draft.research_notice is not None:
        # Explicit frozen messages only: no inference from today's execution or
        # global defaults. Preserve all six original limitations and their text.
        # Plain body notices add no extra heading to an already frozen TOC.
        for warning in draft.research_notice.warnings:
            blocks.append(("확인 사항: " + warning.message, "body", False, {}))
    if reference:
        from .report_notes import localize_notes, TITLE_PRESENTATION_POLICY, SECTION_GROUPING_POLICY
        blocks = localize_notes(blocks, draft.sources, settings, presentation_policy=TITLE_PRESENTATION_POLICY,
                                grouping_policy=SECTION_GROUPING_POLICY)
    if concise and not reference:
        from .report_outline import split_bullet
        blocks = [(part, kind, forced and i == 0, meta) for text, kind, forced, meta in blocks
                  for i, part in enumerate(split_bullet(text, [e["text"] for e in emphasis] if rich else ()) if kind == "body" else [text])]
    result, used = [], 0
    for text, kind, forced, meta in blocks:
        style_kind = meta.get("style_kind", role(text, kind))
        # Preserve every character; split only at an existing space when possible.
        # Bounded chunks leave room for larger mandated fonts and indents.
        chunks = []
        remaining = text
        while kind == "body" and len(remaining) > 380:
            cut = remaining.rfind(" ", 250, 380)
            cut = cut + 1 if cut >= 250 else 380
            offset = len(text) - len(remaining)
            for start, end in meta.get("citation_ranges", ()):
                if start < offset + cut < end:
                    cut = start - offset
                    break
            chunks.append(remaining[:cut]); remaining = remaining[cut:]
        chunks.append(remaining)
        chunk_offset = 0
        for index, chunk in enumerate(chunks):
            weighted = sum(1 if ord(c) > 255 else .6 for c in chunk)
            width, line, gap = (33, 22.4, 14) if kind == "body" else (27, 35.2, 25) if kind == "title" else (29, 25.6, 14) if kind == "heading" else (39, 15.6, 7)
            if style_kind in ("square", "circle", "dash"):
                width, line, gap = 29, 24, 10
            cost = math.ceil(weighted / width) * line + gap
            new_page = (forced and index == 0) or (used > 0 and used + cost > 570)
            used = cost if new_page else used + cost
            result.append({"text": chunk, "kind": kind, "page_break": new_page, **meta})
            if reference and (kind != "source" or meta.get("visual_id") or meta.get("visual_caption")):
                ranges = [(start - chunk_offset, end - chunk_offset) for start, end in meta.get("citation_ranges", rendered_marks.get(text, ()))
                          if chunk_offset <= start < end <= chunk_offset + len(chunk)]
                result[-1].pop("citation_ranges", None)
                if ranges:
                    result[-1]["citation_ranges"] = ranges
            chunk_offset += len(chunk)
            if rich:
                local_ids = meta.get("citation_source_ids")
                scope = meta.get("parenthetical_role")
                local_emphasis = ([{**item, "text": present(item["text"], body=True, scope=scope)} for item in emphasis]
                                  if scoped_labels and scope else emphasis)
                if local_ids:
                    numbers = {identifier: n for n, identifier in enumerate(local_ids, 1)}
                    if meta.get("table_common_source_id"):
                        from .report_notes import without_source_tokens
                        local_emphasis = [{**item, "text": without_source_tokens(item["text"], {meta["table_common_source_id"]})}
                                          for item in local_emphasis if all(ref in numbers for ref in re.findall(r"\[(S\d+)\]", item["text"]))]
                    else:
                        local_emphasis = [{**item, "text": presentation_text(item["text"], settings, source_ids, marker_numbers=numbers)}
                                         for item in local_emphasis if all(ref in numbers for ref in re.findall(r"\[(S\d+)\]", item["text"]))]
                result[-1]["emphasis"] = [item for item in local_emphasis if item["text"] and item["text"] in chunk]
            if style_kind in ("square", "circle", "dash", "dot"):
                result[-1]["style_kind"] = style_kind + ("_continuation" if index else "")
    return result
