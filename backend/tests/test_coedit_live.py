import uuid
from app.db import transaction,Version
from sqlalchemy import select
from test_coedit import room,join,document


def live(c,path,token,version,**changes):
    body={'base_version':version,'sequence':1,'typing':False,**changes}
    return c.post(path+'/live',headers={'X-Coedit-Session':token},json=body)


def test_live_page_is_unsaved_scoped_and_cleared_on_leave(room):
    c,app,doc,path,_=room
    a,b=join(c,path),join(c,path)
    svg='<svg xmlns="http://www.w3.org/2000/svg"><text>작성 중인 실제 문구</text></svg>'
    first=live(c,path,a,doc['current_version'],typing=True,svg=svg,page=2)
    assert first.status_code==200,first.text
    peer=live(c,path,b,doc['current_version']).json()['peers'][0]
    assert peer['user_id']=='local' and peer['typing'] and peer['page']==2
    watched=live(c,path,b,doc['current_version'],sequence=2,watch=peer['id']).json()['preview']
    assert watched['svg']==svg and 'token' not in watched
    assert c.get(f"/api/documents/{doc['id']}").json()['current_version']==doc['current_version']
    with transaction(app.state.engine) as db: assert len(list(db.scalars(select(Version))))==1
    # A delayed old frame cannot replace a newer frame.
    assert live(c,path,a,doc['current_version'],sequence=2,typing=True,svg='<svg/>').status_code==200
    live(c,path,a,doc['current_version'],typing=True,svg=svg)
    assert live(c,path,b,doc['current_version'],sequence=3,watch=peer['id']).json()['preview']['svg']=='<svg/>'
    c.delete(path,headers={'X-Coedit-Session':a})
    assert live(c,path,b,doc['current_version'],sequence=4,watch=peer['id']).json()['preview'] is None
    assert live(c,path,a,doc['current_version']).status_code==409


def test_live_page_validation_scope_and_expiry(room,monkeypatch):
    import app.coedit as module
    c,_,doc,path,_=room;a,b=join(c,path),join(c,path)
    assert live(c,path,a,str(uuid.uuid4()),typing=True,svg='<svg/>').status_code==409
    assert live(c,path,'wrong',doc['current_version']).status_code==409
    assert live(c,path,a,doc['current_version'],svg='x'*600001).status_code==422
    assert live(c,path,a,doc['current_version'],user_id='spoof').status_code==422
    live(c,path,a,doc['current_version'],typing=True,svg='<svg/>')
    peer=live(c,path,b,doc['current_version']).json()['peers'][0]
    now=module.time.time();monkeypatch.setattr(module.time,'time',lambda:now+7)
    assert live(c,path,b,doc['current_version'],sequence=2,watch=peer['id']).json()['preview'] is None

def test_manual_conflict_can_be_preserved_as_private_revision_without_overwriting(room):
    from test_coedit import write,downloaded,text
    from app.document_branches import DocumentBranch
    c,app,doc,path,original=room
    a,b=join(c,path),join(c,path)
    accepted=write(c,path,a,doc['current_version'],document(a='다른 사람 반영')).json()['version_id']
    incoming=document(a='내 미반영 수정')
    assert write(c,path,b,doc['current_version'],incoming).status_code==409
    key=str(uuid.uuid4())
    def preserve(data=incoming):
        return c.post(path+'/revision',params={'base_version':doc['current_version'],'request_id':key},headers={'X-Coedit-Session':b},content=data)
    first=preserve();assert first.status_code==200,first.text
    branch=first.json()['document']
    assert preserve().json()['document']['id']==branch['id']
    assert preserve(document(a='바뀐 재시도')).status_code==409
    assert c.get(f"/api/documents/{doc['id']}").json()['current_version']==accepted
    assert downloaded(c,doc,doc['current_version'])==original
    assert text(downloaded(c,branch,branch['current_version']))=='내 미반영 수정|둘째 문단 원문'
    with transaction(app.state.engine) as db:
        rows=list(db.scalars(select(DocumentBranch)))
        assert len(rows)==1 and rows[0].base_version_id==doc['current_version']
    c.delete(path,headers={'X-Coedit-Session':b})
    assert preserve().status_code==409


def test_live_requested_pages_are_scoped_bounded_and_revision_fenced(room):
    c,_,doc,path,_=room;a,b=join(c,path),join(c,path);version=doc['current_version']
    live(c,path,a,version,draft=True,page=2,page_count=3,revision=7,svg='<svg>two</svg>')
    peer=live(c,path,b,version).json()['peers'][0]
    assert peer['page_count']==3
    request=live(c,path,b,version,sequence=2,watch=peer['id'],watch_page=1).json()
    assert request['preview'] is None
    response=live(c,path,a,version,sequence=2,draft=True,page=2,page_count=3,revision=7,svg='<svg>two</svg>').json()
    assert response['requested_pages']==[1]
    live(c,path,a,version,sequence=3,draft=True,page=2,page_count=3,rendered_page=1,revision=7,svg='<svg>one</svg>')
    preview=live(c,path,b,version,sequence=3,watch=peer['id'],watch_page=1).json()['preview']
    assert preview['page']==1 and preview['writing_page']==2 and preview['svg']=='<svg>one</svg>'
    assert 'frames' not in preview and 'watch' not in preview
    # A different document revision must never serve cached old pages.
    live(c,path,a,version,sequence=4,draft=True,page=2,page_count=3,revision=8,svg='<svg>new two</svg>')
    assert live(c,path,b,version,sequence=4,watch=peer['id'],watch_page=1).json()['preview'] is None
    assert live(c,path,b,version,sequence=5,watch=peer['id'],watch_page=2).json()['preview']['svg']=='<svg>new two</svg>'
    # All retained pages share the original 600k character budget.
    live(c,path,a,version,sequence=5,draft=True,page=2,page_count=3,rendered_page=1,revision=8,svg='a'*400000)
    live(c,path,a,version,sequence=6,draft=True,page=2,page_count=3,rendered_page=3,revision=8,svg='b'*400000)
    assert live(c,path,b,version,sequence=6,watch=peer['id'],watch_page=1).json()['preview'] is None
    assert live(c,path,b,version,sequence=7,watch=peer['id'],watch_page=3).json()['preview']['svg']=='b'*400000
    assert live(c,path,a,version,sequence=7,page_count=2,rendered_page=3).status_code==422
    live(c,path,b,version,sequence=8,watch=peer['id'],watch_page=999)
    assert live(c,path,a,version,sequence=8,page_count=3).json()['requested_pages']==[]
    c.delete(path,headers={'X-Coedit-Session':b})
    assert live(c,path,a,version,sequence=9,page_count=3).json()['requested_pages']==[]
