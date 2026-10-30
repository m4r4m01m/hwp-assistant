import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import Version, transaction
from app.document_library import PersonalDocument
from test_workspaces import spaces, new_space, add, scoped_doc
from test_accounts import accounts, login
from test_storage import create, package

def fresh(c):
    doc,token=create(c,package('개인 계획'))
    c.delete(f'/api/documents/{doc["id"]}/session',headers={'X-Edit-Session':token})
    return doc

def folder(c,name='계획',parent=None,id=None):
    return c.put('/api/folders/'+(id or str(uuid.uuid4())),json={'name':name,'parent_id':parent})

def test_new_document_private_in_shared_mode_old_document_unchanged(spaces):
    c,app,_,_=spaces
    old=fresh(c)
    with transaction(app.state.engine) as db: db.delete(db.get(PersonalDocument,old['id']))
    new=fresh(c)
    assert login(c,'outsider').status_code==200
    assert [d['id'] for d in c.get('/api/documents').json()]==[old['id']]
    assert c.get(f'/api/documents/{new["id"]}').status_code==404
    assert c.post(f'/api/documents/{new["id"]}/session').status_code==404
    assert c.delete(f'/api/documents/{new["id"]}',headers={'Idempotency-Key':str(uuid.uuid4())}).status_code==404

def test_private_sharing_folders_and_revocation_keep_document_history(spaces):
    c,app,_,_=spaces
    doc=fresh(c); path=f'/api/documents/{doc["id"]}'
    assert c.put(path+'/access',json={'user_id':'writer','role':'reader'}).status_code==200
    owner_folder=folder(c).json()
    assert c.put(path+'/folder',json={'folder_id':owner_folder['id']}).status_code==200
    with TestClient(app,base_url='https://testserver') as recipient:
        assert login(recipient,'writer').status_code==200
        listed=recipient.get('/api/documents').json()[0]
        assert listed['personal_document'] and listed['individually_shared'] and not listed['owned_by_me']
        assert listed['folder_id'] is None and not listed['can_trash']
        assert recipient.get('/api/folders').json()==[]
        assert recipient.put(path+'/folder',json={'folder_id':owner_folder['id']}).status_code==404
        own=folder(recipient,'共有資料').json()
        assert recipient.put(path+'/folder',json={'folder_id':own['id']}).status_code==200
        assert c.get('/api/documents').json()[0]['folder_id']==owner_folder['id']
        before=c.get(path+'/versions').json()
        assert recipient.delete('/api/folders/'+own['id']).status_code==200
        assert c.get(path+'/versions').json()==before
        assert c.delete(path+'/access/writer').status_code==200
        assert recipient.get('/api/documents').json()==[]
        assert recipient.put(path+'/folder',json={'folder_id':None}).status_code==404

def test_nested_folder_cycle_cross_owner_and_nonempty_folder_deletion(spaces):
    c,app,_,_=spaces
    a=folder(c,'A').json(); b=folder(c,'B',a['id']).json()
    assert folder(c,'A',b['id'],a['id']).status_code==409
    assert c.delete('/api/folders/'+a['id']).status_code==409
    assert folder(c,'B',a['id']).status_code==409
    assert folder(c,'B renamed',None,b['id']).status_code==200
    assert c.delete('/api/folders/'+a['id']).status_code==200
    with TestClient(app,base_url='https://testserver') as other:
        assert login(other,'outsider').status_code==200
        assert folder(other,'stolen',None,b['id']).status_code==404
        assert folder(other,'child',b['id']).status_code==404
        assert other.delete('/api/folders/'+b['id']).status_code==404

def test_folder_depth_and_existing_subtree_move_limit(spaces):
    c,_,_,_=spaces
    root=folder(c,'root').json();parent=root
    for i in range(7): parent=folder(c,str(i),parent['id']).json()
    assert folder(c,'too deep',parent['id']).status_code==409
    a=folder(c,'another').json(); folder(c,'child',a['id'])
    assert folder(c,'another',parent['id'],a['id']).status_code==409

def test_private_copy_keeps_original_shared_scope_and_existing_bytes(spaces):
    c,app,_,_=spaces
    doc=fresh(c);path=f'/api/documents/{doc["id"]}'
    assert c.put(path+'/access',json={'user_id':'writer','role':'reader'}).status_code==200
    assert login(c,'writer').status_code==200
    copied=c.post(path+'/copies',json={'document_id':str(uuid.uuid4()),'source_version':doc['current_version'],'request_id':str(uuid.uuid4()),'name':'내 사본'}).json()
    assert login(c,'manager').status_code==200
    assert c.get('/api/documents/'+copied['id']).status_code==404
    assert c.get(path+'/versions/'+doc['current_version']+'/file').content==package('개인 계획')

def test_workspace_assignment_preserves_original_private_marker_without_granting_old_shares(spaces):
    c,_,_,_=spaces
    doc=fresh(c);path=f'/api/documents/{doc["id"]}'
    assert c.put(path+'/access',json={'user_id':'outsider','role':'editor'}).status_code==200
    team,_=new_space(c)
    assert c.put(path+'/workspace',json={'workspace_id':team['id'],'expected_version':doc['current_version']}).status_code==200
    assert login(c,'outsider').status_code==200
    assert c.get(path).status_code==404

def test_return_to_private_revokes_team_and_invites_but_retry_keeps_later_share(spaces):
    c,app,_,_=spaces
    team,_=new_space(c);add(c,team,'writer','editor');doc=scoped_doc(c,team);path=f'/api/documents/{doc["id"]}'
    assert c.put(path+'/access',json={'user_id':'outsider','role':'editor'}).status_code==200
    body={'workspace_id':None,'expected_version':doc['current_version']}
    assert c.put(path+'/workspace',json=body).status_code==200
    with TestClient(app,base_url='https://testserver') as other:
        for user in ['writer','outsider']:
            assert login(other,user).status_code==200
            assert other.get(path).status_code==404
    assert c.put(path+'/access',json={'user_id':'writer','role':'reader'}).status_code==200
    assert c.put(path+'/workspace',json=body).status_code==200
    assert any(x['user_id']=='writer' for x in c.get(path+'/access').json())
    assert len(c.get(path+'/versions').json())==1
