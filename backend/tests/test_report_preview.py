import base64
import copy
import io
import uuid
import zipfile
from xml.etree import ElementTree as E
import pytest
from app import document_standard
from app.report_hwpx import HP, paragraphs
from app.report_layout import lines
from test_reports import report_env, create, ready, seed, synthetic_output
from test_collaboration import secured, as_user


def prepare(client):
    body = create(client); job = ready(client, body)
    url = f"/api/report-tasks/{body['id']}/preview"
    value = {"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"], "seed_base64": base64.b64encode(seed()).decode()}
    result = client.post(url, json=value); assert result.status_code == 200, result.text
    return url, value, result.json()


def render(client, url, plan):
    body = synthetic_output(plan); body.pop("approval_id"); body["preview_id"] = plan["id"]
    result = client.post(url + "/render", json=body); assert result.status_code == 200, result.text
    return body, result.json()


def test_preview_is_not_approval_or_saved_document_and_survives_config_change(report_env, monkeypatch, tmp_path):
    (c, _, _, folder), _ = report_env
    url, value, plan = prepare(c)
    assert c.get('/api/documents').json() == [] and not list(folder.iterdir())
    job = c.get(url.removesuffix('/preview')).json()
    assert job['approval'] is None and 'preview' not in job['execution']
    monkeypatch.setattr(document_standard, 'ASSETS', tmp_path)
    assert c.post(url, json=value).json() == plan
    assert c.get(url).json() == plan
    body, rendered = render(c, url, plan)
    assert c.post(url + '/render', json=body).json() == rendered
    assert c.get('/api/documents').json() == [] and not list(folder.iterdir())
    assert paragraphs(base64.b64decode(rendered['layout_base64']))[1] == [''] + [p['text'] for p in plan['plan']]
    as_user(c, 'bob')
    assert c.get(url).status_code == 404


@pytest.mark.parametrize('damage', ['text','pages','missing'])
def test_preview_requires_fenced_receipts_before_layout(report_env, damage):
    (c, _, _, folder), _ = report_env
    url, _, plan = prepare(c)
    body = synthetic_output(plan); body.pop('approval_id'); body['preview_id'] = plan['id']
    if damage == 'text': body['receipts'][0]['afterTextSha256'] = '0' * 64
    if damage == 'pages': body['receipts'][0]['pageCountAfter'] = 99
    if damage == 'missing': body['receipts'].pop()
    assert c.post(url + '/render', json=body).status_code == 422
    assert 'layout_base64' not in c.get(url).json()
    assert not list(folder.iterdir())


def test_visible_user_edits_save_once_and_retry_is_bound_to_same_bytes(report_env):
    (c, _, _, folder), _ = report_env
    url, _, plan = prepare(c); _, rendered = render(c, url, plan)
    raw = base64.b64decode(rendered['layout_base64'])
    root, _ = paragraphs(raw)
    root[3].find(f'{{{HP}}}run/{{{HP}}}t').text += ' 사용자 직접 수정'
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as z, zipfile.ZipFile(output,'w') as target:
        for entry in z.infolist(): target.writestr(entry, E.tostring(root) if entry.filename == 'Contents/section0.xml' else z.read(entry))
    payload = {'id':str(uuid.uuid4()),'preview_id':plan['id'],'layout_sha256':rendered['layout_sha256'],
               'output_base64':base64.b64encode(output.getvalue()).decode()}
    result = c.post(url+'/save',json=payload);assert result.status_code == 200,result.text
    assert c.post(url+'/save',json=payload).json() == result.json()
    doc = result.json()
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == output.getvalue()
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 1 and len(list(folder.iterdir())) == 1
    assert c.post(url+'/save',json={**payload,'output_base64':rendered['layout_base64']}).status_code == 409
    as_user(c,'bob');assert c.post(url+'/save',json=payload).status_code == 404


