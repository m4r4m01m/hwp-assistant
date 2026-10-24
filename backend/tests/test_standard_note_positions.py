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
    assert '본문 글자·표 어절' in fields['line_break_unit']['options'][0]['label']


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
