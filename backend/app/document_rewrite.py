"""Frozen, bounded paragraph rewrites; verification never publishes a document."""
import copy
import io
import re
import zipfile
from collections import Counter

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .block_hwpx import HP, Semantic, serialize_block_xml, verify_engine_output
from .editor_design_hwpx import paragraph_controls, _role
from .hwpx import digest


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def protected_positions(text, protected):
    end, positions = 0, []
    if len(protected) > 24 or sum(map(len, protected)) > 1000:
        raise ValueError("고정할 강조·주석 범위를 초과했습니다.")
    for value in protected:
        start = text.find(value)
        if not value.strip() or start < end or start != text.rfind(value):
            raise ValueError("강조·주석 문구를 같은 순서로 한 번씩 유지하세요.")
        end = start + len(value)
        positions.append((start, end))
    return positions


class RewriteParagraph(Strict):
    section: int = Field(ge=0, le=0)
    paragraph: int = Field(ge=0, le=100000)
    text: str = Field(min_length=1, max_length=4000)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    length: int = Field(ge=1, le=4000)
    fixed_prefix: str = Field(default="", max_length=120)
    protected_text: list[str] = Field(default_factory=list, max_length=24)
    rendered_lines: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def integrity(self):
        if len(self.text) != self.length or digest(self.text.encode()) != self.text_sha256:
            raise ValueError("수정할 문단의 원문 확인값이 다릅니다.")
        if self.fixed_prefix and (not self.text.startswith(self.fixed_prefix) or not re.fullmatch(r"\s*[□ㅇ○\-․]\s+\([^()\r\n]{1,40}\)\s*", self.fixed_prefix)):
            raise ValueError("보존할 요약 말머리가 올바르지 않습니다.")
        if self.fixed_prefix and self.protected_text:
            raise ValueError("고정 말머리와 본문 강조 범위가 중복됩니다.")
        protected_positions(self.text, self.protected_text)
        return self


class RewriteContext(Strict):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    paragraphs: list[RewriteParagraph] = Field(max_length=120)
    preserved: dict[str, int] = Field(max_length=8)
    unavailable: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def bounded(self):
        addresses = [(p.section, p.paragraph) for p in self.paragraphs]
        if len(set(addresses)) != len(addresses) or sum(len(p.text) for p in self.paragraphs) > 24000:
            raise ValueError("전체 문단 수정은 120문단·24,000자 이내에서 가능합니다.")
        if any(type(v) is not int or v < 0 or v > 100000 for v in self.preserved.values()):
            raise ValueError("보존 범위가 올바르지 않습니다.")
        return self


class ParagraphRewrite(Strict):
    section: int = Field(ge=0, le=0)
    paragraph: int = Field(ge=0, le=100000)
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement: str = Field(min_length=1, max_length=4000)


def validated_rewrites(payload, proposed):
    if not proposed:
        return []
    context = payload.get("rewrite_context")
    if not context or context.get("unavailable"):
        raise ValueError("문서 전체 수정 범위가 없는 요청입니다.")
    source = {(p["section"], p["paragraph"]): p for p in context["paragraphs"]}
    seen = set()
    result = []
    for item in proposed:
        item = item.model_dump() if isinstance(item, BaseModel) else item
        address = (item["section"], item["paragraph"])
        before = source.get(address)
        if address in seen or not before or before["text_sha256"] != item["before_sha256"]:
            raise ValueError("수정안의 문단 위치·원문 확인값이 다릅니다.")
        seen.add(address)
        text = item["replacement"]
        protected_positions(text, before.get("protected_text", []))
        prefix = re.match(r"^\s*[□ㅇ○\-․]\s+", before["text"])
        if not prefix or not text.startswith(before.get("fixed_prefix") or prefix[0]) or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
            raise ValueError("본문 기호·문단 구조를 유지한 수정안이 필요합니다.")
        if not text[len(before.get("fixed_prefix") or prefix[0]):].strip():
            raise ValueError("본문 내용이 사라진 수정안은 적용할 수 없습니다.")
        numbers = lambda value: Counter(re.findall(r"\d+(?:[.,:/-]\d+)*", value))
        if numbers(before["text"]) != numbers(text):
            raise ValueError("수정안에서 숫자·기간·수치가 바뀌었습니다. 원문을 보존해 다시 요청하세요.")
        if text != before["text"]:
            result.append(item)
    if sum(len(item["replacement"]) for item in result) > 30000:
        raise ValueError("전체 문단 수정안 크기를 초과했습니다.")
    return result


def paragraph_prefix(semantic, paragraph):
    if paragraph_controls(paragraph):
        return None
    if _role(semantic.header, paragraph) not in ("square", "circle", "dash", "dot"):
        return None
    runs = [r for r in paragraph.findall(f"{{{HP}}}run") if any(t.text for t in r.findall(f"{{{HP}}}t"))]
    if not runs or not all(child.tag == f"{{{HP}}}t" and not len(child) for run in paragraph.findall(f"{{{HP}}}run") for child in run):
        return None
    last_style, split = runs[-1].get("charPrIDRef"), len(runs)
    while split > 0 and runs[split - 1].get("charPrIDRef") == last_style:
        split -= 1
    if split == 0:
        return ""
    text = lambda items: "".join(t.text or "" for r in items for t in r.findall(f"{{{HP}}}t"))
    prefix = text(runs[:split])
    if len(prefix) <= 120 and re.fullmatch(r"\s*[□ㅇ○\-․]\s+\([^()\r\n]{1,40}\)\s*", prefix) and text(runs[split:]).strip():
        return prefix
    return None