def test_saved_preview_retry_reports_frozen_version_after_later_document_save(report_env):
    from test_storage import save
    (client, app, _, folder), _ = report_env
    url, _, plan = prepare(client)
    _, rendered = render(client, url, plan)
    output = base64.b64decode(rendered['layout_base64'])
    payload = {'id': str(uuid.uuid4()), 'preview_id': plan['id'],
               'layout_sha256': rendered['layout_sha256'], 'output_base64': rendered['layout_base64']}
    first = client.post(url + '/save', json=payload)
    assert first.status_code == 200, first.text
    document = first.json()
    token = client.post(f"/api/documents/{document['id']}/session").json()['token']
    later = save(client, document, token, data=output)
    assert later.status_code == 200, later.text
    later_version = later.json()['id']
    assert later_version != document['current_version']
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    retry = client.post(url + '/save', json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()['id'] == document['id']
    assert retry.json()['current_version'] == document['current_version']
    assert client.get('/api/documents').json()[0]['current_version'] == later_version
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before

    # A previously committed receipt still requires current owner access.
    from app.db import Access, transaction
    with transaction(app.state.engine) as db:
        db.get(Access, (document['id'], 'alice')).role = 'reader'
    assert client.post(url + '/save', json=payload).status_code == 403
    as_user(client, 'bob')
    assert client.post(url + '/save', json=payload).status_code == 404


def test_preview_save_commit_failure_preserves_unapproved_draft_and_same_retry(report_env):
    from sqlalchemy import event
    (client, app, _, _), _ = report_env
    url, _, plan = prepare(client)
    _, rendered = render(client, url, plan)
    payload = {'id': str(uuid.uuid4()), 'preview_id': plan['id'],
               'layout_sha256': rendered['layout_sha256'], 'output_base64': rendered['layout_base64']}

    def fail_commit(_):
        raise RuntimeError('injected preview save commit failure')

    event.listen(app.state.engine, 'commit', fail_commit)
    try:
        assert client.post(url + '/save', json=payload).status_code == 500
    finally:
        event.remove(app.state.engine, 'commit', fail_commit)
    assert client.get('/api/documents').json() == []
    assert client.get(url.removesuffix('/preview')).json()['approval'] is None
    assert client.get(url).json() == rendered
    result = client.post(url + '/save', json=payload)
    assert result.status_code == 200, result.text
    assert client.post(url + '/save', json=payload).json() == result.json()
    document = result.json()
    assert len(client.get(f"/api/documents/{document['id']}/versions").json()) == 1
    assert client.get(f"/api/documents/{document['id']}/versions/{document['current_version']}/file").content == base64.b64decode(rendered['layout_base64'])


def test_balanced_title_and_word_wrapping_preserve_all_characters():
    title = '금융권 해킹 동향 및 90일 대응계획 경영진 보고'
    parts = lines(title, 21, title=True)
    assert len(parts) == 2 and all(len(p.strip()) > 8 for p in parts)
    assert ''.join(parts) == title
    text = '□ (요약) 고객정보 보호와 업무 연속성을 함께 점검할 필요'
    parts = lines(text, 16)
    assert ''.join(parts) == text
    assert all(p.endswith(' ') for p in parts[:-1])


@pytest.mark.parametrize('marker,limit', [('□',29),('ㅇ',28),('○',28),('-',27)])
def test_long_outline_items_fit_three_lines_without_dropping_facts(marker, limit):
    import math
    from app.report_outline import split_bullet
    from app.report_layout import width
    text = f'{marker} (대응) ' + '고객정보 보호와 업무 연속성을 함께 점검하며 담당자와 완료 기한을 확인할 필요. ' * 7
    parts = split_bullet(text)
    assert len(parts) > 2 and all(p.startswith(marker + ' ') for p in parts)
    assert ''.join(p[2:] for p in parts) == text[2:]
    assert all(sum(max(1,math.ceil(width(line.rstrip())/limit)) for line in lines(p,limit)) <= 3 for p in parts)


def test_new_preview_splits_long_square_without_changing_draft_or_frozen_plan(report_env, monkeypatch):
    from test_reports import DRAFT
    from app import reports
    value = copy.deepcopy(DRAFT)
    value['summary'] = '□ (요약) ' + '담당자와 완료 기한을 확인하며 보호 조치와 검증 증거를 함께 관리할 필요. ' * 6
    value['sections'][0]['paragraphs'] = ['□ 핵심 판단임'] * 6
    monkeypatch.setattr(reports, 'generate_report', lambda *args: (value, {'completed':True}))
    (client, _, _, _), _ = report_env
    url, request, plan = prepare(client)
    assert len([p for p in plan['plan'] if p['text'].startswith('□ ')]) > 7
    assert client.get(url.removesuffix('/preview')).json()['draft']['summary'] == value['summary']
    monkeypatch.setattr('app.report_preview.report_blocks', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must reuse frozen plan')))
    assert client.post(url,json=request).json() == plan
