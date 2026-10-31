"""Admin organization catalog and role changes, synthetic accounts only."""
import uuid
from test_accounts import accounts, login
from test_workspaces import spaces, scoped_doc
from test_organization import assign


def create_unit(c, name='합성 부서', kind='department', parent=None):
    body=dict(id=str(uuid.uuid4()),name=name,kind=kind,parent_id=parent)
    r=c.post('/api/organization/units',json=body)
    assert r.status_code==200,r.text
    return r.json(),body


def test_division_groups_departments_without_document_space_or_affiliation_changes(spaces):
    c, app, _, _ = spaces
    division, _ = create_unit(c, '합성 디지털본부', 'division')
    dep, _ = create_unit(c, '개발부')
    team, _ = create_unit(c, '서비스팀', 'team', dep['id'])
    before = assign(c, 'writer', dep['id'], team['id']).json()
    moved = c.patch('/api/organization/units/' + dep['id'], json={
        'name': dep['name'], 'parent_id': division['id'], 'expected_revision': dep['revision']})
    assert moved.status_code == 200
    assert moved.json()['parent_id'] == division['id']
    assert c.get('/api/organization/accounts/writer').json() == before
    assert division['id'] not in {x['id'] for x in c.get('/api/workspaces').json()}
    assert c.request('DELETE', '/api/organization/units/' + division['id'], json={'expected_revision': division['revision']}).status_code == 409
    assert c.post('/api/organization/units', json=dict(id=str(uuid.uuid4()), name='잘못된 팀', kind='team', parent_id=division['id'])).status_code == 422
    assert c.patch('/api/organization/units/' + dep['id'], json=dict(name=dep['name'], parent_id=team['id'], expected_revision=moved.json()['revision'])).status_code == 422
    assert c.patch('/api/organization/units/' + dep['id'], json=dict(name=dep['name'], parent_id=None, expected_revision=dep['revision'])).status_code == 409


def test_admin_tree_crud_idempotence_membership_and_document_preservation(spaces):
    c,app,_,_=spaces
    dep,body=create_unit(c)
    assert c.post('/api/organization/units',json=body).json()==dep
    team,_=create_unit(c,'기획팀','team',dep['id'])
    attached=assign(c,'writer',dep['id'],team['id']).json()
    assert c.request('DELETE','/api/organization/units/'+team['id'],json={'expected_revision':team['revision']}).status_code==409
    assert c.request('DELETE','/api/organization/units/'+dep['id'],json={'expected_revision':dep['revision']}).status_code==409
    # A different system admin can manage organization without gaining document access.
    doc=scoped_doc(c,team)
    login(c,'outsider')
    renamed=c.patch('/api/organization/units/'+team['id'],json={'name':'기획전략팀','expected_revision':team['revision']})
    assert renamed.status_code==200,renamed.text
    assert c.get('/api/documents/'+doc['id']).status_code==404
    assert c.patch('/api/organization/units/'+team['id'],json={'name':'오래된 변경','expected_revision':team['revision']}).status_code==409
    assert c.patch('/api/organization/units/'+team['id'],json={'name':'  ','expected_revision':renamed.json()['revision']}).status_code==422
    assert assign(c,'writer',revision=attached['revision']).status_code==200
    assert c.request('DELETE','/api/organization/units/'+team['id'],json={'expected_revision':renamed.json()['revision']}).status_code==409 # document remains
    empty,_=create_unit(c,'빈 부서')
    assert c.request('DELETE','/api/organization/units/'+empty['id'],json={'expected_revision':empty['revision']}).status_code==200
    assert c.request('DELETE','/api/organization/units/'+empty['id'],json={'expected_revision':empty['revision']}).status_code==200
    login(c,'writer')
    assert c.get('/api/organization/units').status_code==403
    assert c.post('/api/organization/units',json={**body,'id':str(uuid.uuid4())}).status_code==403
    assert c.patch('/api/organization/units/'+dep['id'],json={'name':'침입','expected_revision':dep['revision']}).status_code==403
    assert c.request('DELETE','/api/organization/units/'+dep['id'],json={'expected_revision':dep['revision']}).status_code==403


def test_roles_require_admin_fresh_revision_and_keep_last_admin(spaces):
    c,app,_,_=spaces
    def account(user):
        return next(p for p in c.get('/api/admin/accounts').json()['items'] if p['user_id']==user)
    def role(user,value,revision):
        return c.post('/api/admin/accounts/'+user+'/role',json={'role':value,'expected_revision':revision})
    writer=account('writer')
    promoted=role('writer','admin',writer['revision'])
    assert promoted.status_code==200 and promoted.json()['is_admin']
    assert role('writer','member',writer['revision']).status_code==409
    assert role('writer','member',promoted.json()['revision']).status_code==200
    assert role('outsider','member',account('outsider')['revision']).status_code==200
    assert role('manager','member',account('manager')['revision']).status_code==409
    login(c,'writer')
    assert role('writer','admin',writer['revision']).status_code==403
    login(c,'manager')
    guest=account('guest')
    assert role('guest','admin',guest['revision']).status_code==422
