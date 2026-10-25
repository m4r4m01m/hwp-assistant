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
    PRESETS, VisualSettingsV3, defaults, legacy_defaults, original_projection, render_preset,
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
        if settings.get("preview_layout_policy") == "NATIVE_V1":
            if preset_id == "appendix-1" and ordinal in (407, 408, 409):
                # Only these known caption/unit/inline-table alignment prefixes
                # move into native paragraph/object positioning.
                original_text = original_text.lstrip(" ")
            elif preset_id in ("appendix-2", "appendix-3"):
                match = re.fullmatch(r"(.*?)\s*-{3,}\s*(\d+)\s*", original_text)
                if match is None and paragraph.find(f".//{{{HP}}}tab") is not None:
                    match = re.fullmatch(r"(.*?)\s+(\d+)\s*", original_text)
                if match is not None:
                    original_text = match[1].rstrip() + match[2]
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


def _archive_contents(raw):
    # DEFLATE bytes/offsets can differ across zlib versions, even
    # when every HWPX member is identical. Pin all member bytes, their order,
    # and stable ZIP metadata, not a particular compressor's bitstream.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {"comment": archive.comment.hex(), "members": [
            {"name": entry.filename, "sha256": digest(archive.read(entry)),
             "date_time": entry.date_time, "compression": entry.compress_type,
             "create_system": entry.create_system, "create_version": entry.create_version,
             "extract_version": entry.extract_version, "flags": entry.flag_bits,
             "internal_attr": entry.internal_attr, "external_attr": entry.external_attr,
             "extra": entry.extra.hex(), "comment": entry.comment.hex()}
            for entry in archive.infolist()
        ]}


# Content snapshots were derived from pre-AUTO reference_standard.py at
# 5977f337b17a6f6e35aa5cf20b4496194cf3ce16 with these exact settings. Its
# output and the current STARS renderer were also byte-identical in the same
# runtime. Persisted preview-byte/retry preservation has separate API tests.
@pytest.mark.parametrize("preset_id,settings_sha,preview_contents_sha", [
    ("appendix-1", "59ad7f985800bdcac06ee607a0bba9a9609259ab914397257bc770aaf693460a", "7a4449373aed8c87d7534a391fa878d545af4ce4f08fe8b151ed443b479204ed"),
    ("appendix-2", "2499f9a07ea2945e3816a6976b8efb57014ead3b071d66db45519fd8b29cbb81", "96fd36fe3262fb71bb7c739069ec30ef96165f30be6013a0bb32fa5b37e7be64"),
    ("appendix-3", "dca24e30b4acffbff151e83b46e0e73a544466967f923b94a415442b85ffd330", "eceea33babde5c8bea3e1cbca3479b6ccba6a6deda88d7a5b84b51b00e7d15ac"),
])
def test_frozen_star_settings_and_preview_keep_pre_auto_contents(preset_id, settings_sha, preview_contents_sha):
    from app.report_contract import report_hash
    settings = {**legacy_defaults(preset_id), "citation_style": "STARS"}
    assert VisualSettingsV3.model_validate(settings).model_dump(mode="json") == settings
    assert report_hash(settings) == settings_sha
    assert report_hash(_archive_contents(render_preset(settings))) == preview_contents_sha


@pytest.mark.parametrize("preset_id,settings_sha,preview_contents_sha", [
    ("appendix-1", "395d93614673adb0407faf3da7d6b338a85ccf848e2bf74cfacd073e035a5661", "a3c9b4fe63ee23dabffc3cef336985621f1763b41c828c45cbd1f0056309ae74"),
    ("appendix-2", "c1f43dd0e6d74ad35171c9e756aec699cb3f50245ea995f2000f85f08aa3268e", "96fd36fe3262fb71bb7c739069ec30ef96165f30be6013a0bb32fa5b37e7be64"),
    ("appendix-3", "d8a7be2c36a1f30084abd58f48e79f1b9ea9635b3798f65195ebd81a1ce24df8", "eceea33babde5c8bea3e1cbca3479b6ccba6a6deda88d7a5b84b51b00e7d15ac"),
])
def test_pre_native_auto_settings_and_all_archive_members_are_unchanged(preset_id, settings_sha, preview_contents_sha):
    from app.report_contract import report_hash
    old = legacy_defaults(preset_id)
    serialized = VisualSettingsV3.model_validate(old).model_dump(mode="json")
    assert serialized == old and "preview_layout_policy" not in serialized
    assert report_hash(serialized) == settings_sha
    assert report_hash(_archive_contents(render_preset(serialized))) == preview_contents_sha
    assert VisualSettingsV3.model_validate({**old, "preview_layout_policy": None}).model_dump(mode="json") == old


