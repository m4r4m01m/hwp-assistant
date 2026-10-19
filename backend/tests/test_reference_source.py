"""Unit checks for the new source-derived path; no rhwp or model calls."""
import copy

from app.document_standard import load_standard, source_parts, HP, HH
from app.reference_source import (
    LANGUAGES, apply_character, apply_page, apply_paragraph, is_reference_settings,
    page_defaults, role_defaults, roles_source, source_profile,
)


def test_source_examples_keep_xml_and_explicit_rule_values_separate():
    before = load_standard()[1]
    roles = roles_source()
    # Leading formatting annotations must not select another font.
    assert roles["heading"]["defaults"]["font"] == "HY헤드라인M"
    assert roles["body"]["defaults"]["font"] == "휴먼명조"
    assert roles["caption"]["xml_defaults"]["size_pt"] == 12
    assert roles["caption"]["defaults"]["size_pt"] == 14
    assert roles["caption"]["defaults"]["line_percent"] == 110
    assert roles["caption"]["defaults"]["horizontal_align"] == "CENTER"
    assert roles["caption"]["provenance"]["rule_overrides"]["size_pt"]["ordinal"] == 86
    assert roles["circle"]["xml_defaults"]["spacing_percent"] == -4
    assert roles["circle"]["defaults"]["spacing_percent"] == 0
    assert roles["attachment"]["xml_defaults"]["size_pt"] == 14
    assert roles["attachment"]["defaults"]["size_pt"] == 20
    assert "목차" in roles["attachment"]["provenance"]["limitation"]
    assert roles["numbered_6"]["provenance"]["marker_rule"]["ordinal"] == 146
    assert roles["numbered_6"]["defaults"]["font"] == roles["body"]["defaults"]["font"]
    assert roles["source"]["defaults"]["line_percent"] == 130
    assert roles["square"]["xml_defaults"]["left_indent_pt"] == 0
    assert roles["square"]["defaults"]["left_indent_pt"] == 24.75
    indent = roles["square"]["provenance"]["native_prefix_indent"]
    assert (indent["marker_x_px"] - indent["body_origin_x_px"]) / indent["css_pixels_per_pt"] == 24.75
    assert indent["prefix"] == "     " and indent["rendered_page"] == 9
    assert roles["table_source"]["defaults"]["line_percent"] == 110
    # Callers own clones; extraction never mutates the canonical HWPX.
    roles["body"]["char"].set("height", "9999")
    roles["square"]["para"].set("id", "changed")
    again = roles_source()
    assert again["body"]["char"].get("height") == "1400"
    assert again["square"]["para"].get("id") == "2"
    assert load_standard()[1] == before
    flat = role_defaults()
    assert flat["body_size_pt"] == 14 and flat["body_horizontal_align"] == "JUSTIFY"
    assert source_profile()["defaults"]["source_line_percent"] == 130
    for role, value in (("subheading", 7.5), ("minor_heading", 14),
                        ("numbered_4", 21), ("numbered_5", 28), ("numbered_6", 35)):
        assert roles[role]["defaults"]["left_indent_pt"] == value
        derivation = roles[role]["provenance"]["rule_overrides"]["left_indent_pt"]
        assert derivation["ordinal"] == 132 and "한 칸" in derivation["interpretation"]


def test_character_settings_resolve_a_new_font_for_every_language_and_effect():
    _, header, _ = source_parts()
    char = roles_source()["body"]["char"]
    settings = {"body_font": "검증용 새 글꼴", "body_size_pt": 15.5,
                "body_ratio_percent": 95, "body_spacing_percent": -2,
                "body_color": "#123abc", "body_bold": True,
                "body_italic": True, "body_underline": "BOTTOM"}
    apply_character(header, char, "body", settings)
    assert char.get("height") == "1550" and char.get("textColor") == "#123ABC"
    refs = char.find(f"{{{HH}}}fontRef")
    for language in LANGUAGES:
        face = next(item for item in header.iter(f"{{{HH}}}fontface")
                    if item.get("lang").casefold() == language)
        assert next(item for item in face if item.get("id") == refs.get(language)).get("face") == "검증용 새 글꼴"
        assert face.get("fontCnt") == str(len(face))
    assert set(char.find(f"{{{HH}}}ratio").attrib.values()) == {"95"}
    assert set(char.find(f"{{{HH}}}spacing").attrib.values()) == {"-2"}
    assert char.find(f"{{{HH}}}bold") is not None
    assert char.find(f"{{{HH}}}italic") is not None
    assert char.find(f"{{{HH}}}underline").get("type") == "BOTTOM"
    font_count = sum(len(face) for face in header.iter(f"{{{HH}}}fontface"))
    apply_character(header, char, "body", {**settings, "body_bold": False,
                    "body_italic": False, "body_underline": "NONE"})
    assert sum(len(face) for face in header.iter(f"{{{HH}}}fontface")) == font_count
    assert all(char.find(f"{{{HH}}}{effect}") is None for effect in ("bold", "italic", "underline"))


def test_paragraph_signed_indents_are_scaled_in_both_hwpunit_branches():
    para = roles_source()["body"]["para"]
    apply_paragraph(para, "body", {"body_horizontal_align": "LEFT", "body_line_percent": 175,
        "body_before_pt": 3.5, "body_after_pt": 9, "body_left_indent_pt": 10,
        "body_right_indent_pt": 2, "body_first_line_indent_pt": -15})
    assert para.find(f"{{{HH}}}align").get("horizontal") == "LEFT"
    assert all(item.get("value") == "175" for item in para.iter(f"{{{HH}}}lineSpacing"))
    for branch in para.find(f"{{{HP}}}switch"):
        factor = 2 if branch.tag.endswith("default") else 1
        values = {item.tag.split("}")[-1]: int(item.get("value"))
                  for item in branch.find(f"{{{HH}}}margin")}
        assert values == {key: value * factor for key, value in
            {"intent": -1500, "left": 1000, "right": 200, "prev": 350, "next": 900}.items()}


def test_page_defaults_preserve_original_unit_rounding_and_change_six_margins():
    _, _, section = source_parts()
    sec_pr = copy.deepcopy(section.find(f".//{{{HP}}}secPr"))
    before = copy.deepcopy(sec_pr.find(f"{{{HP}}}pagePr"))
    apply_page(sec_pr, page_defaults())
    page = sec_pr.find(f"{{{HP}}}pagePr")
    assert page.attrib == before.attrib
    assert page.find(f"{{{HP}}}margin").attrib == before.find(f"{{{HP}}}margin").attrib
    apply_page(sec_pr, {"page_width_mm": 220, "margin_header_mm": 12,
                       "margin_footer_mm": 13, "margin_bottom_mm": 15})
    assert page.get("width") == "62362"
    margin = page.find(f"{{{HP}}}margin")
    assert margin.get("header") == "3402" and margin.get("footer") == "3685"
    assert margin.get("bottom") == "4252"
    assert is_reference_settings({"preset_id": "appendix-2"})
    assert not is_reference_settings({"preset_id": "legacy"})
    assert not is_reference_settings(None)
