import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.db import DesignFragment, transaction
from app.reference_standard import defaults
from app.standard_policy import cover_enabled, toc_mode, table_geometry
from app.standard_samples import SAMPLE, SAMPLE_V4
from app.report_contract import report_blocks
from test_collaboration import secured, as_user, TOKENS
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
    assert c.post('/api/design-fragments', json=body).status_code == 404
    assert c.delete('/api/design-fragments/' + body['id']).status_code == 404
    as_user(c, 'alice')
    assert c.delete('/api/design-fragments/' + body['id']).status_code == 200
    assert c.get('/api/design-fragments').json()['items'] == []


@pytest.mark.parametrize('second_user', ['alice', 'bob'])
def test_concurrent_fragment_registration_has_one_owner_and_stable_receipt(secured, second_user):
    c, _, _, _ = secured
    body = dict(id=str(uuid.uuid4()), name='동시 저장 표', kind='table', settings=defaults())
    users = ['alice', second_user]
    barrier = Barrier(2)

    def submit(user):
        barrier.wait(timeout=10)
        return c.post('/api/design-fragments', json=body, headers={'Authorization': 'Bearer ' + TOKENS[user]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, users))
    if second_user == 'alice':
        assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
        assert responses[0].json() == responses[1].json()
    else:
        assert sorted(r.status_code for r in responses) == [200, 404], [r.text for r in responses]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
    saved = responses[winner].json()
    for user in ('alice', 'bob'):
        headers = {'Authorization': 'Bearer ' + TOKENS[user]}
        expected = [saved] if user == users[winner] else []
        assert c.get('/api/design-fragments', headers=headers).json()['items'] == expected
    assert c.post('/api/design-fragments', json={**body, 'name': '다른 이름'},
                  headers={'Authorization': 'Bearer ' + TOKENS[users[winner]]}).status_code == 409


@pytest.mark.parametrize('hidden', [False, True])
def test_local_fragment_migration_requires_owner_and_preserves_design(secured, monkeypatch, hidden):
    from app.schema import migrate
    c, app, _, _ = secured
    body = dict(id=str(uuid.uuid4()), name='이전 작업 디자인', kind='title', settings=defaults())
    response = c.post('/api/design-fragments', json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    as_user(c, 'bob')
    other = c.post('/api/design-fragments', json={**body, 'id': str(uuid.uuid4())}).json()
    as_user(c, 'alice')
    with transaction(app.state.engine) as db:
        row = db.get(DesignFragment, body['id'])
        row.owner_id = 'local'
        row.hidden = hidden
        original = (row.name, row.kind, row.settings, row.created_at, row.hidden)
    monkeypatch.delenv('HWP_LEGACY_OWNER', raising=False)
    with pytest.raises(ValueError, match='HWP_LEGACY_OWNER.*design fragments'):
        migrate(app.state.engine, {'alice': 'configured', 'bob': 'configured'})
    with transaction(app.state.engine) as db:
        assert db.get(DesignFragment, body['id']).owner_id == 'local'
    monkeypatch.setenv('HWP_LEGACY_OWNER', 'alice')
    migrate(app.state.engine, {'alice': 'configured', 'bob': 'configured'})
    with transaction(app.state.engine) as db:
        row = db.get(DesignFragment, body['id'])
        assert row.owner_id == 'alice'
        assert (row.name, row.kind, row.settings, row.created_at, row.hidden) == original
        assert json.loads(row.settings) == saved['settings']
        assert db.get(DesignFragment, other['id']).owner_id == 'bob'
    assert c.get('/api/design-fragments').json()['items'] == ([] if hidden else [saved])


@pytest.mark.parametrize('legacy_owner', [None, 'alice'])
def test_configured_local_identity_keeps_its_design_fragments(secured, monkeypatch, legacy_owner):
    from app.schema import migrate
    c, app, _, _ = secured
    body = dict(id=str(uuid.uuid4()), name='로컬 디자인', kind='table', settings=defaults())
    assert c.post('/api/design-fragments', json=body).status_code == 200
    with transaction(app.state.engine) as db:
        db.get(DesignFragment, body['id']).owner_id = 'local'
    if legacy_owner:
        monkeypatch.setenv('HWP_LEGACY_OWNER', legacy_owner)
    else:
        monkeypatch.delenv('HWP_LEGACY_OWNER', raising=False)
    migrate(app.state.engine, {'alice': 'configured', 'local': 'configured'})
    with transaction(app.state.engine) as db:
        assert db.get(DesignFragment, body['id']).owner_id == 'local'


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