def test_native_preview_defaults_are_explicit_and_catalog_consistent():
    from app.reference_standard import field_catalog, builtin_identity
    new, old = defaults(), legacy_defaults()
    assert new["preview_layout_policy"] == "NATIVE_V1"
    assert new["unit_horizontal_align"] == "RIGHT"
    assert new["toc_major_horizontal_align"] == new["toc_minor_horizontal_align"] == "LEFT"
    assert old["unit_horizontal_align"] == "CENTER"
    assert old["toc_major_horizontal_align"] == old["toc_minor_horizontal_align"] == "JUSTIFY"
    assert all(item["key"] not in new if item.get("optional") else item["default"] == new[item["key"]] for item in field_catalog())
    assert builtin_identity("appendix-1") != builtin_identity("appendix-1", legacy=True)


@pytest.mark.parametrize("preset,table_id,count", [("appendix-2", "1872749252", 14), ("appendix-3", "1872749254", 3)])
def test_native_toc_uses_right_tabs_in_individual_full_width_cells(preset, table_id, count):
    settings = defaults(preset)
    settings.update(toc_minor_before_pt=1, toc_minor_after_pt=1)
    header, section = _parts(render_preset(settings))
    table = next(item for item in section.iter(f"{{{HP}}}tbl") if item.get("id") == table_id)
    fills = {item.get("id"): item for item in header.iter(f"{{{HH}}}borderFill")}
    paras = {item.get("id"): item for item in header.iter(f"{{{HH}}}paraPr")}
    tabs = {item.get("id"): item for item in header.iter(f"{{{HH}}}tabPr")}
    rows = table.findall(f"{{{HP}}}tr")
    assert len(rows) == int(table.get("rowCnt")) and len(rows) > 3
    entries = []
    for index, row in enumerate(rows[2:]):
        cell, = row.findall(f"{{{HP}}}tc")
        paragraph, = cell.findall(f"{{{HP}}}subList/{{{HP}}}p")
        assert cell.find(f"{{{HP}}}cellSpan").get("colSpan") == "3"
        assert cell.find(f"{{{HP}}}cellAddr").get("rowAddr") == str(index + 2)
        assert cell.find(f"{{{HP}}}cellSz").get("width") == table.find(f"{{{HP}}}sz").get("width")
        bottom = fills[cell.get("borderFillIDRef")].find(f"{{{HH}}}bottomBorder").get("type")
        assert bottom == ("NONE" if index < len(rows) - 3 else settings["toc_border_style"])
        inline = paragraph.find(f".//{{{HP}}}tab")
        if inline is not None:
            assert inline.attrib == {"width": "0", "leader": "0", "type": "1"}
            para = paras[paragraph.get("paraPrIDRef")]
            native = tabs[para.get("tabPrIDRef")].find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}tabItem")
            assert native.get("type") == "RIGHT" and native.get("leader") == "DASH"
            text = _paragraph_texts([paragraph])[0]
            assert "---" not in text and re.search(r"\d+$", text)
            entries.append(text)
    assert len(entries) == count


def test_native_full_width_table_and_caption_have_no_old_alignment_padding():
    _, section = _parts(render_preset(defaults()))
    host = next(p for p in section if p.find(f"{{{HP}}}run/{{{HP}}}tbl[@id='1872749261']") is not None)
    assert "".join(t.text or "" for t in host.findall(f"{{{HP}}}run/{{{HP}}}t")) == ""
    table = host.find(f"{{{HP}}}run/{{{HP}}}tbl")
    assert table.find(f"{{{HP}}}outMargin").get("left") == table.find(f"{{{HP}}}outMargin").get("right") == "0"
    assert int(table.find(f"{{{HP}}}sz").get("width")) == mm_to_hwp(210) - 2 * mm_to_hwp(20)
    assert any(text.startswith("우리나라 현황") for text in _paragraph_texts(list(section)))


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
