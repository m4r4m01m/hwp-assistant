"""New appendix geometry defaults; explicit historical settings retain ownership."""
import copy

import pytest

from app.document_standard import HP, HH, HC, load_standard
from app.reference_report import report_styles
from app.reference_source import role_defaults
from app.reference_standard import (
    REPORT_DEFAULT_OVERRIDES, VisualSettingsV3, defaults, source_metadata,
)
from test_reference_report import generated, parts


def _table_geometry(raw):
    _, section = parts(raw)
    page = section.find(f".//{{{HP}}}pagePr")
    margin = page.find(f"{{{HP}}}margin")
    available = int(page.get("width")) - int(margin.get("left")) - int(margin.get("right"))
    # The data table has two columns; the title frame has one.
    table = next(node for node in section.iter(f"{{{HP}}}tbl") if node.get("colCnt") == "2")
    return available, table


@pytest.mark.parametrize("preset_id", ["appendix-1", "appendix-2", "appendix-3"])
@pytest.mark.parametrize("margins", [{}, {"margin_left_mm": 25, "margin_right_mm": 30}])
def test_new_table_fills_paper_body_width_and_cell_widths_sum_exactly(preset_id, margins):
    settings = {**defaults(preset_id), **margins}
    raw, _ = generated(settings)
    available, table = _table_geometry(raw)
    assert settings["table_width_percent"] == 100
    assert int(table.find(f"{{{HP}}}sz").get("width")) == available
    for row in table.findall(f"{{{HP}}}tr"):
        assert sum(int(cell.find(f"{{{HP}}}cellSz").get("width")) for cell in row) == available


@pytest.mark.parametrize("percent", [62.060593, 80])
def test_stored_or_user_selected_table_width_is_not_promoted_to_new_default(percent):
    settings = {**defaults(), "table_width_percent": percent}
    frozen = VisualSettingsV3.model_validate(settings).model_dump(mode="json")
    snapshot = copy.deepcopy(frozen)
    raw, _ = generated(frozen)
    available, table = _table_geometry(raw)
    assert int(table.find(f"{{{HP}}}sz").get("width")) == round(available * percent / 100)
    assert frozen == snapshot
    assert VisualSettingsV3.model_validate(frozen).model_dump(mode="json") == snapshot


@pytest.mark.parametrize("historical", [False, True])
def test_hierarchical_native_hanging_indents_and_continuations_respect_selected_values(historical):
    settings = defaults()
    if historical:
        # Registered V3 standards and frozen request snapshots already contain
        # explicit zero values; validating them must not apply new defaults.
        settings.update({key: 0 for key in REPORT_DEFAULT_OVERRIDES})
    settings = VisualSettingsV3.model_validate(settings).model_dump(mode="json")
    _, header, _, styles = report_styles(settings)
    paras = {node.get("id"): node for node in header.iter(f"{{{HH}}}paraPr")}
    assert [settings[role + "_left_indent_pt"] for role in ("square", "circle", "dash")] == [24.75, 33, 43]
    for role in ("square", "circle", "dash"):
        left = round(settings[role + "_left_indent_pt"] * 100)
        intent = round(settings[role + "_first_line_indent_pt"] * 100)
        assert intent == (0 if historical else round(REPORT_DEFAULT_OVERRIDES[role + "_first_line_indent_pt"] * 100))
        for name, expected_left, expected_intent in ((role, left, intent),
                (role + "_continuation", left + max(0, -intent), 0)):
            para = paras[styles[name][1]]
            for branch in para.find(f"{{{HP}}}switch"):
                factor = 2 if branch.tag.endswith("default") else 1
                margins = branch.find(f"{{{HH}}}margin")
                assert margins.find(f"{{{HC}}}left").get("value") == str(expected_left * factor)
                assert margins.find(f"{{{HC}}}intent").get("value") == str(expected_intent * factor)


def test_original_values_and_bytes_are_preserved_and_new_defaults_are_attributed():
    before = load_standard()[1]
    original = role_defaults()
    assert all(original[key] == 0 for key in REPORT_DEFAULT_OVERRIDES)
    provenance = source_metadata()["structural_provenance"]
    assert provenance["table_width_percent"]["source_percent"] == 62.060593
    assert provenance["table_width_percent"]["default_percent"] == 100
    assert provenance["bullet_hanging_indents"]["defaults"] == REPORT_DEFAULT_OVERRIDES
    assert load_standard()[1] == before