def inline_runs(semantic, paragraph):
    """Plain runs only; preserve every non-body style and its exact text."""
    if paragraph_controls(paragraph) or _role(semantic.header, paragraph) not in ("square", "circle", "dash", "dot"):
        return None
    runs = paragraph.findall(f"{{{HP}}}run")
    if not runs or not all(child.tag == f"{{{HP}}}t" and not len(child) for run in runs for child in run):
        return None
    groups = []
    for run in runs:
        text = "".join(t.text or "" for t in run)
        if not text:
            continue
        if groups and groups[-1].get("charPrIDRef") == run.get("charPrIDRef"):
            groups[-1][0].text += text
        else:
            item = copy.deepcopy(run)
            for child in list(item): item.remove(child)
            from xml.etree.ElementTree import SubElement
            SubElement(item, f"{{{HP}}}t").text = text
            groups.append(item)
    if not groups:
        return None
    protected = [run for run in groups if run.get("charPrIDRef") != groups[0].get("charPrIDRef")]
    try:
        protected_positions("".join(run[0].text for run in groups), [run[0].text for run in protected])
    except ValueError:
        return None
    return (groups[0], protected) if protected else None


def verify_rewrite_output(source, actual, context, changes):
    """Compare native output with approved text and every unchanged object/style."""
    if digest(source) != context["source_sha256"]:
        raise HTTPException(409, "질문을 보낸 원본 문서와 다릅니다.")
    original = Semantic(source)
    expected = copy.deepcopy(original.section)
    paragraphs = list(expected)
    inputs = {(p["section"], p["paragraph"]): p for p in context["paragraphs"]}
    for change in changes:
        index = change["paragraph"]
        prefix = paragraph_prefix(original, original.section[index]) if index < len(paragraphs) else None
        inline = inline_runs(original, original.section[index]) if prefix is None and index < len(paragraphs) else None
        if prefix is None and inline is None:
            raise HTTPException(422, "제목·표·주석 또는 혼합 서식은 전체 문장 수정 대상이 아닙니다.")
        paragraph = paragraphs[index]
        texts = list(paragraph.iter(f"{{{HP}}}t"))
        text = "".join(t.text or "" for t in texts)
        before = inputs.get((0, index))
        if not before or text != before["text"] or digest(text.encode()) != change["before_sha256"]:
            raise HTTPException(409, "원본 문단과 승인할 수정안이 다릅니다.")
        if inline:
            body_run, protected = inline
            values = [run[0].text for run in protected]
            if before.get("fixed_prefix") or before.get("protected_text") != values:
                raise HTTPException(409, "고정된 강조·주석 범위가 바뀌었습니다.")
            try:
                positions = protected_positions(change["replacement"], values)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            for run in paragraph.findall(f"{{{HP}}}run"): paragraph.remove(run)
            offset, runs = 0, []
            for run, (start, end) in zip(protected, positions):
                if start > offset:
                    body = copy.deepcopy(body_run); body[0].text = change["replacement"][offset:start]; runs.append(body)
                runs.append(copy.deepcopy(run)); offset = end
            if offset < len(change["replacement"]):
                body = copy.deepcopy(body_run); body[0].text = change["replacement"][offset:]; runs.append(body)
            for order, run in enumerate(runs): paragraph.insert(order, run)
            continue
        if before.get("protected_text"):
            raise HTTPException(409, "원문과 다른 강조·주석 범위입니다.")
        if prefix != before.get("fixed_prefix", "") or not change["replacement"].startswith(prefix):
            raise HTTPException(409, "고정된 요약 말머리가 바뀌었습니다.")
        offset, first = 0, 0
        while first < len(texts) and offset < len(prefix):
            offset += len(texts[first].text or "")
            first += 1
        if offset != len(prefix) or first >= len(texts):
            raise HTTPException(422, "요약 말머리와 본문 범위를 구분할 수 없습니다.")
        texts[first].text = change["replacement"][len(prefix):]
        for text_node in texts[first + 1:]:
            text_node.text = ""
    # This archive is verification-only; the user sees and adopts the separately
    # rendered native exact-command output, never this XML representation.
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in original.files.items():
            archive.writestr(name, serialize_block_xml(expected) if name == "Contents/section0.xml" else raw)
    verify_engine_output(source, output.getvalue(), actual)
    rendered = Semantic(actual)
    if {name for name in original.files if not name.startswith("Preview/")} != {name for name in rendered.files if not name.startswith("Preview/")}:
        raise HTTPException(422, "수정안에서 문서 자원이 추가되거나 누락됐습니다.")
    return True
