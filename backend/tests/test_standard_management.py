import base64
import uuid
import pytest
from app.reference_standard import defaults
from app.standard_policy import cover_enabled, toc_mode, table_geometry
from app.standard_samples import SAMPLE, SAMPLE_V4
from app.report_contract import report_blocks
from test_collaboration import secured, as_user
from test_standard_samples import prepare, finish, registration


def test_standard_replace_archive_preserve_history_and_retry(secured):
    c, _, _, _ = secured
    _, stage = prepare(c)
    original_body = registration(finish(c, stage))
    original = c.post('/api/visual-standards/sample-register', json=original_body).json()
    original_bytes = c.get(f"/api/visual-standards/{original['id']}/file").content
    _, stage = prepare(c, {**defaults(), 'body_tracking_percent': 2})
    body = {**registration(finish(c, stage)), 'replaces': original['id']}
    new = c.post('/api/visual-standards/sample-register', json=body)
    assert new.status_code == 200, new.text
    assert c.post('/api/visual-standards/sample-register', json=body).json() == new.json()
    assert c.post('/api/visual-standards/sample-register', json={**body, 'id': str(uuid.uuid4())}).status_code == 409
    assert [i['id'] for i in c.get('/api/visual-standards').json()['items']] == [new.json()['id']]
    assert c.get(f"/api/visual-standards/{original['id']}").json() == original
    assert c.get(f"/api/visual-standards/{original['id']}/file").content == original_bytes
    as_user(c, 'bob')
    assert c.delete('/api/visual-standards/' + new.json()['id']).status_code == 404
    as_user(c, 'alice')
    assert c.delete('/api/visual-standards/' + new.json()['id']).status_code == 200
    assert c.delete('/api/visual-standards/' + new.json()['id']).status_code == 200
    assert c.get('/api/visual-standards').json()['items'] == []
    assert c.get('/api/visual-standards/' + new.json()['id']).json() == new.json()


def test_fragments_are_owned_and_keep_only_design_fields(secured):
    c, _, _, _ = secured
    body = dict(id=str(uuid.uuid4()), name='검수용 표', kind='table', settings={**defaults(), 'table_anchor_role': 'circle'})
    result = c.post('/api/design-fragments', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['settings']['table_anchor_role'] == 'circle'
    assert 'preset_id' not in result.json()['settings']
    assert 'margin_left_mm' not in result.json()['settings']
    assert c.post('/api/design-fragments', json=body).json() == result.json()
    assert c.post('/api/design-fragments', json={**body, 'name': '다른 이름'}).status_code == 409
    as_user(c, 'bob')
    assert c.get('/api/design-fragments').json()['items'] == []
    assert c.delete('/api/design-fragments/' + body['id']).status_code == 404
    as_user(c, 'alice')
    assert c.delete('/api/design-fragments/' + body['id']).status_code == 200
    assert c.get('/api/design-fragments').json()['items'] == []


@pytest.mark.parametrize('preset,cover,toc', [('appendix-1',False,'NONE'),('appendix-2',True,'SEPARATE'),('appendix-3',True,'COVER')])
def test_original_compositions_and_custom_overrides(secured,preset,cover,toc):
    c, _, _, _ = secured
    c._transport.raise_server_exceptions = True
    settings = defaults(preset)
    assert cover_enabled(settings) == cover and toc_mode(settings) == toc
    custom = {**settings, 'cover_mode':'OFF', 'toc_mode':'SEPARATE', 'reference_list':'ALL'}
    _, stage = prepare(c, custom)
    assert stage['needs_toc'] is True
    assert finish(c, stage)['verification']['converged']
    plan = report_blocks(SAMPLE, rich=True, visual_settings=custom, cover={})
    assert not plan[0].get('cover_layout')
    assert any(item['text'] == '참고자료' for item in plan)
    _, stage = prepare(c, {**settings, 'cover_mode':'ON', 'toc_mode':'NONE'})
    assert stage['needs_toc'] is False
    finish(c, stage)


def test_short_notes_wrap_sample_and_table_alignment():
    from app.report_contract import presentation_text
    assert presentation_text('설명 [S1] [S2]', {'citation_style':'NOTE_SHORT'}, {'S1','S2'}, marker_numbers={'S1':1,'S2':2}) == '설명 주1), 2)'
    plan = report_blocks(SAMPLE, rich=True, visual_settings={**defaults(), 'citation_style':'NOTE_SHORT'})
    notes = [p for p in plan if p.get('note_source_id')]
    assert any(p['text'].startswith('주1) ') for p in notes)
    assert any(p['text'].startswith('2) ') for p in notes)
    assert not any(p['text'].startswith('주2) ') for p in notes)
    assert SAMPLE_V4['sections'][2]['paragraphs'] == SAMPLE['sections'][2]['paragraphs'][:2]
    long = next(p for p in SAMPLE['sections'][2]['paragraphs'] if '강제 개행 없이' in p)
    assert '\n' not in long and len(long) > 140
    settings = {**defaults(), 'circle_left_indent_pt': 30, 'circle_first_line_indent_pt': -12,
                'table_anchor_role':'circle', 'table_anchor_percent':50}
    assert table_geometry(settings, 480) == (24, 456)


def test_new_native_wrapping_does_not_rewrite_frozen_layout():
    import copy
    from app.standard_samples import materialize
    from app.report_hwpx import scaffold, paragraphs, HP
    from app.report_rich import layout_rich_report
    from test_reports import seed
    from app.block_hwpx import parsed
    from app.toc_hwpx import packed
    settings = defaults()
    plan = report_blocks(SAMPLE, concise=True, rich=True, visual_settings=settings)
    assert plan[0]['native_wrap'] == 'reference-native-wrap-v1'
    raw, frozen = scaffold(seed(), plan, visual_settings=settings)
    header, section, _, _ = parsed(raw)
    for item in frozen:
        section[item['paragraph']].find(f'{{{HP}}}run/{{{HP}}}t').text = item['text']
    filled = packed(raw, header, section)
    old = copy.deepcopy(frozen)
    old[0].pop('native_wrap')
    legacy = layout_rich_report(filled, old, visual_settings=settings)
    native = layout_rich_report(filled, frozen, visual_settings=settings)
    old_section = parsed(legacy)[1]
    new_section = parsed(native)[1]
    assert len(list(old_section.iter(f'{{{HP}}}lineBreak'))) > len(list(new_section.iter(f'{{{HP}}}lineBreak')))
    assert [''.join(p.itertext()) for p in old_section.iter(f'{{{HP}}}p')] == [''.join(p.itertext()) for p in new_section.iter(f'{{{HP}}}p')]
