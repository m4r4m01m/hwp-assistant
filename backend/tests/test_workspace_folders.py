import uuid
from fastapi.testclient import TestClient
from app.db import transaction
from app.workspaces import WorkspaceMember
from test_accounts import accounts, login
from test_workspaces import spaces, new_space, add, scoped_doc, affiliate

def make(c,space,name='공유 폴더',parent=None,identity=None,revision=None):
    return c.put(f"/api/workspaces/{space['id']}/folders/{identity or uuid.uuid4()}",json={'name':name,'parent_id':parent,'expected_revision':revision})

def test_shared_folder_paths_membership_revision_and_document_preservation(spaces):
    c,app,_,_=spaces
    dep,_=new_space(c);team,_=new_space(c,'팀','team',dep['id']);other,_=new_space(c,'다른 부서')
    add(c,team,'writer','editor');add(c,team,'reader','reader')
    doc=scoped_doc(c,team);url='/api/documents/'+doc['id'];versions=c.get(url+'/versions').json()
    parent=make(c,team).json();child=make(c,team,'하위',parent['id']).json()
    move={'workspace_id':team['id'],'folder_id':child['id'],'expected_folder_id':None}
    assert c.put(url+'/workspace-folder',json=move).status_code==200
    assert c.put(url+'/workspace-folder',json=move).status_code==200
    assert c.get(url+'/versions').json()==versions
    with TestClient(app,base_url='https://testserver') as user:
        login(user,'writer');assert any(f['id']==child['id'] for f in user.get('/api/folders').json())
        listed=user.get(url).json();assert listed['workspace_folder_id']==child['id']
        assert listed['workspace_folder_path']==['공유 폴더','하위']
        changed=make(user,team,'변경',parent['id'],child['id'],child['revision']);assert changed.status_code==200
        assert make(c,team,'오래된 이름',parent['id'],child['id'],child['revision']).status_code==409
        assert make(c,other,'가로채기',None,child['id'],child['revision']).status_code==404
        assert make(c,team,'잘못된 부모',make(c,other).json()['id']).status_code==404
        login(user,'reader');assert make(user,team).status_code==403
        assert user.put(url+'/workspace-folder',json={**move,'folder_id':None,'expected_folder_id':child['id']}).status_code==403
        assert user.request('DELETE',f"/api/workspaces/{team['id']}/folders/{child['id']}",json={'expected_revision':changed.json()['revision']}).status_code==403
        login(user,'outsider');assert not user.get('/api/folders').json()
        assert make(user,team).status_code==404
        assert c.put(url+'/access',json={'user_id':'outsider','role':'reader'}).status_code==200
        assert user.get(url).json()['workspace_folder_path']==['공유 폴더','변경']
        assert not user.get('/api/folders').json() # invitation never exposes siblings
    assert c.put(url+'/workspace-folder',json={**move,'folder_id':parent['id'],'expected_folder_id':None}).status_code==409
    assert c.request('DELETE',f"/api/workspaces/{team['id']}/folders/{parent['id']}",json={'expected_revision':parent['revision']}).status_code==409
    fresh=next(f for f in c.get('/api/folders').json() if f['id']==child['id'])
    assert c.request('DELETE',f"/api/workspaces/{team['id']}/folders/{child['id']}",json={'expected_revision':fresh['revision']}).status_code==200
    assert c.get(url).json()['workspace_folder_id'] is None
    assert c.get(url+'/versions').json()==versions
    assert c.put(url+'/workspace-folder',json={**move,'folder_id':parent['id']}).status_code==200
    affiliate(c,other)
    assert c.put(url+'/workspace',json={'workspace_id':other['id'],'expected_version':doc['current_version']}).status_code==200
    assert c.get(url).json()['workspace_folder_id'] is None
    assert c.put(url+'/workspace-folder',json=move).status_code==409

def test_shared_folder_depth_and_membership_revocation(spaces):
    c,app,_,_=spaces;space,_=new_space(c);root=make(c,space).json();parent=root
    for i in range(7):parent=make(c,space,str(i),parent['id']).json()
    assert make(c,space,'deep',parent['id']).status_code==409
    assert make(c,space,'cycle',parent['id'],root['id'],root['revision']).status_code==409
    assert make(c,space).status_code==409
    add(c,space,'writer','editor');login(c,'writer')
    assert len(c.get('/api/folders').json())==8
    with transaction(app.state.engine) as db:db.delete(db.get(WorkspaceMember,(space['id'],'writer')))
    assert c.get('/api/folders').json()==[]
    assert make(c,space,'after revoke').status_code==404
