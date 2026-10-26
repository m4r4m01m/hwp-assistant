import copy
import re
import pytest
from app.reference_standard import defaults, field_catalog, VisualSettingsV3
from app.report_contract import report_blocks, report_hash
from app.report_runner import writing_prompt
from app.standard_samples import SAMPLE, SAMPLE_V5


@pytest.mark.parametrize('style,inline,bottom', [
    ('NOTE', ['주1)', '주2)'], ['주1)', '2)']),
    ('AUTO', ['주1)', '주2)'], ['주1)', '2)']),
    ('STARS', ['*', '**'], ['*', '**']),
])
def test_bottom_abbreviation_never_shortens_inline_marks(style, inline, bottom):
    settings = {**defaults(), 'citation_style': style, 'citation_note_abbreviated': True}
    draft = copy.deepcopy(SAMPLE)
    plan = report_blocks(draft, rich=True, visual_settings=settings)
    target = next(p for p in plan if p['text'].startswith('□ 문서명과 담당 부서'))
    assert f"부서{inline[0]}를" in target['text']
    assert f"결과{inline[1]}를" in target['text']
    assert [target['text'][a:b] for a,b in target['citation_ranges']] == inline
    notes = [p for p in plan if p.get('note_source_id') and p.get('note_scope') == target['note_scope']]
    assert [p['note_marker'] for p in notes] == bottom
    assert not any(p.get('citation_ranges') for p in notes)
    assert SAMPLE == draft


def test_legacy_hash_and_new_option_are_separate():
    settings = defaults()
    assert report_hash(VisualSettingsV3.model_validate(settings).model_dump(mode='json')) == 'e9895a9204a8ee1e144d65cc038df3f115d4d879d08e5354cafa2e928cb355dc'
    assert 'citation_note_abbreviated' not in VisualSettingsV3.model_validate(settings).model_dump()
    assert VisualSettingsV3.model_validate({**settings,'citation_note_abbreviated':True}).model_dump()['citation_note_abbreviated'] is True
    prompt = writing_prompt({'visual_standard':{'revision':3,'settings':settings}})
    assert 'A는 B[S1]이다.' in prompt


def test_word_anchor_sample_retains_facts_and_original_values_are_visible():
    normalize = lambda text: re.sub(r'\s+|\[S\d+\]', '', text)
    for old, new in zip(SAMPLE_V5['sections'], SAMPLE['sections']):
        assert list(map(normalize, old['paragraphs'])) == list(map(normalize, new['paragraphs']))
    assert SAMPLE['sources'] == SAMPLE_V5['sources']
    fields = {f['key']:f for f in field_catalog()}
    assert '0%' in fields['line_space_policy']['options'][0]['label']
    assert fields['line_break_unit']['options'][0] == {'value': 'ORIGINAL', 'label': '기준서 값 · 역할별 줄바꿈'}
    assert fields['line_break_unit']['option_aliases'] == {}  # Mixed source roles cannot become one global wrap mode.


def test_repeated_note_explanations_have_stable_individual_preview_anchors():
    from app.standard_samples import sample_regions
    def anchors(abbreviated):
        settings = {**defaults(), 'citation_style':'NOTE', 'citation_note_abbreviated':abbreviated}
        plan = report_blocks(SAMPLE, rich=True, visual_settings=settings)
        return [a for a in sample_regions(plan, settings, False) if a.get('id')]
    before, after = anchors(False), anchors(True)
    assert [a['id'] for a in before] == [a['id'] for a in after]
    assert len({a['id'] for a in before}) == len(before)
    repeated = [a for a in before if a['texts'] == before[0]['texts']]
    assert len(repeated) > 1
    assert [a['occurrence'] for a in repeated] == list(range(len(repeated)))


@pytest.mark.parametrize('ratio,spacing', [(100, 0), (90, -5), (110, 10)])
def test_abbreviated_explanation_digits_align_with_the_full_label_using_native_indents(ratio, spacing):
    from test_reference_body_spacing import generated, paragraph_named
    from test_reference_report import parts
    from test_report_local_notes import DRAFT
    settings = {**defaults(), 'citation_style': 'NOTE', 'citation_note_abbreviated': True}
    for role in ('note', 'table_source'):
        settings.update({role + '_ratio_percent': ratio, role + '_spacing_percent': spacing})
    raw, plan = generated(draft=DRAFT, settings=settings)
    header, section = parts(raw)
    notes = [item for item in plan if item.get('note_source_id')]
    assert {item['style_kind'] for item in notes if item.get('note_omitted_prefix')} == {'note', 'table_source'}
    for note in notes:
        if not note.get('note_omitted_prefix'):
            continue
        first = next(item for item in notes if item['note_scope'] == note['note_scope'])
        first_margin = paragraph_named(header, section, first['text'])[1]
        margin = paragraph_named(header, section, note['text'])[1]
        expected_advance = settings[note['style_kind'] + '_size_pt'] * (ratio + spacing) / 100
        assert margin['left'] - first_margin['left'] == pytest.approx(expected_advance, abs=.01)
        assert note['note_marker'] == '2)'
        assert not note['text'].startswith(' ')  # No padding characters in document content.
        assert first['note_marker'] == '주1)'


@pytest.mark.parametrize('first_indent', [-18, 0, 8])
def test_abbreviated_labels_keep_custom_note_continuation_columns(first_indent):
    from test_reference_body_spacing import generated, paragraph_named
    from test_reference_report import parts
    settings = {'citation_style': 'NOTE', 'citation_note_abbreviated': True,
                'note_left_indent_pt': 25, 'note_first_line_indent_pt': first_indent}
    raw, plan = generated(draft=SAMPLE, settings=settings)
    header, section = parts(raw)
    abbreviated = next(item for item in plan if item.get('note_omitted_prefix') and item['style_kind'] == 'note')
    margin = paragraph_named(header, section, abbreviated['text'])[1]
    first_line = margin['left'] + max(0, margin['intent'])
    continuation = margin['left'] + max(0, -margin['intent'])
    assert first_line == pytest.approx(25 + max(0, first_indent) + defaults()['note_size_pt'])
    assert continuation == pytest.approx(25 + max(0, -first_indent))


def test_frozen_abbreviated_plan_without_new_metadata_retains_its_original_geometry(monkeypatch):
    from app import report_notes
    from test_reference_body_spacing import generated, paragraph_named
    from test_reference_report import parts
    original = report_notes.localize_notes

    def legacy_plan(*args, **kwargs):
        result = original(*args, **kwargs)
        for item in result:
            item[3].pop('note_omitted_prefix', None)
        return result

    monkeypatch.setattr(report_notes, 'localize_notes', legacy_plan)
    raw, plan = generated(draft=SAMPLE, settings={'citation_style': 'NOTE', 'citation_note_abbreviated': True})
    header, section = parts(raw)
    second = next(item for item in plan if item.get('note_marker') == '2)')
    first = next(item for item in plan if item.get('note_scope') == second['note_scope'] and item.get('note_marker') == '주1)')
    assert paragraph_named(header, section, second['text'])[1]['left'] == paragraph_named(header, section, first['text'])[1]['left']
    assert all('note_omitted_prefix' not in item for item in plan)
