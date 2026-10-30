"""Organizational affiliation does not grant or revoke document access."""
from test_accounts import accounts, login
from test_workspaces import spaces, new_space, add, scoped_doc


def assign(c, user, dep=None, team=None, revision=None):
    return c.put('/api/organization/accounts/'+user, json={
        'department_id': dep, 'team_id': team, 'expected_revision': revision})


def test_primary_affiliation_is_independent_of_shared_access(spaces):
    c, _, _, _ = spaces
    dep, _ = new_space(c)
    team, _ = new_space(c, '기획팀', 'team', dep['id'])
    other, _ = new_space(c, '운영팀', 'team', dep['id'])
    otherdoc = scoped_doc(c, other)
    add(c, team, 'writer', 'editor')
    add(c, other, 'writer', 'reader')
    original_memberships = c.get('/api/workspaces').json()
    result = assign(c, 'writer', dep['id'], team['id'])
    assert result.status_code == 200, result.text
    assert c.get('/api/workspaces').json() == original_memberships
    assert c.get('/api/organization/me').json()['team'] is None  # manager is not in every team
    login(c, 'writer')
    affiliation = c.get('/api/organization/me').json()
    assert affiliation['team']['id'] == team['id']
    assert affiliation['team']['can_access'] is True
    assert affiliation['department']['can_access'] is False  # no inherited membership
    assert c.get('/api/documents/'+otherdoc['id']).status_code == 200
    assert c.get('/api/organization/units').status_code == 403
    assert c.get('/api/organization/accounts/manager').status_code == 403
    assert assign(c, 'writer', dep['id'], other['id']).status_code == 403
    login(c, 'manager')
    cleared = assign(c, 'writer', revision=affiliation['revision'])
    assert cleared.status_code == 200
    login(c, 'writer')
    assert c.get('/api/organization/me').json()['team'] is None
    assert c.get('/api/documents/'+otherdoc['id']).status_code == 200  # grants survive reassignment


def test_affiliation_validation_revision_retry_and_no_access_escalation(spaces):
    c, _, _, _ = spaces
    dep, _ = new_space(c)
    dep2, _ = new_space(c, '다른 부서')
    team, _ = new_space(c, '기획팀', 'team', dep['id'])
    doc = scoped_doc(c, team)
    assert assign(c, 'missing', dep['id'], team['id']).status_code == 422
    assert assign(c, 'reader', team['id']).status_code == 422
    assert assign(c, 'reader', dep2['id'], team['id']).status_code == 422
    assert assign(c, 'reader', team=team['id']).status_code == 422
    first = assign(c, 'reader', dep['id'], team['id']).json()
    assert assign(c, 'reader', dep['id'], team['id']).json() == first  # lost response retry
    assert assign(c, 'reader', dep2['id']).status_code == 409
    second = assign(c, 'reader', dep2['id'], revision=first['revision']).json()
    assert second['department']['id'] == dep2['id']
    assert assign(c, 'reader', dep['id'], team['id'], first['revision']).status_code == 409
    login(c, 'reader')
    assert c.get('/api/documents/'+doc['id']).status_code == 404
    assert c.get('/api/workspaces').json() == []


def test_admin_account_page_includes_affiliation_without_changing_access(spaces):
    from sqlalchemy import event

    c, app, _, _ = spaces
    dep, _ = new_space(c)
    team, _ = new_space(c, '기획팀', 'team', dep['id'])
    add(c, team, 'writer', 'editor')
    first = assign(c, 'writer', dep['id'], team['id']).json()
    second = assign(c, 'reader', dep['id'], team['id']).json()
    original_members = c.get('/api/workspaces/' + team['id'] + '/members').json()

    statements = []
    def collect(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(app.state.engine, 'before_cursor_execute', collect)
    try:
        response = c.get('/api/admin/accounts?status=approved')
    finally:
        event.remove(app.state.engine, 'before_cursor_execute', collect)
    assert response.status_code == 200, response.text
    items = {item['user_id']: item for item in response.json()['items']}
    assert items['writer']['affiliation'] == first
    assert items['reader']['affiliation'] == second
    assert items['writer']['affiliation']['team']['can_access'] is True
    assert items['reader']['affiliation']['team']['can_access'] is False
    assert items['manager']['affiliation'] == {
        'user_id': 'manager', 'department': None, 'team': None, 'revision': None}
    assert items['writer']['revision'] != first['revision']
    assert sum('from account_affiliations' in sql for sql in statements) == 1
    assert sum('from workspaces' in sql for sql in statements) == 1
    assert sum('from workspace_members' in sql for sql in statements) == 1
    assert c.get('/api/workspaces/' + team['id'] + '/members').json() == original_members
    for secret in ('password_hash', 'session_revision', 'employee_number', 'SPACE-writer'):
        assert secret not in response.text

    cleared = assign(c, 'reader', revision=second['revision'])
    assert cleared.status_code == 200
    refreshed = c.get('/api/admin/accounts?status=approved').json()['items']
    assert next(item for item in refreshed if item['user_id'] == 'reader')['affiliation'] == cleared.json()
    empty = c.get('/api/admin/accounts?offset=10000')
    assert empty.status_code == 200 and empty.json()['items'] == []
    assert login(c, 'reader').status_code == 200
    assert c.get('/api/admin/accounts').status_code == 403
