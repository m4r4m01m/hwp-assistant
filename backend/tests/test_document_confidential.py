import uuid
from fastapi.testclient import TestClient
from test_accounts import accounts, login
from test_workspaces import spaces, new_space, add, scoped_doc
from test_document_library import fresh


def test_confidential_access_is_explicit_not_team_or_admin_and_history_is_unchanged(spaces):
    c, app, _, _ = spaces
    team, _ = new_space(c)
    add(c, team, 'writer', 'editor')
    doc = fresh(c); path = '/api/documents/' + doc['id']
    versions = c.get(path + '/versions').json()
    payload = dict(enabled=True, expected_revision=None, expected_version=doc['current_version'])
    result = c.put(path + '/confidential', json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['confidential']
    assert c.put(path + '/confidential', json=payload).json() == result.json()
    assert c.get(path).json()['confidential']
    assert c.put(path + '/workspace', json=dict(workspace_id=team['id'], expected_version=doc['current_version'])).status_code == 409
    assert c.post(path + '/copies', json=dict(document_id=str(uuid.uuid4()), source_version=doc['current_version'], request_id=str(uuid.uuid4()), name='사본')).status_code == 409
    with TestClient(app, base_url='https://testserver') as other:
        for user in ['writer', 'outsider']:
            assert login(other, user).status_code == 200
            assert not other.get('/api/documents').json()
            assert other.get(path).status_code == 404
            assert other.get(path + '/versions/' + doc['current_version'] + '/file').status_code == 404
            assert other.post(path + '/session').status_code == 404
        assert c.put(path + '/access', json=dict(user_id='writer', role='reader')).status_code == 200
        login(other, 'writer')
        assert other.get(path).status_code == 200
        assert other.get('/api/documents').json()[0]['confidential']
        assert other.get(path + '/versions/' + doc['current_version'] + '/file').status_code == 200
        assert other.put(path + '/confidential', json=payload).status_code == 403
        assert c.delete(path + '/access/writer').status_code == 200
        assert other.get(path).status_code == 404
    assert c.get(path + '/versions').json() == versions


def test_confidential_revision_fences_and_disable_never_publish(spaces):
    c, app, _, _ = spaces
    doc = fresh(c); path = '/api/documents/' + doc['id']
    payload = dict(enabled=True, expected_revision=None, expected_version=doc['current_version'])
    assert c.put(path + '/confidential', json={**payload, 'expected_version':str(uuid.uuid4())}).status_code == 409
    enabled = c.put(path + '/confidential', json=payload).json()
    assert c.put(path + '/confidential', json={**payload, 'enabled':False}).status_code == 409
    disabled = c.put(path + '/confidential', json={**payload, 'enabled':False, 'expected_revision':enabled['confidential_revision']})
    assert disabled.status_code == 200
    assert not disabled.json()['confidential'] and disabled.json()['personal_document']
    assert c.put(path + '/confidential', json=payload).status_code == 409
    login(c, 'outsider')
    assert c.get(path).status_code == 404


def test_shared_document_cannot_be_marked_without_explicit_private_move(spaces):
    c, _, _, _ = spaces
    team, _ = new_space(c); add(c, team, 'writer', 'editor')
    doc = scoped_doc(c, team); path = '/api/documents/' + doc['id']
    payload = dict(enabled=True, expected_revision=None, expected_version=doc['current_version'])
    assert c.put(path + '/confidential', json=payload).status_code == 409
    login(c, 'writer')
    assert c.get(path).status_code == 200
