"""Synthetic inline formatting fences; actual engine evidence is separate."""
import copy
from xml.etree import ElementTree as E

import pytest
from fastapi import HTTPException

from app.block_hwpx import HP, Semantic
from app.document_rewrite import RewriteParagraph, inline_runs, protected_positions, validated_rewrites, verify_rewrite_output
from app.hwpx import digest
from document_rewrite_fixture import document, REPLACEMENTS
from test_document_blocks import changed_zip


def plan():
    raw = document(); semantic = Semantic(raw); index = 7
    text = "".join(semantic.section[index].itertext())
    _, runs = inline_runs(semantic, semantic.section[index])
    paragraph = dict(section=0, paragraph=index, text=text, text_sha256=digest(text.encode()), length=len(text),
                     fixed_prefix="", protected_text=[run[0].text for run in runs])
    change = dict(section=0, paragraph=index, before_sha256=paragraph["text_sha256"], replacement=REPLACEMENTS[text])
    return raw, dict(source_sha256=digest(raw), paragraphs=[paragraph], preserved={}, unavailable=""), change


def output(raw, change):
    semantic = Semantic(raw); p = semantic.section[change["paragraph"]]
    template, protected = inline_runs(semantic, p)
    for run in list(p): p.remove(run)
    offset = 0
    for run, (start, end) in zip(protected, protected_positions(change["replacement"], [r[0].text for r in protected])):
        if start > offset:
            body = copy.deepcopy(template); body[0].text = change["replacement"][offset:start]; p.append(body)
        p.append(run); offset = end
    body = copy.deepcopy(template); body[0].text = change["replacement"][offset:]; p.append(body)
    return changed_zip(raw, {"Contents/section0.xml": E.tostring(semantic.section)})


def test_inline_emphasis_and_superscript_verification():
    raw, context, change = plan()
    RewriteParagraph(**context["paragraphs"][0])
    assert validated_rewrites({"rewrite_context": context}, [change]) == [change]
    assert verify_rewrite_output(raw, output(raw, change), context, [change])


@pytest.mark.parametrize("mutation", ["remove", "duplicate", "reorder", "number"])
def test_protected_inline_text_cannot_be_rewritten_or_relocated_ambiguously(mutation):
    _, context, change = plan()
    if mutation == "remove": change["replacement"] = change["replacement"].replace("주1)", "")
    if mutation == "duplicate": change["replacement"] += " 주1)"
    if mutation == "reorder": change["replacement"] = "□ 주1) 취약 버전 점검 예정"
    if mutation == "number": change["replacement"] += " 30일"
    with pytest.raises(ValueError): validated_rewrites({"rewrite_context": context}, [change])


def test_inline_protection_cannot_be_omitted_or_styles_flattened():
    raw, context, change = plan(); result = output(raw, change)
    omitted = copy.deepcopy(context); omitted["paragraphs"][0]["protected_text"] = []
    with pytest.raises(HTTPException): verify_rewrite_output(raw, result, omitted, [change])
    semantic = Semantic(result); runs = semantic.section[7].findall(f"{{{HP}}}run")
    for run in runs: run.set("charPrIDRef", runs[0].get("charPrIDRef"))
    flat = changed_zip(result, {"Contents/section0.xml": E.tostring(semantic.section)})
    with pytest.raises(HTTPException): verify_rewrite_output(raw, flat, context, [change])
