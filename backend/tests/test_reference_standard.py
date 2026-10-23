"""Original-rule defaults and lossless-content checks for revision 3.

These are pure HWPX checks. They do not call a model, a server or the rhwp engine.
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

import pytest

from app.document_standard import HP, HH, load_standard
from app.hwpx import digest, validate
from app.reference_source import LANGUAGES, mm_to_hwp
from app.reference_standard import (
    PRESETS, VisualSettingsV3, defaults, original_projection, render_preset,
)


REFERENCE_SHA256 = "6c68b50656867f1fc724c50549e60e1109e2f7a00219f2928ef58de0a45711f1"


def _parts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return (ET.fromstring(archive.read("Contents/header.xml")),
                ET.fromstring(archive.read("Contents/section0.xml")))


def _paragraph_texts(paragraphs):
    return ["".join("".join(t.itertext()) for run in p.findall(f"{{{HP}}}run")
                    for t in run.findall(f"{{{HP}}}t"))
            for host in paragraphs for p in host.iter(f"{{{HP}}}p")]


def _assert_preset_content(section, source_section, settings):
    """Only explicit structural marker slots may differ; every other char stays."""
    preset_id = settings["preset_id"]
    _, _, start, stop = PRESETS[preset_id]
    source_paragraphs = [p for host in list(source_section)[start:stop]
                         for p in host.iter(f"{{{HP}}}p")]
    ordinals = {p: i for i, p in enumerate(source_section.iter(f"{{{HP}}}p"))}
    before = _paragraph_texts(list(source_section)[start:stop])
    after = _paragraph_texts(list(section))
    assert len(before) == len(after)
    if preset_id == "appendix-1" and settings["citation_style"] == "AUTO":
        # Only the new AUTO derivative orders the three verified explanations
        # by first citation. Match each unchanged source paragraph at its new slot.
        indices = [i for i, p in enumerate(source_paragraphs) if ordinals[p] in (419, 420, 421)]
        originals = {ordinals[source_paragraphs[i]]: (source_paragraphs[i], before[i]) for i in indices}
        for index, ordinal in zip(indices, (421, 419, 420)):
            source_paragraphs[index], before[index] = originals[ordinal]
    # Pristine appendix 1 has these exact numbering/linked-note slots. Cover/TOC
    # examples and all prose, dates, quantities, spaces and reading order remain.
    numbered = {393: ("1.", "1."), 395: ("가.", "가."), 397: ("1)", "(1)"),
                405: ("나.", "나."), 423: ("2.", "2.")}
    for ordinal, name, literal, replacement in (
        (399, "square", "□", "(가)"), (401, "circle", "ㅇ", "①"), (403, "dash", "-", "㉮")):
        numbered[ordinal] = literal, (replacement if settings["numbering_style"] == "NUMBERED"
                                      else settings[f"bullet_{name}_marker"])
    marks = ("주1)", "주2)", "주3)") if settings["citation_style"] in ("NOTE", "AUTO") else ("*", "**", "***")
    citations = {407: ("*", marks[0], "end"), 411: ("주1)", marks[1], "end"),
                 412: ("주2)", marks[2], "end"), 419: ("주1)", marks[1], "start"),
                 420: ("주2)", marks[2], "start"), 421: ("*", marks[0], "start")}
    def mask(text, literal, position):
        pattern = (r"^(\s*)" + re.escape(literal) + r"(?=\s)") if position == "start" else re.escape(literal) + r"(?=\s*$)"
        match = re.search(pattern, text)
        assert match is not None, (literal, repr(text))
        return text[:match.start()] + ((match[1] if position == "start" else "") + "<STRUCTURAL_MARKER>") + text[match.end():]
    for paragraph, original_text, rendered_text in zip(source_paragraphs, before, after):
        ordinal = ordinals[paragraph]
        if preset_id == "appendix-1" and ordinal in numbered:
            old, new = numbered[ordinal]
            # Only the exact known item-prefix spaces move into paragraph
            # indentation. All other spaces remain part of the content check.
            original_text, rendered_text = mask(original_text.lstrip(), old, "start"), mask(rendered_text, new, "start")
        if preset_id == "appendix-1" and ordinal in citations:
            old, new, position = citations[ordinal]
            original_text, rendered_text = mask(original_text, old, position), mask(rendered_text, new, position)
        assert rendered_text == original_text, ordinal


def _projection_semantics(node):
    """Whitelist only the documented reading-copy differences."""
    local = node.tag.split("}")[-1]
    attributes = {key: "#FFFFFFFF" if key in ("shadeColor", "faceColor")
                  and value.casefold() == "none" else value for key, value in node.attrib.items()}
    children = [child for child in node if not (
        local == "secPr" and child.tag.split("}")[-1] in ("presentation", "parameterset"))]
    return node.tag, sorted(attributes.items()), node.text, node.tail, tuple(
        _projection_semantics(child) for child in children)


def test_revision_three_defaults_follow_written_original_rules_over_sample_xml():
    settings = VisualSettingsV3.model_validate(defaults()).model_dump(mode="json")
    assert (settings["caption_size_pt"], settings["caption_line_percent"],
            settings["caption_horizontal_align"], settings["caption_underline"]) == (14, 110, "CENTER", "BOTTOM")
    assert (settings["body_size_pt"], settings["body_horizontal_align"],
            settings["body_line_percent"]) == (14, "JUSTIFY", 160)
    assert (settings["table_size_pt"], settings["table_line_percent"],
            settings["table_horizontal_align"]) == (12, 110, "CENTER")
    # General reference/note and table/figure notes have distinct source rules.
    assert (settings["source_size_pt"], settings["source_line_percent"]) == (12, 130)
    assert (settings["table_source_size_pt"], settings["table_source_line_percent"]) == (12, 110)
    assert settings["table_border_color"] == "#000000"
    assert settings["table_border_scope"] == "NO_OUTER_SIDES"
    assert settings["body_parenthetical_labels"] is False
    assert settings["show_core_summary"] is False
    assert settings["numbering_style"] == "BULLETS"
    assert settings["citation_style"] == "AUTO"
    assert [settings[role + "_left_indent_pt"] for role in
            ("heading", "subheading", "minor_heading", "numbered_4", "numbered_5", "numbered_6")] == [0, 7.5, 14, 21, 28, 35]


def test_numbered_levels_promote_only_original_item_spaces_to_native_indentation():
    settings = VisualSettingsV3.model_validate({**defaults(), "numbering_style": "NUMBERED"}).model_dump(mode="json")
    header, section = _parts(render_preset(settings))
    paras = {node.get("id"): node for node in header.iter(f"{{{HH}}}paraPr")}
    expected = {"1. 핀테크 현황": 0, "가. 개 요": 750, "(1) 현황 ": 1400,
                "(가) ‘A사’는": 2100, "① 2015년 현재": 2800, "㉮ 박찬호": 3500}
    seen = set()
    for paragraph, text in zip(section.iter(f"{{{HP}}}p"), _paragraph_texts(list(section))):
        for prefix, indent in expected.items():
            if text.startswith(prefix):
                seen.add(prefix)
                assert not text[0].isspace()
                para = paras[paragraph.get("paraPrIDRef")]
                for branch in para.find(f"{{{HP}}}switch"):
                    factor = 2 if branch.tag.endswith("default") else 1
                    margin = branch.find(f"{{{HH}}}margin")
                    assert margin.find("{http://www.hancom.co.kr/hwpml/2011/core}left").get("value") == str(indent * factor)
    assert seen == set(expected)


@pytest.mark.parametrize("preset_id,required_words", [
    ("appendix-1", ("핀테크 발전 전망과 과제", "‘A사’", "구분", "정보통신진흥협회")),
    ("appendix-2", ("핀테크 발전 전망과 과제", "2015. 4.", "< 목", "○○부 ○○팀")),
    ("appendix-3", ("핀테크 발전 전망과 과제", "2015. 4.", "< 목", "○○부 ○○팀")),
])
def test_three_presets_preserve_exact_original_paragraph_content_and_source(preset_id, required_words):
    profile, original = load_standard()
    assert profile["reference_sha256"] == digest(original) == REFERENCE_SHA256
    _, source_section = _parts(original)
    _, _, start, stop = PRESETS[preset_id]
    expected = _paragraph_texts(list(source_section)[start:stop])
    settings = VisualSettingsV3.model_validate(defaults(preset_id)).model_dump(mode="json")
    result = render_preset(settings)
    validate(result)
    _, section = _parts(result)
    _assert_preset_content(section, source_section, settings)
    text = "\n".join(expected)
    assert all(word in text for word in required_words)
    assert load_standard()[1] == original


def test_original_projection_changes_only_presentation_parameters_and_no_fill_encoding():
    _, original = load_standard()
    projection = original_projection()
    validate(projection)
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(io.BytesIO(projection)) as result:
        assert source.namelist() == result.namelist()
        for name in source.namelist():
            before, after = source.read(name), result.read(name)
            if name.endswith((".xml", ".hpf")):
                source_root, actual_root = ET.fromstring(before), ET.fromstring(after)
                assert _projection_semantics(source_root) == _projection_semantics(actual_root), name
                for props in actual_root.iter(f"{{{HP}}}secPr"):
                    assert not any(child.tag.split("}")[-1] in ("presentation", "parameterset") for child in props)
                assert not any(value.casefold() == "none" for element in actual_root.iter()
                               for key, value in element.attrib.items() if key in ("shadeColor", "faceColor"))
            else:
                assert after == before, name
    assert _paragraph_texts([_parts(projection)[1]]) == _paragraph_texts([_parts(original)[1]])
    assert digest(load_standard()[1]) == REFERENCE_SHA256


@pytest.mark.parametrize("preset_id,settings_sha,preview_sha", [
    ("appendix-1", "59ad7f985800bdcac06ee607a0bba9a9609259ab914397257bc770aaf693460a", "d10d5c2f01ff4a9ba389a9e8882e6ca31048a6fc63598031352357f64fe68ede"),
    ("appendix-2", "2499f9a07ea2945e3816a6976b8efb57014ead3b071d66db45519fd8b29cbb81", "46f7ad6e697b49aa47081bf99d4d0d9a934100c9ebdc08cc97b1aaedc2a749d5"),
    ("appendix-3", "dca24e30b4acffbff151e83b46e0e73a544466967f923b94a415442b85ffd330", "13b21e1b39c2041bdf2b020abd81254be9be08289f729dc969205355f875dc54"),
])
def test_frozen_star_settings_and_preview_keep_pre_auto_hashes(preset_id, settings_sha, preview_sha):
    from app.report_contract import report_hash
    settings = {**defaults(preset_id), "citation_style": "STARS"}
    assert VisualSettingsV3.model_validate(settings).model_dump(mode="json") == settings
    assert report_hash(settings) == settings_sha
    assert digest(render_preset(settings)) == preview_sha


def test_auto_sample_uses_numbered_markers_for_the_same_three_note_table():
    _, section = _parts(render_preset(defaults()))
    texts = _paragraph_texts(list(section))
    assert "대표기업주2)" in texts and "서비스 실적주3)" in texts
    assert any(text.endswith("현황주1)") for text in texts)
    assert any(text.lstrip().startswith("주1) ") for text in texts)
    assert any(text.lstrip().startswith("주2) ") for text in texts)
    assert any(text.lstrip().startswith("주3) ") for text in texts)
    assert [re.match(r"^\s*(주[1-3]\))\s", text)[1] for text in texts if re.match(r"^\s*(주[1-3]\))\s", text)] == ["주1)", "주2)", "주3)"]


def test_preset_applies_font_size_page_and_cell_border_controls_without_rewriting_content():
    settings = defaults("appendix-1")
    settings.update(title_font="원문 표준 검증 글꼴", title_size_pt=27,
        page_width_mm=220, margin_left_mm=25, margin_header_mm=12,
        table_border_scope="ALL", table_border_style="DOT", table_border_color="#102A43",
        table_border_width_mm=.3, table_padding_left_mm=2.5, table_padding_top_mm=1,
        table_min_row_height_mm=9, table_header_fill="#DDEEFF", table_vertical_align="TOP")
    settings = VisualSettingsV3.model_validate(settings).model_dump(mode="json")
    result = render_preset(settings)
    header, section = _parts(result)
    _, original = load_standard()
    _, source_section = _parts(original)
    _, _, start, stop = PRESETS["appendix-1"]
    _assert_preset_content(section, source_section, settings)
    title = next(p for p in list(section)[0].iter(f"{{{HP}}}p")
                 if "핀테크 발전 전망과 과제" in "".join(t.text or ""
                     for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t")))
    run = next(run for run in title.findall(f"{{{HP}}}run")
               if any((t.text or "").strip() for t in run.findall(f"{{{HP}}}t")))
    char = next(c for c in header.iter(f"{{{HH}}}charPr") if c.get("id") == run.get("charPrIDRef"))
    assert char.get("height") == "2700"
    refs = char.find(f"{{{HH}}}fontRef")
    for language in LANGUAGES:
        face = next(face for face in header.iter(f"{{{HH}}}fontface")
                    if face.get("lang").casefold() == language)
        assert next(font for font in face if font.get("id") == refs.get(language)).get("face") == settings["title_font"]
    page = section.find(f".//{{{HP}}}pagePr")
    assert page.get("width") == str(mm_to_hwp(220))
    assert page.find(f"{{{HP}}}margin").get("left") == str(mm_to_hwp(25))
    assert page.find(f"{{{HP}}}margin").get("header") == str(mm_to_hwp(12))
    table = next(table for table in section.iter(f"{{{HP}}}tbl")
                 if table.get("id") == "1872749261")
    fills = {fill.get("id"): fill for fill in header.iter(f"{{{HH}}}borderFill")}
    for cell in table.iter(f"{{{HP}}}tc"):
        fill = fills[cell.get("borderFillIDRef")]
        for edge in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
            border = fill.find(f"{{{HH}}}{edge}")
            assert (border.get("type"), border.get("width"), border.get("color")) == ("DOT", "0.3 mm", "#102A43")
        margin = cell.find(f"{{{HP}}}cellMargin")
        assert margin.get("left") == str(mm_to_hwp(2.5))
        assert margin.get("top") == str(mm_to_hwp(1))
        assert int(cell.find(f"{{{HP}}}cellSz").get("height")) >= mm_to_hwp(9)
        assert cell.find(f"{{{HP}}}subList").get("vertAlign") == "TOP"
    first_fill = fills[table.find(f"{{{HP}}}tr/{{{HP}}}tc").get("borderFillIDRef")]
    assert next(item for item in first_fill.iter() if item.tag.endswith("winBrush")).get("faceColor") == "#DDEEFF"
    assert digest(load_standard()[1]) == REFERENCE_SHA256


def test_custom_bullet_and_note_markers_apply_without_superscript_or_claim_changes():
    settings = defaults("appendix-1")
    settings.update(numbering_style="BULLETS", bullet_square_marker="◆", bullet_circle_marker="▷",
                    bullet_dash_marker="·", citation_style="NOTE", citation_superscript=False)
    settings = VisualSettingsV3.model_validate(settings).model_dump(mode="json")
    header, section = _parts(render_preset(settings))
    _, original = load_standard()
    _, source_section = _parts(original)
    _assert_preset_content(section, source_section, settings)
    texts = _paragraph_texts(list(section))
    assert any(text.lstrip().startswith("◆ ‘A사’") for text in texts)
    assert any(text.startswith("▷ 2015년") for text in texts)
    assert any(text.startswith("· 박찬호") for text in texts)
    assert "대표기업주2)" in texts and "서비스 실적주3)" in texts
    chars = {char.get("id"): char for char in header.iter(f"{{{HH}}}charPr")}
    inline_markers = []
    for paragraph in section.iter(f"{{{HP}}}p"):
        for run in paragraph.findall(f"{{{HP}}}run"):
            text = "".join(t.text or "" for t in run.findall(f"{{{HP}}}t"))
            if text in ("주1)", "주2)", "주3)"):
                inline_markers.append(text)
                assert chars[run.get("charPrIDRef")].find(f"{{{HH}}}supscript") is None
    assert set(inline_markers) == {"주1)", "주2)", "주3)"}
    assert digest(load_standard()[1]) == REFERENCE_SHA256
